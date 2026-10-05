"""最小示例：开一个独立窗口。

    python examples/minimal.py --backend d3d11 "sin(1/x)"
    uv run qmlmathplot "tan(x)"          # 等价（命令行入口）

左键拖拽平移；滚轮以光标为锚点缩放。实现见 ``qmlmathplot.app``。
"""

import sys

from qmlmathplot.app import main

if __name__ == "__main__":
    sys.exit(main())
