"""验证 core.py 旧注释的论断："带用户函数的着色器版本在 Intel D3D11 上会让第一帧挂死"。

stage 烘焙修好之后再测同一件事（同一表达式、同一后端）：把片元模板里的
``#define F(u)`` / ``#define DF`` 换成真正的 GLSL 用户函数，其余不变。
"""

from __future__ import annotations

import os
import runpy
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import _safe_grab  # noqa: F401  （把 grabWindow 换成异步抓图，避免会话死锁）

from qmlmathplot import core

_OLD = "#define F(u) (@FUNC@)\n#define DF @DFUNC@"
_NEW = ("float F(float x) { return (@FUNC@); }\n"
        "float DFf(float x) { return (@DFUNC@); }\n"
        "#define DF DFf(x)")

if _OLD not in core.QML_FRAGMENT_TEMPLATE:
    raise SystemExit("模板结构变了，请更新本探针")
core.QML_FRAGMENT_TEMPLATE = core.QML_FRAGMENT_TEMPLATE.replace(_OLD, _NEW)
print("[probe] 已把 F()/DF 宏替换为 GLSL 用户函数", flush=True)

sys.argv = ["_probe_qml.py", sys.argv[1] if len(sys.argv) > 1 else "sin(1/x)"]
runpy.run_path("_probe_qml.py", run_name="__main__")
