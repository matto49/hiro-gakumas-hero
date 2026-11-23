"""UINet onnx（IR 10 / opset 20）→ onnxruntime 1.14 能读的版本（IR 8 / opset 18）：Gelu 拆成 0.5·x·(1+erf(x/√2))。

KAA（ksaa）把 onnxruntime 钉在 ~=1.14.0，读不了 IR 10 / opset 20 的原模型；网络本身不变，只换写法。
2026-11-12 用 400 个真实 UIObs 核对：原模型（onnxruntime 1.31）与本转换（1.14）动作 400/400 相同，概率最大差 6e-7。
独立脚本，只依赖 onnx / numpy，在隔离环境里跑，不装进 hiro 的环境：
    uv run --isolated --no-project --with onnx --with numpy python convert_model.py 原模型.onnx 输出.onnx
"""

import sys

import numpy as np
import onnx
from onnx import helper, numpy_helper

src, dst = sys.argv[1], sys.argv[2]
m = onnx.load(src)
g = m.graph
nodes = []
c_half = numpy_helper.from_array(np.array(0.5, np.float32), "gelu_c_half")
c_one = numpy_helper.from_array(np.array(1.0, np.float32), "gelu_c_one")
c_isq2 = numpy_helper.from_array(np.array(1 / np.sqrt(2), np.float32), "gelu_c_isqrt2")
g.initializer.extend([c_half, c_one, c_isq2])
n = 0
for node in g.node:
    if node.op_type == "Gelu":
        approx = next((a.s.decode() for a in node.attribute if a.name == "approximate"), "none")
        assert approx == "none", approx
        x, y = node.input[0], node.output[0]
        p = f"gelu{n}_"
        n += 1
        nodes += [
            helper.make_node("Mul", [x, "gelu_c_isqrt2"], [p + "s"]),
            helper.make_node("Erf", [p + "s"], [p + "e"]),
            helper.make_node("Add", [p + "e", "gelu_c_one"], [p + "a"]),
            helper.make_node("Mul", [x, p + "a"], [p + "m"]),
            helper.make_node("Mul", [p + "m", "gelu_c_half"], [y]),
        ]
    else:
        nodes.append(node)
del g.node[:]
g.node.extend(nodes)
for o in m.opset_import:
    if o.domain in ("", "ai.onnx"):
        o.version = 18
m.ir_version = 8
onnx.checker.check_model(m)
onnx.save(m, dst)
print("gelu replaced", n)
