"""实验后端 rapidocr+charset：PP-OCRv3 日文识别在 CTC 解码前把游戏字表以外的字屏蔽掉（规格 §6.5）。

只在评测工具里用，不改实机代码。检测模型、放大倍数、补白与 hiro.vision.ocr 的 rapidocr 后端相同，只换识别后处理；
用自己的 RapidOCR 实例，不影响同一进程里别的后端。字表只从主数据和代码来（tools.ocr_dataset.lexicon），不从标注文字来。
"""

from __future__ import annotations

import string
import unicodedata

import cv2
import numpy as np

# 字表之外也放行的符号（数值、效果文里常见）；全角写法 NFKC 后落在 ASCII 的也一并放行
PUNCT = set(string.punctuation) | set("％＋×・〜～「」『』（）【】！？、。，．：；ー－―…♪★☆→←↑↓／・゛゜")


def allowed_mask(character: list[str], charset: set[str]) -> np.ndarray:
    """识别字典每一列能不能出：blank（第 0 列）、空格、字表里的字（含 NFKC 后在字表里的全角写法）、PUNCT。"""
    out = np.zeros(len(character), np.float32)
    for i, ch in enumerate(character):
        if i == 0 or ch == " " or ch in charset or ch in PUNCT:
            out[i] = 1
            continue
        n = unicodedata.normalize("NFKC", ch)
        if n and all(c in charset or c in PUNCT for c in n):
            out[i] = 1
    return out


class MaskedDecode:
    """包住 rapidocr 的 CTCLabelDecode：概率矩阵乘上掩码再解码，表外字的概率归零，这一步改出表内概率最高的字。"""

    def __init__(self, inner, charset: set[str]):
        self.inner = inner
        self.character = inner.character
        self.mask = allowed_mask(inner.character, charset)

    def __call__(self, preds: np.ndarray, *args, **kw):
        return self.inner(preds * self.mask[: preds.shape[-1]], *args, **kw)


_engine: dict = {}


def _engine_for(charset: set[str]):
    key = id(charset)
    if key not in _engine:
        from hiro.vision.ocr import _rapid_model

        from rapidocr_onnxruntime import RapidOCR

        eng = RapidOCR(rec_model_path=str(_rapid_model("ja")), use_det=True, use_cls=False, use_rec=True)
        eng.text_rec.postprocess_op = MaskedDecode(eng.text_rec.postprocess_op, charset)
        _engine.clear()
        _engine[key] = eng
    return _engine[key]


def screen_texts_masked(img, charset: set[str]) -> list[tuple[str, tuple[int, int, int, int]]]:
    """整帧文字 [(文字, (x1, y1, x2, y2))]，坐标是整帧坐标；流程同 hiro.vision.ocr.screen_texts 走 rapidocr 时。"""
    from hiro.vision.ocr import RAPID_PAD, SCREEN_SCALE, _pad_to, sanitize

    s = SCREEN_SCALE
    big = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    padded, ox, oy = _pad_to(big, RAPID_PAD)
    res, _ = _engine_for(charset)(padded)
    out = []
    for box, text, _conf in res or []:
        xs, ys = [int(p[0]) - ox for p in box], [int(p[1]) - oy for p in box]
        out.append((sanitize(text), (round(min(xs) / s), round(min(ys) / s), round(max(xs) / s), round(max(ys) / s))))
    return out
