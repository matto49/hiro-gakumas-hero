"""设备后端与 OCR 后端：与 KAA 支持的平台对齐（HIRO_DEVICE / HIRO_OCR），以及非 macOS 下能导入。"""

import os
import subprocess
import sys
from pathlib import Path

from hiro import device
from hiro.vision import ocr

import numpy as np
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


# ---------------------------------------------------------------- 设备后端
def test_auto_on_mac_is_maatools():
    assert device.resolve_backend("auto", platform="darwin") == "maatools"


def test_original_playcover_quartz_is_no_longer_supported(monkeypatch):
    monkeypatch.setenv("HIRO_DEVICE", "quartz")
    with pytest.raises(ValueError, match="可选"):
        device.requested_backend()


def test_maatools_unreachable_says_hguandl_playcover(monkeypatch):
    pytest.importorskip("kotonebot")
    import socket

    from hiro.devices import maatools

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # 没人在听的端口
    monkeypatch.setattr(device, "MaaToolsImpl", lambda dev: maatools.MaaToolsImpl(dev, port))
    with pytest.raises(RuntimeError, match="hguandl"):
        device.connect("maatools")


def test_auto_elsewhere_requires_explicit_device():
    with pytest.raises(RuntimeError, match="HIRO_DEVICE"):
        device.resolve_backend("auto", platform="win32")


def test_explicit_backend_passes_through(monkeypatch):
    for name in ("maatools", "adb", "mumu", "leidian", "dmm"):
        monkeypatch.setenv("HIRO_DEVICE", name)
        assert device.resolve_backend() == name


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("HIRO_DEVICE", "bluestacks")
    with pytest.raises(ValueError, match="可选"):
        device.requested_backend()


def test_playcover_only_fallbacks(monkeypatch):
    monkeypatch.setattr(device, "BACKEND", None)
    monkeypatch.setenv("HIRO_DEVICE", "mumu")
    assert device.is_playcover() is False
    monkeypatch.setenv("HIRO_DEVICE", "maatools")
    assert device.is_playcover() is True
    monkeypatch.setattr(device, "BACKEND", "adb")  # connect 之后以实际后端为准
    assert device.is_playcover() is False


def test_landscape_guard_inactive_never_toggles_fullscreen():
    from hiro.loop.landscape import LandscapeGuard

    g = LandscapeGuard(active=False)
    black = np.zeros((1280, 720, 3), np.uint8)
    g.black_since = -1e9  # 早已黑屏很久
    assert g.tick(black) is False
    assert g.in_live(black) is True  # 判断 LIVE 照常（调用方据此不按「认不出」停机）


# ---------------------------------------------------------------- KAA 后端配置
kaa_env = pytest.importorskip("hiro.kaa_env")


@pytest.fixture
def kaa_config():
    pytest.importorskip("kaa.config.schema")
    from kaa.config.schema import KaaConfig

    return KaaConfig


def test_playcover_keeps_kaa_default(kaa_config):
    for name in ("auto", "maatools"):
        assert kaa_env.kaa_backend(name) is None


@pytest.mark.parametrize(
    ("name", "platform", "kind", "shot"),
    [
        ("adb", "darwin", "custom", "adb"),
        ("leidian", "win32", "leidian", "adb"),
        ("mumu", "win32", "mumu12v5", "adb"),  # nemu_ipc 点击送不到应用显示（GKMS 第一–三局）
        ("mumu", "darwin", "mumu12v5", "adb"),
        ("dmm", "win32", "dmm", "windows_background"),
    ],
)
def test_kaa_backend_defaults_pass_kaa_validation(kaa_config, name, platform, kind, shot):
    from kaa.config.validation import validate_profile_config

    b = kaa_env.kaa_backend(name, platform=platform)
    assert (b.lifecycle.type, b.screenshot_impl) == (kind, shot)
    cfg = kaa_config()
    cfg.backend = b
    assert [i.message for i in validate_profile_config(cfg) if i.severity == "error"] == []


def test_kaa_backend_adb_address_and_instance(kaa_config):
    b = kaa_env.kaa_backend("adb", adb="127.0.0.1:16384")
    assert (b.connection.type, b.connection.ip, b.connection.port) == ("tcp", "127.0.0.1", 16384)
    m = kaa_env.kaa_backend("mumu", instance="2", platform="win32")
    assert m.connection.type == "auto" and m.lifecycle.instance_id == "2"
    d = kaa_env.kaa_backend("mumu", platform="win32")
    assert d.lifecycle.instance_id == "0"  # 单开缺省 0（KAA 不设会报错）
    assert d.lifecycle.mumu_background_mode is True  # MuMu 6 不开后台模式，nemu_ipc 截到的是桌面
    assert (
        kaa_env.kaa_backend("mumu", platform="win32", screenshot="nemu_ipc").screenshot_impl == "nemu_ipc"
    )  # 显式指定仍可用
    assert kaa_env.kaa_backend("mumu", platform="win32", mumu_background=False).lifecycle.mumu_background_mode is False
    with pytest.raises(ValueError, match="ip:port"):
        kaa_env.kaa_backend("adb", adb="127.0.0.1")


def test_bad_screenshot_combo_is_caught_by_kaa_validation(kaa_config):
    from kaa.config.validation import validate_profile_config

    cfg = kaa_config()
    cfg.backend = kaa_env.kaa_backend("dmm", screenshot="adb")
    assert any(i.severity == "error" for i in validate_profile_config(cfg))


# ---------------------------------------------------------------- OCR 后端
def test_ocr_engine_selection(monkeypatch):
    monkeypatch.setattr(ocr, "_engine", None)
    monkeypatch.setenv("HIRO_OCR", "rapidocr")
    assert ocr.engine() == "rapidocr"
    monkeypatch.setattr(ocr, "_engine", None)
    monkeypatch.setenv("HIRO_OCR", "tesseract")
    with pytest.raises(ValueError, match="可选"):
        ocr.engine()


def test_rapidocr_boxes_scaled_back(monkeypatch):
    """RapidOCR 路径：放大 3 倍识别，小图居中补白到 631 再识别，框减去补白偏移、除回原图坐标；只要英文时用英文模型；全角转半角。"""
    calls = []

    def eng(tag):
        def run(img):
            calls.append((tag, img.shape[:2]))
            ox, oy = (631 - 300) // 2, (631 - 120) // 2  # 300×120 的放大图补到 631×631 的偏移
            return [
                [[[ox + 30, oy + 60], [ox + 120, oy + 60], [ox + 120, oy + 90], [ox + 30, oy + 90]], "１２３", 0.9]
            ], None

        return run

    monkeypatch.setattr(ocr, "_rapid_engine", lambda languages: eng("en" if tuple(languages) == ("en-US",) else "ja"))
    monkeypatch.setattr(ocr, "_engine", "rapidocr")
    img = np.full((40, 100, 3), 255, np.uint8)
    assert ocr.ocr_boxes(img) == [("123", 0.9, (10, 20, 40, 30))]
    assert calls[-1] == ("ja", (631, 631))
    assert ocr.ocr(img, languages=("en-US",)) == [("123", 0.9)]
    assert calls[-1][0] == "en"


def test_rapidocr_model_lookup(monkeypatch, tmp_path):
    """识别模型先找 cache/rapidocr，没有再找已装 kaa 包里随带的同名文件；都没有讲清楚缺什么。"""
    (tmp_path / "japan_PP-OCRv3_rec_infer.onnx").write_bytes(b"x")
    monkeypatch.setattr(ocr, "RAPID_DIR", tmp_path)
    assert ocr._rapid_model("ja") == tmp_path / "japan_PP-OCRv3_rec_infer.onnx"
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match="en_PP-OCRv3_rec_infer.onnx"):  # noqa: RUF043
        ocr._rapid_model("en")


def test_screen_texts_region_sorted_and_sanitized(monkeypatch):
    """screen_texts：区域坐标加回整帧、自上而下 / 同行从左到右、NFKC 规整；Box.center 与 kotonebot 点 Rect 的位置相同。"""
    seen = {}

    def fake(img, *, scale):
        seen["shape"], seen["scale"] = img.shape[:2], scale
        return [("Ｂ（２）", 1.0, (60, 5, 80, 15)), ("A", 0.8, (0, 6, 20, 16)), ("上", 0.9, (0, 0, 10, 4))]

    monkeypatch.setattr(ocr, "ocr_boxes", fake)
    img = np.zeros((1280, 720, 3), np.uint8)
    got = ocr.screen_texts(img, (100, 200, 300, 50))
    assert seen == {"shape": (50, 300), "scale": ocr.SCREEN_SCALE}
    assert [t.text for t in got] == ["上", "A", "B(2)"]
    assert got[1].rect == ocr.Box(100, 206, 120, 216) and got[1].rect.center == (110, 211) and got[1].conf == 0.8
    assert ocr.Box(10, 10, 15, 13).center == (12, 11)  # x1 + w // 2，同 kotonebot device.click(Rect)
    assert ocr.find_text(img, "B(").text == "B(2)"
    assert ocr.find_text(img, lambda t: t.startswith("Z")) is None
    assert ocr.screen_texts(None) == []


# ---------------------------------------------------------------- 非 macOS 能导入
def test_modules_import_without_macos_frameworks():
    """挡住 AppKit / Quartz / Vision / Foundation / objc，模拟 Windows / Linux：这些模块顶层不能依赖它们。"""
    code = """
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('AppKit', 'Quartz', 'Vision', 'Foundation', 'objc'):
            raise ImportError('blocked: ' + name)
sys.meta_path.insert(0, Block())
import hiro.vision.ocr as o, hiro.loop.landscape, hiro.device as d
o._engine = None
import os; os.environ.pop('HIRO_OCR', None)
assert o.engine() == 'rapidocr', o.engine()
print('ok')
"""
    env = {**os.environ, "PYTHONPATH": str(SRC)}  # 保留原环境：Windows 上的 Python 缺 SYSTEMROOT 无法初始化
    env.pop("HIRO_OCR", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-2000:]


# ---------------------------------------------------------------- RapidOCR 读歪时的选行动页（2026-11-06 Windows 实测）
def test_lesson_text_tolerates_rapidocr_misreads():
    from hiro.vision import action

    def kind(text):
        return next((k for p, k in action.ACTIONS if p.search(text)), None)

    assert kind("V0公開レッズン") == "open_lesson"  # Vo.→V0、ス→ズ
    assert kind("W公開レッスン") == "open_lesson"  # Vi.→W
    assert kind("Da.公開レッスン") == "open_lesson"


@pytest.mark.parametrize(("bgr", "want"), [((180, 80, 240), "vo"), ((230, 140, 30), "da"), ((40, 150, 250), "vi")])
def test_attr_by_band_color(bgr, want):
    """文字条底色：粉=Vo、蓝=Da、橙=Vi（中间留一条白字，模拟按钮文字）。"""
    from hiro.vision import action

    img = np.full((1280, 720, 3), 255, np.uint8)
    img[1090:1120, 100:260] = bgr
    img[1100:1110, 150:210] = 255
    assert action._attr_by_band(img, 180, 1105) == want


# ---------------------------------------------------------------- 字形归一后的安全拦截（RapidOCR：「タイトルへ」→「タイトルヘ」）
def test_textnorm_folds_lookalike_kana():
    from hiro.vision.textnorm import contains, fold

    assert fold("タイトルヘ") == fold("タイトルへ")
    assert contains("メタイトルヘ", "タイトルへ")
    assert contains("通信工ラー", "エラー")
    assert not contains("リトライ", "タイトルへ")


def test_rescue_blocks_title_button_read_as_katakana_he():
    """LLM 兜底不能点「タイトルへ」：读成片假名「ヘ」也要拦住。"""
    from hiro.loop.rescue import check

    boxes = [("通信中にエラーが発生しました", 0.99, (60, 1042, 396, 1068)), ("タイトルヘ", 0.96, (112, 1142, 313, 1178)),
             ("リトライ", 1.0, (470, 1146, 578, 1175))]  # fmt: skip
    act, why = check({"action": "tap_box", "box": 1}, boxes)
    assert act["action"] == "stop" and "危险词" in why


def test_comm_error_never_picks_merged_title_box():
    """两个按钮被读成一个框「タイトルヘリトライ」时，不能把它当成リトライ去点（中心落在两个按钮之间）。"""
    from hiro.loop.comm_error import comm_error

    boxes = [("通信エラー", 0.99, (59, 957, 216, 989)), ("タイトルヘリトライ", 0.9, (112, 1142, 578, 1178))]
    assert comm_error(boxes)["retry"] is None


def test_advance_blocks_title_read_as_katakana_he():
    from hiro.loop.advance import blocked

    assert blocked(["タイトルヘ"]) is not None


# ---------------------------------------------------------------- Codex CLI 作为 LLM（Windows 上用 ChatGPT 账号）
def test_codex_adapter_spec_and_missing_binary(monkeypatch):
    from hiro.brain import llm

    monkeypatch.setenv("HIRO_CODEX_BIN", "")
    monkeypatch.setattr("shutil.which", lambda _: None)
    a = llm.from_spec("codex:gpt-6-astra")
    assert isinstance(a, llm.CodexCLI) and a.model == "gpt-6-astra"
    a.binary = None
    r = a.ask_json("sys", "user")
    assert r.data is None and "找不到 codex" in r.error


def test_codex_adapter_passes_utf8_prompt_and_reads_reply(monkeypatch, tmp_path):
    """系统提示 + 用户提示拼成 UTF-8 从标准输入传；回复从 -o 文件读；在临时目录里跑（不读仓库 AGENTS.md）。"""
    import subprocess as sp
    from pathlib import Path

    from hiro.brain import llm

    seen = {}

    def fake_run(cmd, input=None, capture_output=None, timeout=None, cwd=None):
        seen.update(cmd=cmd, prompt=input.decode("utf-8"), cwd=cwd)
        Path(cmd[cmd.index("-o") + 1]).write_text('{"action": "play", "card": 1}', encoding="utf-8")
        return sp.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    a = llm.CodexCLI(binary="codex.exe")
    r = a.ask_json("系统：只输出 JSON", "用户：出哪张牌")
    assert r.error is None and r.data == {"action": "play", "card": 1}
    assert "系统：只输出 JSON" in seen["prompt"] and "用户：出哪张牌" in seen["prompt"]
    assert seen["cmd"][-1] == "-" and "read-only" in seen["cmd"] and "--ephemeral" in seen["cmd"]
    assert seen["cwd"] and "hiro-codex-" in seen["cwd"]
    r = a.ask_json_image("s", "u", b"\xff\xd8jpeg")
    assert "-i" in seen["cmd"] and r.data == {"action": "play", "card": 1}


# ---------------------------------------------------------------- 本战选课：缺倍率换算数据时按 R1 回合数近似（R1_TURN_PROXY）
def test_r1_turn_weighted_gain_prefers_heavier_r1_attr():
    from types import SimpleNamespace

    from hiro.state import context as C
    from hiro.state.produce import FINALS

    st = SimpleNamespace(
        mode=FINALS, params={"Vo": 1710, "Da": 2268, "Vi": 913}, param_cap=3200, idol_card_id="i_card-shro-3-007"
    )
    vo = C.r1_turn_weighted_gain(st, {"Vo": 180})
    vi = C.r1_turn_weighted_gain(st, {"Vi": 180})
    da = C.r1_turn_weighted_gain(st, {"Da": 180})
    assert vo > da > vi  # 篠澤広 均衡型 R1 回合 Vo 4 / Da 3 / Vi 2
    capped = SimpleNamespace(**{**st.__dict__, "params": {"Vo": 3150, "Da": 2268, "Vi": 913}})
    assert C.r1_turn_weighted_gain(capped, {"Vo": 180}) == 4 * 50  # 到上限为止
    assert (
        C.r1_turn_weighted_gain(SimpleNamespace(**{**st.__dict__, "mode": "produce-007"}), {"Vo": 180}) is None
    )  # 选拔不算


def test_emulator_swipe_keeps_duration():
    """MuMu / adb 后端：带时长的滑动不再被 AdbImpl 丢掉时长（Windows 第二局换卡翻牌组每次都快速划）。"""
    from hiro.device import _keep_swipe_duration

    class Scaler:
        def logic_to_physical(self, p):
            return (p[0] * 1.5, p[1] * 1.5)

    class NemuIpcImpl:
        def __init__(self):
            self.calls = []

        def swipe(self, *a):
            self.calls.append(a)

    class Adb:
        def __init__(self):
            self.cmds = []

        def shell(self, cmd):
            self.cmds.append(cmd)

    class AdbImpl:
        def __init__(self):
            self.adb = Adb()

    class Dev:
        def __init__(self, touch, commands):
            self.scaler, self._touch, self._multitouch, self.commands = Scaler(), touch, None, commands
            self.plain = []

        def swipe(self, *a, **kw):
            self.plain.append(a)

    nemu, adb = NemuIpcImpl(), AdbImpl()
    d = Dev(nemu, adb)
    _keep_swipe_duration(d)
    d.swipe(100, 200, 100, 400, duration=0.6)
    assert nemu.calls == [(150, 300, 150, 600, 0.6)] and adb.adb.cmds == []
    d.swipe(1, 2, 3, 4)  # 不带时长照旧
    assert d.plain == [(1, 2, 3, 4, None)]

    adb2 = AdbImpl()
    d2 = Dev(adb2, adb2)
    _keep_swipe_duration(d2)
    d2.swipe(100, 200, 100, 400, duration=0.4)
    assert adb2.adb.cmds == ["input touchscreen swipe 150 300 150 600 400"]


def test_winocr_word_joining():
    """Windows OCR 一字一词：日文不留空格，英文数字隔开才留空格；同一行相隔很远的两段拆开（货架一排价格）。"""
    from hiro.vision.ocr import _join_words, _split_words

    w = lambda t, x, wd=20, h=20: (t, (x, 0, wd, h))  # noqa: E731
    assert _join_words([w("キ", 0), w("ャ", 30), w("ン", 52), w("セ", 74), w("ル", 96)]) == "キャンセル"
    assert _join_words([w("56.4", 0, 40), w("%", 42)]) == "56.4%"
    assert _join_words([w("Vo.", 0, 30), w("Lesson", 40, 60)]) == "Vo. Lesson"
    segs = _split_words([w("70", 0, 30), w("49", 34, 30), w("105", 200, 40)])
    assert [_join_words(s) for s in segs] == ["7049", "105"]


@pytest.mark.fixtures
def test_finals_npc_score_with_dropped_digit_is_rejected(monkeypatch):
    """本战 NPC 分数整场不变（R1 约 29–40 万、R2 约 42–60 万），读成五位数一定是掉了首位：作废，不交给名次自洽检查当上限
    （Windows 第二局 RapidOCR 把 451280 读成 51280，我方分数被丢了 8 次）。"""
    from hiro.vision import hif

    import cv2

    img = cv2.imread(str(Path(__file__).parent / "fixtures" / "finals" / "exam-r2-t12.jpg"))
    monkeypatch.setattr(
        hif, "ocr_engine", lambda: "vision"
    )  # 测的是 Vision 读法下掉首位的作废；非 Vision 后端走 ddddocr
    reads = iter([598516, 60505])
    monkeypatch.setattr(
        hif, "_ocr_int", lambda im, box: next(reads) if box[2] - box[0] == hif.FINALS_NPC_W + 1 else None
    )
    assert hif.read_finals_ranking(img)["npc_scores"] == [598516, None]


def test_imwrite_handles_non_ascii_path(tmp_path):
    """标定帧目录是中文名（「换卡」）：Windows 上 OpenCV 的 imwrite 会静默失败，hiro.imgio.imwrite 先编码再写文件。"""
    from hiro.imgio import imwrite

    import cv2
    import numpy as np

    p = tmp_path / "20261107-063238-换卡" / "01-阶段1.jpg"
    p.parent.mkdir()
    assert imwrite(p, np.full((8, 8, 3), 200, np.uint8)) and cv2.imdecode(
        np.frombuffer(p.read_bytes(), np.uint8), 1
    ).shape == (8, 8, 3)


def test_finals_round_needs_both_npc_in_one_round():
    """本战 NPC 目标分按主数据区间归轮：Windows 第三局 ddddocr 把 R1 的 399562 读成 599562，单看第一位像 R2；
    第二位 303083 仍是 R1，两位矛盾就不判。区间外的读数（703083）当读错。"""
    from hiro.vision.hif import finals_round_from_npc

    assert finals_round_from_npc([399562, 303083]) == 1
    assert finals_round_from_npc([599112, 455627]) == 2
    assert finals_round_from_npc([599112, None]) == 2
    assert finals_round_from_npc([599562, 303083]) is None
    assert finals_round_from_npc([None, 703083]) is None
    assert finals_round_from_npc([None, None]) is None


def test_finals_label_round_from_turns_when_npc_unsure():
    """开考判轮：剩余回合 > 9 只能是 R2；NPC 判不了时剩余 9 回合是 R1 刚开考（R2 开考是 12）。"""
    from hiro.produce.hif import finals_label_round

    assert finals_label_round(None, 9) == 1
    assert finals_label_round(None, 12) == 2
    assert finals_label_round(1, 12) == 2
    assert finals_label_round(2, 7) == 2
    assert finals_label_round(None, 7) is None


def test_merge_dialog_screens_tolerates_one_misread_cell():
    """两屏重叠两行、其中一格两屏认成不同的卡：仍按两行对齐（不再整块接在后面拼出 27 张），那一格记成没认全。
    只重叠一行时不放宽（牌组里常有同名卡，一行三格相同不足以对齐）。"""
    from hiro.vision.deck import merge_dialog_screens

    def screen(rows):
        return [
            {"row": r, "col": c, "card_id": cid, "name": cid, "upgrade": 1}
            for r, row in enumerate(rows)
            for c, cid in enumerate(row)
        ]

    a = [["a1", "a2", "a3", "a4"], ["b1", "b2", "b3", "b4"], ["c1", "c2", "c3", "c4"]]
    b = [["b1", "b2", "b3", "b4"], ["c1", "XX", "c3", "c4"], ["d1", "d2", "d3"]]
    m = merge_dialog_screens([screen(a), screen(b)], expected=15)
    assert m["complete"] and m["new_rows"] == [3, 1] and len(m["cards"]) == 15
    assert m["unknown"] == [9]
    one = merge_dialog_screens([screen(a), screen([["c1", "XX", "c3", "c4"], ["d1", "d2", "d3"]])], expected=15)
    assert one["new_rows"] == [3, 2] and not one["complete"]


@pytest.mark.fixtures
def test_result_score_over_max_is_dropped(monkeypatch):
    """结算页分数超过两轮合计上限（Windows 第四局把 1,079,363 读成 36350791）：作废成 None，名次和其他行照常。"""
    from hiro.vision import hif
    from hiro.vision.offline import load
    from hiro.vision.pages import _texts, read_finals_ranking_page

    import cv2

    real = hif._result_score
    monkeypatch.setattr(
        hif, "_result_score", lambda img, digits, b: (36350791, None) if digits == "479574" else real(img, digits, b)
    )
    img = load(cv2.imread(str(Path(__file__).parent / "fixtures" / "finals" / "round1-result.jpg")))
    r = read_finals_ranking_page(img, _texts(img))
    assert [(e["rank"], e["score"]) for e in r["entries"]] == [(1, None), (2, 365868), (3, 117846)] and r["rank"] == 3


def test_dimmed_card_needs_two_agreeing_gains():
    """压暗格几档提亮投票：两档一致才用；各档都不一样就当没认出（不交出认错的卡）。"""
    from hiro.vision.deck import _dimmed_card

    import numpy as np

    class C:
        def __init__(self, i):
            self._id = i

    crop = np.full((10, 10, 3), 100, np.uint8)
    seq = iter([None, C("a"), C("a")])
    assert _dimmed_card(crop, 2.0, lambda im: next(seq))._id == "a"
    seq2 = iter([C("a"), C("b"), C("c")])
    assert _dimmed_card(crop, 2.0, lambda im: next(seq2)) is None


@pytest.mark.fixtures
def test_enhance_dialog_scan_win5_stitches():
    """Windows 第五局强化翻牌组：MuMu 每滑一次约 1.5 行，第 2、3 屏只重叠一行，压暗格又认错两张 → 「第 3 屏没有重叠的行」、拼出 25 张。
    压暗格投票后拼出整副（记账 20 张，容差内）。"""
    from hiro.vision.deck import ENHANCE_GRID, merge_dialog_screens, read_card_dialog
    from hiro.vision.offline import load

    import cv2

    d = Path(__file__).parent / "fixtures" / "deck_scan" / "win5-enhance"
    if not d.is_dir():
        pytest.skip("需要样例 tests/fixtures/deck_scan/win5-enhance")
    screens = [
        read_card_dialog(load(cv2.imread(str(d / f"screen{i:02d}.png"))), ENHANCE_GRID)["cards"] for i in range(4)
    ]
    m = merge_dialog_screens(screens, expected=20)
    assert m["complete"] and m["new_rows"] == [3, 1, 2, 0]
    names = [c["name"] for c in m["cards"]]
    assert "潜在能力" not in names and names.count("えいえいおー") == 1


def test_sale_split_and_floor():
    """原价和 SALE 价读成一个数：按「SALE = 原价 × 0.7 取整」拆开取 SALE 价；小于 5 的残读（「50」读成「0」）去掉。"""
    from hiro.vision.shop import _sale_split

    assert (
        _sale_split([7049]) == [49]
        and _sale_split([10573]) == [73]
        and _sale_split([7553]) == [53]
        and _sale_split([75, 52]) == [75, 52]
    )
    assert _sale_split([0]) == [] and _sale_split([1234]) == [1234]


@pytest.mark.fixtures
def test_consult_sale_price_win6():
    """Windows 第六局相談货架 #124：SALE 卡 70→49，RapidOCR 整块读成 40；非 Vision 后端改为多种读法投票。"""
    from hiro.vision.offline import load
    from hiro.vision.shop import read_consult

    import cv2

    f = Path(__file__).parent / "fixtures" / "consult" / "consult-sale-win6-124.jpg"
    if not f.exists():
        pytest.skip("需要样例 tests/fixtures/consult/consult-sale-win6-124.jpg")
    shelf = read_consult(load(cv2.imread(str(f))))["shelf"]
    assert [e["price"] for e in shelf] == [49, 77, 70, 70, 70, 50, 50, 100]


@pytest.mark.fixtures
@pytest.mark.parametrize(("rel", "costs"), [
    ("class-no-cost-win6-011.jpg", [("class", None)] * 3),  # 授業卡没有消耗徽章：RapidOCR 读出幻影「18」
    ("open-lesson-cost-win6-083.jpg", [("rest", None), ("open_lesson", 8), ("open_lesson", 8), ("open_lesson", 6)]),  # 「-6」被漏读
])  # fmt: skip
def test_action_costs_gated_by_heart(rel, costs):
    """选行动页课程卡的体力消耗以绿色爱心徽章为准（非 Vision 后端）：没有爱心不认数字，有爱心没读到就单独读爱心右边。"""
    from hiro.vision.extract import extract
    from hiro.vision.offline import load

    import cv2

    f = Path(__file__).parent / "fixtures" / "action_select" / rel
    if not f.exists():
        pytest.skip(f"需要样例 tests/fixtures/action_select/{rel}")
    raw, _ = extract("ACTION_SELECT", load(cv2.imread(str(f))))
    assert [(o["kind"], o["hp_cost"]) for o in raw["options"]] == costs


def test_r1_done_in_trace(tmp_path):
    """本局 trace 里出现过 R1 轮次结果页（kind=round_result）就认为 R1 已打完；只有 standings（字段里 round_result: null）不算。"""
    import json
    from types import SimpleNamespace as NS

    import hiro.produce.hif as hif

    d = tmp_path / "mainwin10-20261108-032030"
    d.mkdir()
    page = lambda kind: json.dumps(  # noqa: E731
        {"observation": {"page": {"name": "HIF_FINALS_RANKING"}, "fields": {"kind": kind, "round_result": None}}}
    )
    (d / "steps.jsonl").write_text(page("standings") + "\n", encoding="utf-8")
    st = hif.HiroHifStrategy.__new__(hif.HiroHifStrategy)
    st.s = NS(trace=NS(dir=str(d)))
    assert st._r1_done_in_trace() is False
    (d / "steps.jsonl").write_text(page("standings") + "\n" + page("round_result") + "\n", encoding="utf-8")
    assert st._r1_done_in_trace() is True


def test_vote_price_sale_fixes_one_digit_misread(monkeypatch):
    """SALE 格：几种读法一致把 49 读成 40、整块读到了原价 70 → 按原价七折取整成 49（Windows 第八、十局）。"""
    from hiro.vision import digits, shop

    import numpy as np

    monkeypatch.setattr(shop, "_cell_price", lambda img, x, band, scale=3: [40])
    monkeypatch.setattr(digits, "read_dddd", lambda img, box: 40)
    img = np.zeros((1280, 720, 3), np.uint8)
    assert shop._vote_price(img, 65, (612, 652), [70, 40], sale=True) == 49
    assert shop._vote_price(img, 65, (612, 652), [70, 49], sale=True) == 49
    assert shop._vote_price(img, 65, (612, 652), [40], sale=False) == 40


def test_vote_price_sale_single_digit(monkeypatch):
    """SALE 实付价读成个位数（105→73 读成 5）：按看到的原价七折取整。"""
    from hiro.vision import digits, shop

    import numpy as np

    monkeypatch.setattr(shop, "_cell_price", lambda img, x, band, scale=3: [5])
    monkeypatch.setattr(digits, "read_dddd", lambda img, box: None)
    assert shop._vote_price(np.zeros((1280, 720, 3), np.uint8), 65, (612, 652), [105, 5], sale=True) == 73


def test_ap_shortfall_with_accented_a():
    """RapidOCR 把「APが8不足しています」读成「ÄPが8不足しています」（Windows 第十二局 AP 补充失败）：Ä 归一成 A，缺口照读。"""
    from hiro.loop import ap_refill as ap

    t = ap._texts([("ÄPが8不足しています", 0.9, (60, 1040, 300, 1070)), ("回復する", 0.9, (470, 1145, 580, 1175))])
    assert ap.shortfall(t) == 8 and ap._lowest(t, "回復する") is not None
    assert ap._texts([("ÄPドリンク", 0.9, (0, 0, 10, 10))])[0][0] == "APドリンク"


@pytest.mark.fixtures
def test_ap_shortfall_real_frame_win12():
    from hiro.loop import ap_refill as ap
    from hiro.produce.ops import ocr_boxes

    import cv2

    f = Path(__file__).parent / "fixtures" / "ap_refill" / "ap-short-win12.png"
    if not f.exists():
        pytest.skip("需要样例 tests/fixtures/ap_refill/ap-short-win12.png")
    assert ap.shortfall(ap._texts(ocr_boxes(cv2.imread(str(f))))) == 8


def test_vote_price_sale_known_prices(monkeypatch):
    """SALE 格连原价都没读到、实付价 49 读成 40：已知 SALE 价里只差一位的唯一一个是 49。"""
    from hiro.vision import digits, shop

    import numpy as np

    monkeypatch.setattr(shop, "_cell_price", lambda img, x, band, scale=3: [40])
    monkeypatch.setattr(digits, "read_dddd", lambda img, box: 40)
    assert shop._vote_price(np.zeros((1280, 720, 3), np.uint8), 65, (612, 652), [40], sale=True) == 49


def test_sim_workers_capped_on_windows(monkeypatch):
    """Windows 默认不开模拟进程池（spawn 子进程重新导入整套 KAA，第十五局内存吃满、MuMu 崩溃）；HIRO_SIM_WORKERS 可改。"""
    import sys

    from hiro.sim.interval import sim_workers

    monkeypatch.delenv("HIRO_SIM_WORKERS", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    assert sim_workers(4) == 1
    monkeypatch.setenv("HIRO_SIM_WORKERS", "2")
    assert sim_workers(4) == 2
    monkeypatch.delenv("HIRO_SIM_WORKERS")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert sim_workers(4) == 4


def test_score_added_digit_rejected():
    """考试分数 2890 读成 28900（末尾多一位）：判读错，不采信（Windows 第十五局之后 10 步正确读数都被拦）。正常增长不受影响。"""
    from hiro.state.invariants import _added_digit

    assert _added_digit(2890, 28900) and _added_digit(2890, 28905) and _added_digit(2890, 12890)
    assert not _added_digit(2890, 10012) and not _added_digit(597, 2890) and not _added_digit(48913, 109614)
    assert not _added_digit(50, 500)
