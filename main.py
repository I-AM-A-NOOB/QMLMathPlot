"""组件封装的最小验证入口。

    python main.py --backend d3d11 "sin(1/x)"

左键拖拽平移；滚轮以光标为锚点缩放。实现见 qmlmathplot.app。
"""

import sys

from qmlmathplot.app import main

if __name__ == "__main__":
    sys.exit(main())
