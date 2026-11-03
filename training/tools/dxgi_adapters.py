# -*- coding: utf-8 -*-
"""
用 DXGI 枚举显示适配器：索引、名称、厂商 ID、LUID、显存。

为什么要自己写：onnxruntime 只给 device_id 这个整数，不告诉你它对应哪块 GPU。
而这台机器有三块适配器（GameViewer 虚拟显示器 / Radeon 780M / RTX 4060），
DML 默认选中的是 **独显**，导致"以为在 780M 上跑"其实跑在 4060 上。
LUID 还能和 Windows 性能计数器 `\\GPU Engine(*)` 里的 luid_0x... 直接对上，
从而把"进程 → 适配器"的归属做实。

用法：
    python -c "from dxgi_adapters import enum_adapters; [print(a) for a in enum_adapters()]"
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_void_p, c_wchar

# 厂商 ID（PCI Vendor ID）
VENDOR = {
    0x1002: "AMD",
    0x10DE: "NVIDIA",
    0x8086: "Intel",
    0x1414: "Microsoft",
}


class DXGI_ADAPTER_DESC(ctypes.Structure):
    _fields_ = [
        ("Description", c_wchar * 128),
        ("VendorId", ctypes.c_uint),
        ("DeviceId", ctypes.c_uint),
        ("SubSysId", ctypes.c_uint),
        ("Revision", ctypes.c_uint),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", ctypes.c_int64),
        ("Flags", ctypes.c_uint),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# IID_IDXGIFactory {7b7166ec-21c7-44ae-b21a-c9ae321ae369}
IID_IDXGIFactory = GUID(0x7B7166EC, 0x21C7, 0x44AE,
                        (ctypes.c_ubyte * 8)(0xB2, 0x1A, 0xC9, 0xAE,
                                             0x32, 0x1A, 0xE3, 0x69))


def _vfn(ptr, slot: int, restype, *argtypes):
    """取 COM 对象 vtable 第 slot 个函数"""
    vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[slot])


def enum_adapters() -> list[dict]:
    """
    返回按 DXGI 枚举顺序排列的适配器列表。
    device_id 就是这个列表的下标（与 onnxruntime 的 device_id 一致）。
    """
    try:
        dxgi = ctypes.WinDLL("dxgi.dll")
    except OSError:
        return []

    factory = c_void_p()
    hr = dxgi.CreateDXGIFactory(byref(IID_IDXGIFactory), byref(factory))
    if hr != 0 or not factory:
        return []

    out = []
    try:
        # IDXGIFactory vtable: 7 = EnumAdapters, 8 = MakeWindowAssociation
        EnumAdapters = _vfn(factory, 7, ctypes.c_long,
                            ctypes.c_uint, POINTER(c_void_p))
        # IDXGIAdapter vtable: 7 = EnumOutputs, 8 = GetDesc
        i = 0
        while True:
            adapter = c_void_p()
            hr = EnumAdapters(factory, i, byref(adapter))
            if hr != 0 or not adapter:      # DXGI_ERROR_NOT_FOUND
                break
            try:
                GetDesc = _vfn(adapter, 8, ctypes.c_long,
                               POINTER(DXGI_ADAPTER_DESC))
                desc = DXGI_ADAPTER_DESC()
                if GetDesc(adapter, byref(desc)) == 0:
                    luid = desc.AdapterLuid & 0xFFFFFFFFFFFFFFFF
                    out.append({
                        "device_id": i,
                        "name": desc.Description,
                        "vendor_id": desc.VendorId,
                        "vendor": VENDOR.get(desc.VendorId, "Unknown"),
                        "luid": luid,
                        "luid_str": f"0x{luid:016x}",
                        "dedicated_mb": desc.DedicatedVideoMemory // (1 << 20),
                        "shared_mb": desc.SharedSystemMemory // (1 << 20),
                    })
            finally:
                # IDXGIAdapter Release (vtable slot 2)
                _vfn(adapter, 2, ctypes.c_ulong)(adapter)
            i += 1
    finally:
        _vfn(factory, 2, ctypes.c_ulong)(factory)
    return out


def find_device_id(adapters: list[dict], vendor_id: int = 0x1002) -> int | None:
    """按厂商 ID 找 device_id（默认找 AMD）"""
    for a in adapters:
        # 虚拟显示器厂商 ID 常为 0x1414(Microsoft)；只认真正的 AMD
        if a["vendor_id"] == vendor_id and a["dedicated_mb"] >= 0:
            return a["device_id"]
    return None


if __name__ == "__main__":
    ads = enum_adapters()
    if not ads:
        print("枚举失败")
    for a in ads:
        print(f"[{a['device_id']}] {a['name']:<42s} "
              f"vendor=0x{a['vendor_id']:04X}({a['vendor']})  "
              f"luid={a['luid_str']}  显存 {a['dedicated_mb']} MB / "
              f"共享 {a['shared_mb']} MB")
