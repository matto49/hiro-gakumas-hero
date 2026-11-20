"""卡面图库（选卡弹窗、相談货架、牌组卡格认卡）：和 KAA image_db 的 skill_cards_dialog 同一套做法，不依赖 KAA。

图：AssetSource.game_data("skill_cards")（KAA 公开数据 skill_cards/，按 IMREAD_COLOR 读，同 KAA）。描述子：OpenCV SIFT，nfeatures=500，
其余参数缺省（同 KAA SiftDescriptor(nfeatures=500)）。检索：所有卡的描述子（约 47 万个）放进 faiss IVFFlat，
nlist = 1024、nprobe = 16。KAA 用 IVFFlat、nprobe = nlist = 100，等于全量搜索，每张卡要 1.4 秒（换卡弹窗一屏 12 张 20 多秒）；
这里只搜最近的 16 个簇，每张约 40 ms，fixtures 上 60 张卡和全量搜索结果全一致（作者本机，未发布）。
描述子少于 IVF_MIN 个（测试用的小图库）时用精确索引。查询：截图每个描述子找 1 个最近邻，距离（L2 平方）≤ GOOD_MATCH_MAX_DIST
的给那张图投一票；按「票数多、其次最小距离小」排序（同 KAA ImageDatabase.query）。match：同一张卡的角色专属图（-hmsz 等后缀）
合并计票，最高 < MIN_VOTES 票不认（同 KAA match_card_region）。

描述子第一次用时现算（约两千张，几十秒），存到 cache/card_sift.npz，图库目录里的文件变了就重算。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

NFEATURES = 500
GOOD_MATCH_MAX_DIST = 20000.0
MIN_VOTES = 3
NLIST, NPROBE = 1024, 16
IVF_MIN = NLIST * 39  # faiss 训练每个簇至少要 39 个点
CACHE = Path(__file__).resolve().parents[3] / "cache" / "card_sift.npz"


@dataclass(frozen=True)
class Hit:
    key: str  # 图文件名去掉 .png（asset id）
    votes: int
    distance: float  # 最近的一次匹配距离（L2 平方）


def _card_dir() -> Path | None:
    from hiro import plugins

    src = plugins.get("asset_source")
    return src.game_data("skill_cards") if src is not None and hasattr(src, "game_data") else None


def _sift():
    return cv2.SIFT_create(nfeatures=NFEATURES)


def descriptors(img: np.ndarray) -> np.ndarray:
    _, d = _sift().detectAndCompute(img, None)
    return np.empty((0, 128), np.float32) if d is None else d.astype(np.float32)


def _signature(files: list[Path]) -> str:
    h = hashlib.sha1()
    for f in files:
        st = f.stat()
        h.update(f"{f.name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()


@cache
def _db() -> tuple[tuple[str, ...], np.ndarray, object]:
    """(keys, 每个描述子属于第几张图, faiss 索引)。"""
    import faiss

    d = _card_dir()
    files = sorted(d.glob("*.png")) if d is not None and d.is_dir() else []
    sig = _signature(files)
    if CACHE.exists():
        z = np.load(CACHE, allow_pickle=False)
        if str(z["sig"]) == sig:
            keys, owner, vecs = tuple(z["keys"].tolist()), z["owner"], z["vecs"]
        else:
            keys = ()
    else:
        keys = ()
    if not keys and files:
        logger.info("卡面图库：给 %d 张卡图算 SIFT 描述子（只算一次，存 %s）", len(files), CACHE)
        ks, owners, chunks = [], [], []
        for f in files:
            dsc = descriptors(cv2.imread(str(f), cv2.IMREAD_COLOR))
            if len(dsc):
                owners.append(np.full(len(dsc), len(ks), np.int32))
                chunks.append(dsc)
                ks.append(f.name.removesuffix(".png"))
        keys, owner, vecs = tuple(ks), np.concatenate(owners), np.concatenate(chunks)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        np.savez(CACHE, sig=sig, keys=np.array(keys), owner=owner, vecs=vecs)
    if not keys:
        return (), np.zeros(0, np.int32), None
    vecs = np.ascontiguousarray(vecs, dtype=np.float32)
    if len(vecs) < IVF_MIN:
        index = faiss.IndexFlatL2(128)
    else:
        index = faiss.IndexIVFFlat(faiss.IndexFlatL2(128), 128, NLIST)
        sample = vecs[np.random.default_rng(0).choice(len(vecs), min(len(vecs), NLIST * 100), replace=False)]
        index.train(sample)  # 固定抽样，每次建出来的簇一样（约 6 秒，进程内只建一次）
        index.nprobe = NPROBE
    index.add(vecs)
    return keys, owner, index


_MEMO: dict[bytes, list[Hit]] = {}
MEMO_SIZE = 512  # 轮询时同一屏反复读：同样的卡区像素直接用上次的结果


def query(img: np.ndarray) -> list[Hit]:
    """全部有票的图，按票数降序、最小距离升序（同 KAA ImageDatabase.query 的局部描述子分支）。"""
    key = hashlib.blake2b(np.ascontiguousarray(img).tobytes() + repr(img.shape).encode(), digest_size=16).digest()
    if (hit := _MEMO.get(key)) is not None:
        return list(hit)
    hits = _query(img)
    if len(_MEMO) >= MEMO_SIZE:
        _MEMO.pop(next(iter(_MEMO)))
    _MEMO[key] = hits
    return list(hits)


def _query(img: np.ndarray) -> list[Hit]:
    keys, owner, index = _db()
    q = descriptors(img)
    if index is None or not len(q):
        return []
    dist, lab = index.search(q, 1)
    votes: dict[int, int] = {}
    best: dict[int, float] = {}
    for dd, ll in zip(dist[:, 0], lab[:, 0]):
        if ll < 0 or float(dd) > GOOD_MATCH_MAX_DIST:
            continue
        k = int(owner[ll])
        votes[k] = votes.get(k, 0) + 1
        best[k] = min(best.get(k, float("inf")), float(dd))
    hits = [Hit(keys[k], v, best[k]) for k, v in votes.items()]
    return sorted(hits, key=lambda h: (-h.votes, h.distance))


def match(img: np.ndarray, *, min_votes: int = MIN_VOTES) -> str | None:
    """卡区截图 → base asset id（角色专属图合并计票，同 KAA match_card_region）；最高票 < min_votes 返回 None。"""
    from hiro.vision.deck import base_asset

    votes: dict[str, int] = {}
    best: dict[str, float] = {}
    for h in query(img):
        a = base_asset(h.key)
        votes[a] = votes.get(a, 0) + h.votes
        best[a] = min(best.get(a, float("inf")), h.distance)
    if not votes:
        return None
    top = min(votes, key=lambda a: (-votes[a], best[a]))
    return top if votes[top] >= min_votes else None
