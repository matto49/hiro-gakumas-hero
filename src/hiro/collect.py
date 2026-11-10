# 来源：digital-hiro src/hiro/collect.py（develop ce5085f）
"""实机采集：持续截图，画面变化时保存并打上识别结果，用于积累样本、验证识别。

    .venv/bin/python -m hiro.collect [--interval 0.5] [--minutes 30] [--name 备注] [--port 8765]

输出 datasets/<时间>-<备注>/：frames/*.jpg 与 manifest.jsonl（每行带识别原始 fields 与契约 observation）。

同一窗口被两个进程同时截图时，后来者的 CGWindowListCreateImage 会返回空，所以截图只能有一个进程。
需要操作时不要另起进程，而是通过本进程的本机 HTTP 接口下发，由采集循环在同一线程执行：
    curl 'http://127.0.0.1:8765/tap?x=360&y=1161&wait=1.5'
    curl 'http://127.0.0.1:8765/dtap?x=100&y=1010&wait=1.5'   # 行动按钮等需要双击确认
    curl 'http://127.0.0.1:8765/swipe?x1=360&y1=900&x2=360&y2=400&duration=0.4&wait=1'
    curl 'http://127.0.0.1:8765/shot'
每个命令返回执行后那一帧的识别结果与截图路径。
"""

import argparse
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

import hiro  # noqa: F401

from hiro.device import connect
from kaa.config.schema import KaaConfig
from kaa.kaa_context import init as kaa_init
from kotonebot.backend.context import device, init_context, manual_context

from hiro.vision.extract import extract
from hiro.vision.observe import observation
from hiro.vision.scene import classify_current


def check_stale() -> str | None:
    """素材包比本机游戏资源旧时的提示。要读本机客户端资源（私有的 hiro.assets.octo）；没有这个模块时不查。"""
    try:
        from hiro.assets import check_stale as _check  # 私有：读 PlayCover 客户端缓存
    except ImportError:
        return None
    return _check()


def dhash(img, size: int = 16) -> np.ndarray:
    gray = cv2.cvtColor(cv2.resize(img, (size + 1, size)), cv2.COLOR_BGR2GRAY)
    return (gray[:, 1:] > gray[:, :-1]).flatten()


class _Command:
    def __init__(self, name: str, params: dict):
        self.name, self.params = name, params
        self.done = threading.Event()
        self.result: dict = {}


def _serve(port: int, commands: "queue.Queue[_Command]") -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            cmd = _Command(url.path.strip("/"), params)
            commands.put(cmd)
            cmd.done.wait(timeout=30)
            body = json.dumps(cmd.result or {"error": "timeout"}, ensure_ascii=False, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--threshold", type=int, default=12, help="dHash 汉明距离超过该值才视为新画面")
    parser.add_argument("--min-gap", type=float, default=3.0, help="同一场景两次自动保存的最小间隔（秒），避免待机动画刷屏")
    parser.add_argument("--name", default="live")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--dir", default=None, help="追加到已有数据集目录")
    args = parser.parse_args()
    if stale := check_stale():
        print(f"[警告] {stale}")

    out = Path(args.dir) if args.dir else Path("datasets") / f"{datetime.now():%Y%m%d-%H%M%S}-{args.name}"
    (out / "frames").mkdir(parents=True, exist_ok=True)
    manifest = (out / "manifest.jsonl").open("a", encoding="utf-8")
    kaa_init(KaaConfig(), "collect")
    init_context(target_device=connect())
    manual_context().begin()
    commands: "queue.Queue[_Command]" = queue.Queue()
    threading.Thread(target=_serve, args=(args.port, commands), daemon=True).start()
    print(f"collecting into {out}; commands on http://127.0.0.1:{args.port}", flush=True)

    last_hash = None
    last_auto = {"scene": None, "t": 0.0}
    seq = sum(1 for _ in (out / "manifest.jsonl").open()) if (out / "manifest.jsonl").exists() else 0
    deadline = time.time() + args.minutes * 60
    def save(img, capture_ms: float, trigger: str | None) -> dict:
        nonlocal seq
        result = classify_current()
        fields, extract_ms = extract(result.scene, img)
        seq += 1
        name = f"{seq:05d}-{result.scene}.jpg"
        cv2.imwrite(str(out / "frames" / name), img, [cv2.IMWRITE_JPEG_QUALITY, 92])  # PNG 约 0.7MB/帧，曾把磁盘写满
        t = time.time()
        timings = {"capture": round(capture_ms, 1), "classify": round(result.elapsed_ms, 1), "extract": round(extract_ms, 1)}
        obs = observation(seq, t, result.scene, fields, area=result.area, screenshot=f"frames/{name}", timings_ms=timings)
        row = {
            "seq": seq, "t": t, "file": f"frames/{name}", "area": result.area, "scene": result.scene,
            "trigger": trigger, "capture_ms": timings["capture"], "classify_ms": timings["classify"],
            "extract_ms": timings["extract"], "fields": fields, "observation": obs.to_dict(),
        }
        manifest.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        manifest.flush()
        print(f"{seq:05d} {result.area}/{result.scene} classify={result.elapsed_ms:.0f}ms extract={extract_ms:.0f}ms {trigger or ''}", flush=True)
        return row

    def run_command(cmd: _Command) -> None:
        nonlocal last_hash
        p = cmd.params
        try:
            if cmd.name == "tap":
                device.click(int(p["x"]), int(p["y"]))
            elif cmd.name == "dtap":
                device.double_click(int(p["x"]), int(p["y"]))
            elif cmd.name == "swipe":
                device.swipe(int(p["x1"]), int(p["y1"]), int(p["x2"]), int(p["y2"]), float(p.get("duration", 0.4)))
            elif cmd.name != "shot":
                cmd.result = {"error": f"unknown command {cmd.name}"}
                return
            time.sleep(float(p.get("wait", 1.5 if cmd.name != "shot" else 0)))
            tick = time.perf_counter()
            img = device.screenshot()
            last_hash = dhash(img)
            cmd.result = save(img, (time.perf_counter() - tick) * 1000, f"{cmd.name} {p}")
            cmd.result["path"] = str(out / cmd.result["file"])
        except Exception as e:
            cmd.result = {"error": repr(e)}
        finally:
            cmd.done.set()

    while time.time() < deadline:
        tick = time.perf_counter()
        while not commands.empty():
            run_command(commands.get())
        try:
            img = device.screenshot()
        except Exception as e:  # 截图偶发失败（锁屏、窗口切换），跳过本次
            print(f"screenshot failed: {e}", flush=True)
            time.sleep(1)
            continue
        capture_ms = (time.perf_counter() - tick) * 1000
        h = dhash(img)
        if last_hash is None or int(np.count_nonzero(h != last_hash)) > args.threshold:
            last_hash = h
            scene = classify_current().scene
            if scene != last_auto["scene"] or time.time() - last_auto["t"] >= args.min_gap:
                save(img, capture_ms, None)
                last_auto.update(scene=scene, t=time.time())
        time.sleep(max(0.0, args.interval - (time.perf_counter() - tick)))


if __name__ == "__main__":
    main()
