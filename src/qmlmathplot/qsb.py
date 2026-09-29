"""把运行时生成的 GLSL 烘成 .qsb（Qt 6 的 ShaderEffect / 自定义材质只吃 .qsb）。

为什么必须在运行期烘：着色器里带着用户输入的表达式（sympy 生成），Qt 官方的
工作流是"构建时用 qsb 烘好、把 .qsb 作为资源发出去"，这里做不到。代价实测：
一条着色器约 63 ms（两个 stage 约 126 ms），按源码 sha256 缓存到临时目录，同一
表达式第二次起 0.15 ms，帧循环里零调用。

跨平台：Windows wheel 实测自带 ``PySide6/qsb.exe``；PySide6 的打包脚本
（build_scripts/wheel_files.py，Quick3D 模块的 extra_files 里是 ``qsb*``）没有
平台条件，所以三大平台预期都有，但只有 Windows 是我亲手验证过的。找不到时会
退回 PATH，再找不到就报错并给出获取方式。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

__all__ = ["bake", "qsb_binary"]

_EXE = "qsb.exe" if os.name == "nt" else "qsb"


def _candidates() -> list[Path]:
    """qsb 可能出现的位置：PySide6 包目录（各平台布局不同）、Qt 的 libexec、PATH。"""
    cands: list[Path] = []
    try:
        import PySide6  # 延迟导入：只有 QML 前端才需要烘着色器

        pkg = Path(PySide6.__file__).parent
        cands += [pkg / _EXE, pkg / "Qt" / "bin" / _EXE, pkg / "Qt" / "libexec" / _EXE]
    except ImportError:  # 没装 PySide6 时仍然允许用 PATH 上的 qsb
        pass
    found = shutil.which("qsb")
    if found:
        cands.append(Path(found))
    return cands


def qsb_binary() -> Path:
    """定位 qsb（Qt Shader Baker）。"""
    for cand in _candidates():
        if cand.exists():
            return cand
    raise RuntimeError(
        "找不到 qsb（Qt Shader Baker），QML 前端的着色器无法烘焙。三种拿到它的办法：\n"
        "  1) 用 PySide6 的 wheel（Windows 实测自带 qsb.exe；其他平台先确认一下）\n"
        "  2) 装 Qt Shader Tools：发行版包（如 qt6-shadertools）或 Qt 在线安装器\n"
        "  3) 已经有 qsb 的话，把它放进 PATH\n"
        "见 https://doc.qt.io/qt-6/qtshadertools-index.html"
    )


def bake(source: str, stage: str, cache_dir: Path | str | None = None) -> Path:
    """把一段 GLSL 源码烘成 .qsb，返回文件路径（带源码 hash 缓存）。

    stage: "vert" 或 "frag"
    """
    if stage not in ("vert", "frag"):
        raise ValueError(f"stage 必须是 'vert' 或 'frag'，收到 {stage!r}")

    key = hashlib.sha256(f"{stage}\0{source}".encode()).hexdigest()[:16]
    out_dir = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()) / "qmlmathplot-qsb"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stage}-{key}.qsb"
    if out_path.exists():
        return out_path

    src_path = out_dir / f"{stage}-{key}.glsl"
    src_path.write_text(source, encoding="utf-8")
    tmp_out = out_path.with_suffix(".qsb.tmp")
    # --qt6 == --glsl "100 es,120,150" --hlsl 50 --msl 12，即 Qt Quick 材质的标准目标集
    proc = subprocess.run(  # noqa: S603
        [str(qsb_binary()), "--qt6", "-o", str(tmp_out), str(src_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not tmp_out.exists():
        raise RuntimeError(
            f"qsb 烘焙失败（{stage}, rc={proc.returncode}）：\n{proc.stdout}\n{proc.stderr}"
        )
    tmp_out.replace(out_path)  # 原子替换，避免并发/中断留下半个文件
    return out_path
