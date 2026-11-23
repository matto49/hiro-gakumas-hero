"""固定区域的数字和短文本 OCR。两个后端，接口相同：

- vision：Apple Vision（macOS 系统文字识别）。实测在作者机器上比 KAA 的 RapidOCR 快 2–7 倍且更准（见 README），
  macOS 上默认用它。区域先放大 3 倍再识别，小号描边数字更稳。
- rapidocr：RapidOCR（rapidocr_onnxruntime + PaddleOCR PP-OCRv3 日文 / 英文识别模型，见 _rapid_model；不经过 kotonebot）。非 macOS（模拟器 / DMM）用它；
  **识别率与速度没有在本项目的回归截图上核对过**，换平台后先用回归截图验一遍（见 docs/assets.md）。
- winocr：Windows 自带的文字识别（Windows.Media.Ocr，经 winrt 调用；要装了日文 OCR 语言，Windows 设置里加日语即可）。
  Windows 实测单块区域约 0.05 s。它按「词」返回结果，日文是一字一词：这里按间隙把一行拆成段、段内拼回文字（见 _split_words / _join_words）。

- ppocr5：rapidocr 包里的 PaddleOCR PP-OCRv5（中日英一个模型，onnxruntime），比 KAA 自带的 RapidOCR 模型新；
  Mac / Windows 结果一致。中日混合模型会把部分汉字认成简体（强化、交换済），按 _SIMPLIFIED 换回日文写法。

- simd：可选的本机 SIMD PaddleOCR PP-OCRv6 medium，同时供 Hiro 与 KAA 使用（安装见 docs/assets.md）。

HIRO_OCR=auto（缺省：macOS 且能导入 Vision → vision，否则 rapidocr）/ vision / rapidocr / winocr / ppocr5 / simd。
rapidocr 的推理设备：HIRO_OCR_DEVICE=auto（缺省 CUDA → DirectML → CPU）/ cuda / dml / cpu；CPU 线程数 HIRO_OCR_THREADS（见 ocr_device_order）。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import cv2
from cv2.typing import MatLike

logger = logging.getLogger(__name__)

ENGINES = ("auto", "vision", "rapidocr", "winocr", "ppocr5", "simd")
_engine: str | None = None


def _vision_available() -> bool:
    try:
        import Foundation  # noqa: F401
        import Vision  # noqa: F401
    except ImportError:
        return False
    return True


def engine() -> str:
    """当前 OCR 后端（首次调用时按 HIRO_OCR 和平台确定，之后不变）。"""
    global _engine
    if _engine is None:
        want = (os.environ.get("HIRO_OCR") or "auto").strip().lower()
        if want not in ENGINES:
            raise ValueError(f"HIRO_OCR={want!r} 不认识；可选：{', '.join(ENGINES)}")
        if want == "auto":
            want = "vision" if sys.platform == "darwin" and _vision_available() else "rapidocr"
        _engine = want
    return _engine


def _vision_raw(img: MatLike, w: int, h: int, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；框按归一化坐标换回原图尺寸 (w, h)。"""
    import Vision
    from Foundation import NSData

    _ok, buf = cv2.imencode(".png", img)
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(buf.tobytes(), len(buf)), None
    )
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(0)  # accurate
    request.setRecognitionLanguages_(list(languages))
    request.setUsesLanguageCorrection_(False)
    handler.performRequests_error_([request], None)
    out = []
    for obs in request.results() or []:
        cand = obs.topCandidates_(1)[0]
        bb = obs.boundingBox()  # 归一化坐标，原点在左下
        x1, y1 = bb.origin.x * w, (1 - bb.origin.y - bb.size.height) * h
        out.append(
            (
                str(cand.string()),
                float(cand.confidence()),
                (round(x1), round(y1), round(x1 + bb.size.width * w), round(y1 + bb.size.height * h)),
            )
        )
    return out


RAPID_DIR = Path(__file__).resolve().parents[3] / "cache" / "rapidocr"
RAPID_MODELS = {
    "ja": "japan_PP-OCRv3_rec_infer.onnx",
    "en": "en_PP-OCRv3_rec_infer.onnx",
}  # 识别模型；检测用 rapidocr_onnxruntime 自带的
RAPID_PAD = 631  # 小图居中补白到 631×631 再识别（PaddleOCR 模型图太小准确率低；与原先经 kotonebot 调用时一致）
_rapid: dict = {}


def _rapid_model(tag: str) -> Path:
    """识别模型路径：RAPID_DIR（cache/rapidocr）里有就用；没有就用已安装的 kaa 包里随带的同名文件
    （只按路径找数据文件，不 import kaa）。PaddleOCR PP-OCRv3 日文 / 英文模型，ModelScope 上没有 v3 的公开地址。"""
    import importlib.util

    name = RAPID_MODELS[tag]
    own = RAPID_DIR / name
    if own.exists():
        return own
    spec = importlib.util.find_spec("kaa")
    for d in (spec.submodule_search_locations or []) if spec else []:
        if (f := Path(d) / "res" / "models" / name).exists():
            return f
    raise RuntimeError(f"HIRO_OCR=rapidocr 要的模型 {name} 不在 {own.parent}，也没装 kaa 包")


OCR_DEVICES = ("cuda", "dml", "cpu")  # HIRO_OCR_DEVICE=auto 时按这个顺序试
_EP = {"cuda": "CUDAExecutionProvider", "dml": "DmlExecutionProvider", "cpu": "CPUExecutionProvider"}


def ocr_device_order(want: str | None = None, available: list[str] | None = None) -> list[str]:
    """RapidOCR 推理设备的尝试顺序。HIRO_OCR_DEVICE：auto（缺省：CUDA → DirectML → CPU，装了哪种 onnxruntime 就有哪种）/ cuda / dml / cpu。
    指定的设备这台机器的 onnxruntime 没有时退回 CPU（日志写明）。CUDA 要 onnxruntime-gpu 且 CUDA / cuDNN 版本对得上；
    DirectML 要 onnxruntime-directml（Windows，任意显卡）。三者互斥，同一个环境只能装一种。"""
    want = (want if want is not None else os.environ.get("HIRO_OCR_DEVICE", "auto")).strip().lower() or "auto"
    if available is None:
        import importlib

        # 按模块名导入：类型检查环境（CI 只装 .[dev]）没有 onnxruntime
        available = list(importlib.import_module("onnxruntime").get_available_providers())
    providers: list[str] = available
    if want not in ("auto", *OCR_DEVICES):
        raise ValueError(f"HIRO_OCR_DEVICE 只能是 auto / cuda / dml / cpu，不是 {want!r}")
    order = [d for d in (OCR_DEVICES if want == "auto" else (want,)) if _EP[d] in providers]
    if want not in ("auto", "cpu") and not order:
        logger.warning(
            "HIRO_OCR_DEVICE=%s，但 onnxruntime 没有 %s（现有 %s），文字识别用 CPU", want, _EP[want], providers
        )
    return order if "cpu" in order else [*order, "cpu"]


def _ocr_threads() -> int:
    """HIRO_OCR_THREADS：CPU 推理用几个线程（缺省 -1 = onnxruntime 自己定，通常占满所有核）。CPU 模式下调小能降占用和风扇声，单帧会慢一些。"""
    try:
        return int(os.environ.get("HIRO_OCR_THREADS") or -1)
    except ValueError:
        return -1


def _session_device(engine) -> str:
    """引擎实际在用的设备（看检测模型 session 的第一个 provider；onnxruntime 加载 CUDA / DirectML 失败时会悄悄退回 CPU）。"""
    try:
        first = engine.text_det.infer.session.get_providers()[0]
    except Exception:  # noqa: BLE001
        return "cpu"
    return next((d for d, ep in _EP.items() if ep == first), "cpu")


def _rapid_engine(languages):
    from rapidocr_onnxruntime import RapidOCR

    tag = "en" if tuple(languages) == ("en-US",) else "ja"  # 只要英文时用英文模型（数字），否则日文模型
    if tag not in _rapid:
        threads = _ocr_threads()
        engine = None
        for dev in ocr_device_order():
            flags = {
                f"{part}_{k}": dev == d
                for part in ("det", "rec")
                for k, d in (("use_cuda", "cuda"), ("use_dml", "dml"))
            }
            engine = RapidOCR(rec_model_path=str(_rapid_model(tag)), use_det=True, use_cls=False, use_rec=True,
                              det_intra_op_num_threads=threads, rec_intra_op_num_threads=threads, **flags)  # fmt: skip
            got = _session_device(engine)
            if got == dev:
                break
            logger.warning("文字识别：%s 没加载成功（实际 %s），试下一个", dev, got)
        logger.info("文字识别（RapidOCR %s 模型）用 %s", tag, _session_device(engine))
        _rapid[tag] = engine
    return _rapid[tag]


def _pad_to(img: MatLike, size: int) -> tuple[MatLike, int, int]:
    """居中补白到至少 size×size，返回 (新图, x 偏移, y 偏移)。"""
    h, w = img.shape[:2]
    ph, pw = max(0, size - h), max(0, size - w)
    if not ph and not pw:
        return img, 0, 0
    top, left = ph // 2, pw // 2
    return (
        cv2.copyMakeBorder(img, top, ph - top, left, pw - left, cv2.BORDER_CONSTANT, value=(255, 255, 255)),
        left,
        top,
    )


def _rapid_raw(img: MatLike, scale: float, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；四点框取外接矩形、减去补白偏移、除以 scale 换回原图坐标；文字做 sanitize（全角转半角）。"""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    padded, ox, oy = _pad_to(img, RAPID_PAD)
    res, _ = _rapid_engine(languages)(padded)
    out = []
    for box, text, conf in res or []:
        xs, ys = [int(p[0]) - ox for p in box], [int(p[1]) - oy for p in box]
        out.append(
            (
                sanitize(text),
                float(conf),
                (round(min(xs) / scale), round(min(ys) / scale), round(max(xs) / scale), round(max(ys) / scale)),
            )
        )
    return out


_win: dict = {}


def _win_engine(languages):
    """按语言缓存 OcrEngine（只要英文时用 en-US，否则 ja）；事件循环也缓存，recognize_async 在里面同步跑完。"""
    import asyncio

    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    tag = "en-US" if tuple(languages) == ("en-US",) else "ja"
    if tag not in _win:
        eng = OcrEngine.try_create_from_language(Language(tag))
        if eng is None:
            raise RuntimeError(f"Windows OCR 没有 {tag} 语言：在 Windows 设置 → 时间和语言 → 语言里添加日语（含 OCR）")
        _win[tag] = eng
    if "loop" not in _win:
        _win["loop"] = asyncio.new_event_loop()
    return _win[tag], _win["loop"]


def _is_ascii_word(t: str) -> bool:
    return bool(t) and t.isascii() and any(c.isalnum() for c in t)


def _split_words(words: list[tuple[str, tuple[float, float, float, float]]]) -> list[list[tuple]]:
    """Windows OCR 会把同一水平线上相隔很远的几段字并成一行（货架一排价格「70 49 105 35」），Apple Vision 是分开的。
    间隙大于字高的 1.2 倍就断成新的一段，各自出框。"""
    segs: list[list[tuple]] = []
    for wd in words:
        x, _, _, h = wd[1]
        if segs:
            px, _, pw, ph = segs[-1][-1][1]
            if x - (px + pw) <= 1.2 * max(h, ph):
                segs[-1].append(wd)
                continue
        segs.append([wd])
    return segs


def _join_words(words: list[tuple[str, tuple[float, float, float, float]]]) -> str:
    """一段里的词拼成文字：日文一字一词、字间不留空格；只有两边都是英文 / 数字且确实隔开（间隙 ≥ 字高 0.35 倍）才留一个空格。"""
    out = ""
    prev = None
    for text, (x, _, w, h) in words:
        if prev is not None:
            ptext, px, pw, ph = prev
            gap = x - (px + pw)
            if _is_ascii_word(ptext) and _is_ascii_word(text[:1]) and gap >= 0.35 * max(h, ph):
                out += " "
        out += text
        prev = (text, x, w, h)
    return out


def _win_raw(img: MatLike, scale: float, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；框除以 scale 换回原图坐标。Windows OCR 不给置信度，统一记 1.0。"""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    eng, loop = _win_engine(languages)
    bgra = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA if img.ndim == 2 else cv2.COLOR_BGR2BGRA)
    h, w = bgra.shape[:2]
    if min(h, w) < 40:  # 太小的图 Windows OCR 读不出，向右下补到 40（不影响原坐标）
        bgra = cv2.copyMakeBorder(bgra, 0, max(0, 40 - h), 0, max(0, 40 - w), cv2.BORDER_REPLICATE)
        h, w = bgra.shape[:2]
    dw = DataWriter()
    dw.write_bytes(bgra.tobytes())
    bmp = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    res = loop.run_until_complete(eng.recognize_async(bmp))
    out = []
    for line in res.lines:
        ws = [
            (wd.text, (wd.bounding_rect.x, wd.bounding_rect.y, wd.bounding_rect.width, wd.bounding_rect.height))
            for wd in line.words
        ]
        if not ws:
            continue
        ws.sort(key=lambda t: t[1][0])
        for seg in _split_words(ws):
            x1 = min(b[0] for _, b in seg)
            y1 = min(b[1] for _, b in seg)
            x2 = max(b[0] + b[2] for _, b in seg)
            y2 = max(b[1] + b[3] for _, b in seg)
            out.append(
                (_join_words(seg), 1.0, (round(x1 / scale), round(y1 / scale), round(x2 / scale), round(y2 / scale)))
            )
    return out


# 只在简体中文里出现、日文不用的字 → 日文写法（PP-OCRv5 中日混合模型的读法；两边写法相同的字不收）
_SIMPLIFIED = str.maketrans(
    dict(
        zip(
            "强换查选择认识确战试验费结气动习课讲获领张级时间开关闭门问题说话读买卖价钱饮剧场进还对应实际现发处务员东车运转轮输边过达连补态优胜败负记录设计让训练绩续继绝经给线红约纪细终组织编缘网罗义书页顶项顺须预频颜风飞饰马驱鱼鸟乐兴举产亲亿传伤众华单卫历压变叶听启园围图圣坏块报备头夺奋宽导层岁岛带广库废异弹归忆怀总恶惊惯戏户扩扫护损据挥摄敌无显晓暂术机杂权极构标样树桥检欢测济满灵烧热爱猎环电疗盘盖监码础种积稳竞笔简类紧纯纸绘统维综绿缓缩罚职联脑艺节范药营虑见观规视览觉议许论访证评诉词译诗该详语误请诸谁调谈谢谱贝贡财责贤货质贩贯贵贴贸资赏赖赞赠跃践轻载较辅辈迁远违适递遗邮邻释钟钢银锁锐错键镜长闪闻阳阶阵陆险隐难额馆骑鲜鸣",
            "強換査選択認識確戦試験費結気動習課講獲領張級時間開関閉門問題説話読買売価銭飲劇場進還対応実際現発処務員東車運転輪輸辺過達連補態優勝敗負記録設計譲訓練績続継絶経給線紅約紀細終組織編縁網羅義書頁頂項順須預頻顔風飛飾馬駆魚鳥楽興挙産親億伝傷衆華単衛歴圧変葉聴啓園囲図聖壊塊報備頭奪奮寛導層歳島帯広庫廃異弾帰憶懐総悪驚慣戯戸拡掃護損拠揮摂敵無顕暁暫術機雑権極構標様樹橋検歓測済満霊焼熱愛猟環電療盤蓋監碼礎種積穏競筆簡類緊純紙絵統維総緑緩縮罰職聯脳芸節範薬営慮見観規視覧覚議許論訪証評訴詞訳詩該詳語誤請諸誰調談謝譜貝貢財責賢貨質販貫貴貼貿資賞頼賛贈躍践軽載較輔輩遷遠違適逓遺郵隣釈鐘鋼銀鎖鋭錯鍵鏡長閃聞陽階陣陸険隠難額館騎鮮鳴",
            strict=True,
        )
    )
)
_pp: dict = {}


PP_DIR_DEFAULT = Path(__file__).resolve().parents[3] / "cache" / "ppocr5"


def _pp_engine(languages):
    """PP-OCRv5 mobile（检测 + 中日英识别）。模型从 HIRO_PPOCR_DIR（缺省 cache/ppocr5）加载：KAA 把 onnxruntime 固定在 1.14
    （Windows DLL 问题），只认 IR ≤ 8，rapidocr 自带的 v5 模型是 IR 10，要先用 tools/assets/ppocr_models.py 转一份。
    数字也用同一个模型（英文模型读本项目的小号描边数字没有更好），languages 不再区分。"""
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

    if "eng" not in _pp:
        d = Path(os.environ.get("HIRO_PPOCR_DIR") or PP_DIR_DEFAULT)
        det, rec = d / "ch_PP-OCRv5_det_mobile.onnx", d / "ch_PP-OCRv5_rec_mobile.onnx"
        if not det.exists() or not rec.exists():
            raise RuntimeError(f"HIRO_OCR=ppocr5 要的模型不在 {d}：先跑 python tools/assets/ppocr_models.py")
        _pp["eng"] = RapidOCR(params={
            "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE, "Det.model_path": str(det),
            "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE, "Rec.model_path": str(rec),
            "Rec.lang_type": LangRec.CH, "Global.log_level": "error",
        })  # fmt: skip
    return _pp["eng"]


def _pp_raw(img: MatLike, scale: float, languages) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """img 已放大；四点框取外接矩形、除以 scale 换回原图坐标；简体字换回日文写法。"""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    res = _pp_engine(languages)(img)
    if res.boxes is None or res.txts is None:
        return []
    out = []
    for box, text, conf in zip(res.boxes, res.txts, res.scores, strict=False):
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append(
            (
                str(text).translate(_SIMPLIFIED),
                float(conf),
                (round(min(xs) / scale), round(min(ys) / scale), round(max(xs) / scale), round(max(ys) / scale)),
            )
        )
    return out


def ocr_boxes(
    img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")
) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """每段文字、置信度和它在 img 中的外接框 (x1, y1, x2, y2)。空图（裁剪框落到画面外）返回 []，不抛异常（Mac h8 11-15：
    弹窗标题条贴着画面底边，正文裁出空图，cv2.resize 报 !ssize.empty()，主循环崩溃）。"""
    if img is None or getattr(img, "size", 0) == 0 or min(img.shape[:2]) < 1:
        return []
    eng = engine()
    if eng == "simd":
        from hiro.vision.simd_ocr import simd_engine

        return simd_engine().read(img, scale=scale)
    h, w = img.shape[:2]
    if scale != 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if eng == "vision":
        return _vision_raw(img, w, h, languages)
    if eng == "winocr":
        return _win_raw(img, scale, languages)
    if eng == "ppocr5":
        return _pp_raw(img, scale, languages)
    return _rapid_raw(img, scale, languages)


def ocr(img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> list[tuple[str, float]]:
    return [(t, c) for t, c, _ in ocr_boxes(img, scale=scale, languages=languages)]


def ocr_line(img: MatLike, *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> str:
    """已知只有一行字的框（比如 HUD 上固定位置的数字）：SIMD 后端跳过文字检测、只跑识别。"""
    if img is None or not img.size:
        return ""
    if engine() == "simd":
        from hiro.vision.simd_ocr import simd_engine

        return "".join(t for t, _, _ in simd_engine().read(img, scale=scale, only_rec=True))
    return "".join(t for t, _ in ocr(img, scale=scale, languages=languages))


# ---------------------------------------------------------------- 整帧 / 区域文字（页面判定、找按钮）

_CHAR_FIX = {"ó": "6", "ą": "a"}  # RapidOCR 日文模型的常见误读


def sanitize(text: str) -> str:
    """NFKC 规整（全角英数 / 括号转半角）+ 常见误读修正。整帧文字都过一遍，匹配时不用再管全角半角。"""
    text = unicodedata.normalize("NFKC", text)
    for k, v in _CHAR_FIX.items():
        text = text.replace(k, v)
    return text


class Point(NamedTuple):
    x: int
    y: int


class Box(NamedTuple):
    """整帧坐标的外接框 (x1, y1, x2, y2)。点它用 device.click(*box.center)。"""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def w(self) -> int:
        return self.x2 - self.x1

    @property
    def h(self) -> int:
        return self.y2 - self.y1

    @property
    def center(self) -> Point:
        return Point(self.x1 + self.w // 2, self.y1 + self.h // 2)

    @property
    def xywh(self) -> tuple[int, int, int, int]:
        return self.x1, self.y1, self.w, self.h

    @property
    def top_left(self) -> Point:
        return Point(self.x1, self.y1)


class Text(NamedTuple):
    """一段识别出的文字：.text、.rect（Box，整帧坐标）、.conf。"""

    text: str
    rect: Box
    conf: float = 1.0


SCREEN_SCALE = 1.5  # 整帧放大倍数（Vision 整帧约 0.5s；3 倍太慢，1 倍小字读不全）


def _xywh(rect) -> tuple[int, int, int, int]:
    return tuple(int(v) for v in (rect.xywh if hasattr(rect, "xywh") else rect))  # type: ignore[return-value]


def screen_texts(img: MatLike | None, rect=None, *, scale: float = SCREEN_SCALE) -> list[Text]:
    """img 整帧（或 rect=(x, y, w, h) / 带 .xywh 的区域）里的文字，坐标是整帧坐标，自上而下、同行从左到右。
    img 为 None（测试替身）时返回空；识别出错由调用方处理。"""
    if img is None:
        return []
    ox = oy = 0
    if rect is not None:
        ox, oy, w, h = _xywh(rect)
        img = img[oy : oy + h, ox : ox + w]
    out = [Text(sanitize(t), Box(ox + x1, oy + y1, ox + max(x2, x1 + 1), oy + max(y2, y1 + 1)), c)
           for t, c, (x1, y1, x2, y2) in ocr_boxes(img, scale=scale)]  # fmt: skip
    return sorted(out, key=lambda b: (b.rect.center.y // 12, b.rect.center.x))


def find_text(img: MatLike | None, match: str | Callable[[str], bool], rect=None) -> Text | None:
    """screen_texts 里第一段匹配的文字：match 是字符串时要求包含它，是函数时按函数判。"""
    pred = (lambda t: match in t) if isinstance(match, str) else match
    return next((t for t in screen_texts(img, rect) if pred(t.text)), None)


def digits_image(img: MatLike, *, white_min: int = 200) -> MatLike:
    """白色描边数字 → 白底黑字：取接近白色的像素作为字形，加边框，便于识别小号数字。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    _, mask = cv2.threshold(gray, white_min, 255, cv2.THRESH_BINARY)
    inv = 255 - mask
    return cv2.copyMakeBorder(inv, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)


def ocr_digits(img: MatLike, *, white_min: int = 200, scale: float = 3.0) -> int | None:
    pre = digits_image(img, white_min=white_min)
    text = " ".join(t for t, _ in ocr(cv2.cvtColor(pre, cv2.COLOR_GRAY2BGR), scale=scale, languages=("en-US",)))
    text = text.replace(",", "").replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def ocr_text(img: MatLike, **kw) -> str:
    return " ".join(t for t, _ in ocr(img, **kw))


def ocr_int(img: MatLike, **kw) -> int | None:
    text = ocr_text(img, **kw).replace(",", "").replace("O", "0").replace("o", "0")
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def ocr_batch(crops: list[MatLike], *, scale: float = 3.0, languages=("ja-JP", "en-US")) -> list[str]:
    """多块区域各自 OCR，返回每块按 Vision 结果顺序拼接的文字。

    试过把多块竖向拼成一张图只调一次 Vision：快约一半，但精度明显下降（突破线「64」读成「9A」、卡名的「+」
    读成「-十」，换放大倍数和间隔都不稳），线程并发也不会更快（Vision 内部串行）。所以仍逐块调用，
    省时间靠调用方的区域缓存（hiro.vision.region_cache）跳过没变的区域。"""
    return ["".join(t for t, _ in ocr(c, scale=scale, languages=languages)) for c in crops]
