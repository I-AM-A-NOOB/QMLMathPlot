"""无窗口判定：D3D 后端的问题出在"驱动"还是"着色器(HLSL)不合法"。

三层证据，全部在无窗口、无交换链下取（不会让桌面卡死）：

  1) D3DCompile（Microsoft 自带的 HLSL→DXBC 编译器，纯 CPU 工作）
     —— HLSL 是否合法、编译耗时多少。这一步与显卡驱动无关。
  2) 硬件 D3D11 设备（Intel/AMD/NV）上的 CreateVertexShader / CreatePixelShader
     —— 驱动是否接受这份 DXBC（驱动在这里做 DXBC→ISA 的二次编译，挂死通常发生于此）。
  3) WARP（Windows 软件光栅器）上同样两步 —— "换一家实现"的对照：
     若 WARP 通过而硬件挂 → 驱动问题；若两者都在同一变体上失败 → HLSL 本身不合法。

着色器变体（同一份 QML 隐函数着色器，逐个削减语法构造）：
  V0 平凡的对照 HLSL（只有 cbuffer，直接 return 常数）
  V1 只有平滑描边（删掉整个欠采样包络块）
  V2 全量但把 6 处动态分支 if(...) 改成三元表达式（去动态流控制）
  V3 全量但把 8 处 (s != s) NaN 三元改成直通（去自比较 idiom）
  V4 全量（生产用着色器）

用法: python _probe_d3d_headless.py [expr]   （默认 sin(1/x)）
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import sympy as sp  # noqa: E402

from qmlmathplot import core, qsb  # noqa: E402

ZERO = 0


# ---------------------------------------------------------------- Win32/COM 胶水

def _blob_bytes(blob: ctypes.c_void_p) -> bytes:
    """ID3DBlob 的 vtable: 0..2 IUnknown, 3 GetBufferPointer, 4 GetBufferSize。"""
    vtbl = ctypes.cast(blob, ctypes.POINTER(ctypes.c_void_p))[0]
    fns = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))
    get_ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(fns[3])
    get_size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(fns[4])
    return ctypes.string_at(get_ptr(blob), get_size(blob))


def _vcall(obj: ctypes.c_void_p, index: int, restype, argtypes, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0]
    fns = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(fns[index])(obj, *args)


try:
    _compiler = ctypes.WinDLL("d3dcompiler_47.dll")
except OSError:  # 老系统回退
    _compiler = ctypes.WinDLL("d3dcompiler_43.dll")

_D3DCompile = _compiler.D3DCompile
_D3DCompile.restype = ctypes.c_long
_D3DCompile.argtypes = [
    ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint,
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
]

_d3d11 = ctypes.WinDLL("d3d11.dll")
_D3D11CreateDevice = _d3d11.D3D11CreateDevice
_D3D11CreateDevice.restype = ctypes.c_long
_D3D11CreateDevice.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint,
    ctypes.POINTER(ctypes.c_uint), ctypes.c_uint, ctypes.c_uint,
    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_void_p),
]

D3D_DRIVER_TYPE_HARDWARE = 1
D3D_DRIVER_TYPE_WARP = 5
FEATURE_LEVELS = (ctypes.c_uint * 3)(0xB000, 0xA100, 0xA000)  # 11_0, 10_1, 10_0


def compile_hlsl(src: str, stage: str) -> tuple[bool, float, int, str, bytes]:
    """D3DCompile: HLSL -> DXBC。返回 (成功, 秒, hr, 错误文本, 字节码)。"""
    target = "ps_5_0" if stage == "frag" else "vs_5_0"
    data = src.encode()
    code = ctypes.c_void_p()
    err = ctypes.c_void_p()
    t0 = time.perf_counter()
    hr = _D3DCompile(data, len(data), b"shader.hlsl", None, None, b"main", target.encode(),
                     0, 0, ctypes.byref(code), ctypes.byref(err))
    dt = time.perf_counter() - t0
    msg = ""
    bytecode = b""
    if err:
        msg = _blob_bytes(err).decode("utf-8", "replace").strip()
    if hr == ZERO and code:
        bytecode = _blob_bytes(code)
    return hr == ZERO, dt, hr, msg, bytecode


def create_device(driver_type: int):
    """无 HWND、无交换链的 D3D11 设备。"""
    dev = ctypes.c_void_p()
    ctx = ctypes.c_void_p()
    level = ctypes.c_uint()
    hr = _D3D11CreateDevice(None, driver_type, None, 0, FEATURE_LEVELS, 3, 7,
                            ctypes.byref(dev), ctypes.byref(level), ctypes.byref(ctx))
    if hr != ZERO or not dev:
        return None, None, hr, 0
    return dev, ctx, hr, level.value


def create_shader(dev: ctypes.c_void_p, bytecode: bytes, stage: str) -> tuple[bool, float, int]:
    """ID3D11Device::CreateVertexShader(12) / CreatePixelShader(15)——驱动在这里二次编译。"""
    index = 15 if stage == "frag" else 12
    out = ctypes.c_void_p()
    t0 = time.perf_counter()
    hr = _vcall(dev, index, ctypes.c_long,
                [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                ctypes.c_char_p(bytecode), len(bytecode), None, ctypes.byref(out))
    return hr == ZERO, time.perf_counter() - t0, hr


# ---------------------------------------------------------------- 着色器变体

def extract_hlsl(glsl: str, stage: str) -> str:
    """走生产同一条路：GLSL --qsb--> .qsb --dump--> HLSL 5.0 源码。"""
    path = qsb.bake(glsl, stage)
    dump = subprocess.run([str(qsb.qsb_binary()), "-d", str(path)],
                          capture_output=True, text=True, check=True).stdout
    lines = dump.splitlines()
    start = None
    for i, line in enumerate(lines):
        # 段头行不缩进；列表里的条目是缩进的 -> 用缩进区分
        if line == line.lstrip() and re.match(r"Shader \d+: HLSL 50\b", line):
            for k in range(i + 1, min(i + 6, len(lines))):
                if "Contents:" in lines[k]:
                    start = k + 1
                    break
            break
    if start is None:
        raise RuntimeError("qsb dump 里找不到 HLSL 50 段")
    out: list[str] = []
    for line in lines[start:]:
        if set(line.strip()) == {"*"} and line.strip():
            break
        out.append(line)
    return "\n".join(out).strip()


def variants(expr: sp.Expr) -> dict[str, tuple[str, str]]:
    """{名字: (stage, HLSL 源码)}"""
    vert_glsl, frag_glsl = core.qml_shader_sources(expr)

    # V1: 删掉整个欠采样包络块（保留平滑描边）
    i2 = frag_glsl.index("    // ---- 2)")
    iend = frag_glsl.index("    float a = cov")
    v1 = frag_glsl[:i2] + frag_glsl[iend:]

    # V2: 6 处动态分支 -> 三元（去动态流控制）
    v2 = re.sub(r"if \((d\w+ \* d\w+ < 0\.0)\) \{ turns \+= 1\.0; \}",
                r"turns += (\1) ? 1.0 : 0.0;", frag_glsl)

    # V3: 8 处 NaN 自比较 idiom -> 直通
    v3 = re.sub(r"(s\d) = \(\1 != \1\) \? 0\.0 : \1;", r"\1 = \1;", frag_glsl)

    # 关键：取的是 qsb 真正交给 D3D11 的那份 HLSL（和生产同一条路）
    out: dict[str, tuple[str, str]] = {"V4-全量": ("frag", extract_hlsl(frag_glsl, "frag"))}
    if v1 != frag_glsl:
        out["V1-去包络块"] = ("frag", extract_hlsl(v1, "frag"))
    if v2 != frag_glsl:
        out["V2-去动态分支"] = ("frag", extract_hlsl(v2, "frag"))
    if v3 != frag_glsl:
        out["V3-去NaN三元"] = ("frag", extract_hlsl(v3, "frag"))
    out["Vx-顶点"] = ("vert", extract_hlsl(vert_glsl, "vert"))
    return out


CONTROL_HLSL = """
cbuffer buf : register(b0)
{
    float4x4 qt_Matrix;
    float qt_Opacity;
    float4 view;
    float2 size;
    float lineWidth;
    float4 color;
};

struct VOut
{
    float4 pos : SV_Position;
    float2 uv : TEXCOORD0;
};

float4 main(VOut v) : SV_Target
{
    return float4(1.0, 0.5, 0.1, 1.0) * lineWidth;
}
"""


def main() -> int:
    expr_src = sys.argv[1] if len(sys.argv) > 1 else "sin(1/x)"
    expr = sp.sympify(expr_src, locals={"x": sp.Symbol("x")})
    print(f"表达式: {expr_src}", flush=True)

    cases: dict[str, tuple[str, str]] = {"V0-对照(平凡HLSL)": ("frag", CONTROL_HLSL)}
    cases.update(variants(expr))

    print("\n=== 第 1 层: D3DCompile（Microsoft HLSL 编译器，纯 CPU，与显卡无关）===", flush=True)
    compiled: dict[str, bytes] = {}
    for name, (stage, src) in cases.items():
        ok, dt, hr, msg, bytecode = compile_hlsl(src, stage)
        flag = "OK " if ok else "FAIL"
        print(f"  {name:<18} [{flag}] {dt * 1000:7.1f} ms  hr=0x{hr & 0xFFFFFFFF:08X}"
              f"  {len(bytecode)} B", flush=True)
        if msg:
            print(f"      └─ {msg.splitlines()[0][:150]}", flush=True)
        if ok:
            compiled[name] = bytecode

    for label, driver in (("硬件(Intel/AMD/NV)", D3D_DRIVER_TYPE_HARDWARE),
                          ("WARP(软件光栅器)", D3D_DRIVER_TYPE_WARP)):
        print(f"\n=== 第 2{'(对照)' if driver == D3D_DRIVER_TYPE_WARP else ''} 层: {label} "
              f"设备创建 + 着色器创建（驱动二次编译）===", flush=True)
        t0 = time.perf_counter()
        dev, _ctx, hr, level = create_device(driver)
        if dev is None:
            print(f"  D3D11CreateDevice 失败 hr=0x{hr & 0xFFFFFFFF:08X}", flush=True)
            continue
        print(f"  设备 OK: featureLevel=0x{level:X}  ({time.perf_counter() - t0:.2f} s)", flush=True)
        for name, (stage, _src) in cases.items():
            if name not in compiled:
                continue
            ok, dt, hr = create_shader(dev, compiled[name], stage)
            flag = "OK " if ok else "FAIL"
            print(f"  {name:<18} [{flag}] {dt * 1000:7.1f} ms  hr=0x{hr & 0xFFFFFFFF:08X}"
                  f"  ({stage})", flush=True)
    print("\n完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
