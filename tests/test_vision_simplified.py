"""A16（11-26）：macOS 27.2.1 的 Apple Vision 把「審査基準」读成简体「審查基準」，初的考前说明页判成 unknown。
Vision 的输出和 PP-OCR 一样按 _SIMPLIFIED 换回日文写法；日文本来就有的字不动。"""

from hiro.vision import ocr


def test_simplified_table_folds_vision_reads():
    assert "審查基準".translate(ocr._VISION_SIMPLIFIED) == "審査基準"
    assert "选择".translate(ocr._VISION_SIMPLIFIED) == "選択"
    for ja in (
        "審査基準",
        "合格条件",
        "タップして次へ",
        "3位以上",
        "もしくはスコア1700以上",
        "Pドリンク",
        "夢が叶う",
        "机",
        "据え置き",
    ):
        assert ja.translate(ocr._VISION_SIMPLIFIED) == ja


def test_vision_raw_applies_table(monkeypatch):
    import sys
    import types

    import numpy as np

    class Cand:
        def string(self):
            return "審查基準"

        def confidence(self):
            return 1.0

    class Box:
        origin = types.SimpleNamespace(x=0.1, y=0.8)
        size = types.SimpleNamespace(width=0.2, height=0.05)

    class Obs:
        def topCandidates_(self, n):
            return [Cand()]

        def boundingBox(self):
            return Box()

    class Req:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def setRecognitionLevel_(self, v):
            pass

        def setRecognitionLanguages_(self, v):
            pass

        def setUsesLanguageCorrection_(self, v):
            pass

        def results(self):
            return [Obs()]

    class Handler:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithData_options_(self, data, opts):
            return self

        def performRequests_error_(self, reqs, err):
            return True, None

    vision = types.SimpleNamespace(VNImageRequestHandler=Handler, VNRecognizeTextRequest=Req)
    foundation = types.SimpleNamespace(NSData=types.SimpleNamespace(dataWithBytes_length_=lambda b, n: b))
    monkeypatch.setitem(sys.modules, "Vision", vision)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    out = ocr._vision_raw(np.zeros((40, 40, 3), np.uint8), 720, 1280, ("ja-JP",))
    assert out[0][0] == "審査基準"


def test_briefing_rows_tolerate_vision_glue(monkeypatch):
    """考前说明页：Vision 把图标粘在属性值前（「县879」）、评价标记连进倍率框（「1112%◎」）、
    「>>」框进倍率框（「》1868%」）、整行一个框（「（207623029%◎すばらしい！」）。真值取自私有帧 briefing-2 / q17-427。"""
    import hiro.vision.digits
    from hiro.vision import pages

    import numpy as np

    reads = {930: 1868, 1010: 30290, 1090: 1112}  # ddddocr：倍率列固定位置 / 单框各读一次；「%」偶尔读成 0

    def fake_dddd(img, box):
        cy = (box[1] + box[3]) // 2
        return next(v for y, v in reads.items() if abs(cy - y) <= 30)

    monkeypatch.setattr(hiro.vision.digits, "read_dddd", fake_dddd)
    boxes = [
        ("县879", (82, 931, 192, 963)),
        ("》1868%", (223, 920, 460, 977)),
        ("◎", (471, 925, 517, 971)),
        ("（207623029%◎すばらしい！", (69, 1008, 651, 1056)),
        ("471", (115, 1090, 192, 1124)),
        ("1112%◎", (305, 1084, 514, 1134)),
    ]
    r = pages.read_exam_briefing(np.zeros((1280, 720, 3), np.uint8), boxes)
    assert r["params"] == {"Vo": 879, "Da": None, "Vi": 471}  # 整行一个框时属性值不在 x<230 的独立框里，不猜
    assert r["multiplier_pct"] == {"Vo": 1868, "Da": 3029, "Vi": 1112}


def test_cut_box_keeps_digits_and_percent():
    from hiro.vision.pages import _cut_box

    assert _cut_box("1112%◎", 5, (305, 1084, 514, 1134)) == (305, 1084, 479, 1134)
    assert _cut_box("1413%", 5, (302, 918, 459, 976)) == (302, 918, 459, 976)
