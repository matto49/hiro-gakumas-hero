"""从初实机 trace 还原逐周：每个 ACTION_SELECT 决策点算一周（同一周停机重进只算一次），考试页按 exam_session.label 记。
用法：python tools/weeks_from_traces.py traces/<run1> traces/<run2> ...（同一局的 trace 按时间顺序给）"""

import json
import sys

weeks = []
last_key = None
seen_exams = set()
for d in sys.argv[1:]:
    try:
        lines = open(d + "/steps.jsonl").read().splitlines()  # noqa: SIM115
    except OSError:
        continue
    for line in lines:
        s = json.loads(line)
        o = s.get("observation") or {}
        name = (o.get("page") or {}).get("name")
        f = o.get("fields") or {}
        st = s.get("state") or {}
        label = (st.get("exam_session") or {}).get("label")
        if name == "ACTION_SELECT" and f.get("options") is not None and f.get("params"):
            opts = sorted({(x.get("option"), x.get("attr"), bool(x.get("sp"))) for x in f["options"]}, key=str)
            key = ("sel", json.dumps(f["params"], sort_keys=True), f.get("produce_point"), f.get("stamina"))
            if key != last_key and opts:
                weeks.append(
                    {
                        "kind": "select",
                        "trace": d,
                        "seq": s["seq"],
                        "stamina": f.get("stamina"),
                        "pp": f.get("produce_point"),
                        "params": f["params"],
                        "options": [f"{a}{'/' + b if b else ''}{'/SP' if c else ''}" for a, b, c in opts],
                    }
                )
                last_key = key
        elif label and label.startswith("hajime-") and not label.startswith("hajime-lesson"):  # noqa: SIM102
            if label not in seen_exams:
                seen_exams.add(label)
                weeks.append({"kind": "exam", "label": label, "trace": d, "seq": s["seq"]})
                last_key = ("exam", label)
for i, w in enumerate(weeks, 1):
    print(
        i,
        w.get("label") or "",
        w.get("stamina", ""),
        w.get("pp", ""),
        w.get("params", ""),
        w.get("options", ""),
        w["trace"][-15:],
        w["seq"],
    )
