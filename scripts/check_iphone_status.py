#!/usr/bin/env python3
"""
检查当前连接到 Mac 的 iPhone USB 状态，并在处于恢复模式 (0x1281) 时
通过 libusb 直接读取 iBoot 底层 NVRAM 环境变量（全程无需按手机按键）。
"""
import ctypes
import subprocess
import sys


def check_ioreg():
    out = subprocess.run(
        ["ioreg", "-p", "IOUSB", "-l", "-w", "0"],
        capture_output=True,
        text=True,
    ).stdout
    modes = {
        4647: "DFU Mode (0x1227 - 硬件级固件升级模式)",
        4737: "Recovery Mode (0x1281 - 普通恢复模式 iBoot)",
        4780: "RestoreOS Ramdisk Mode (0x12ac - 刷机内存盘模式)",
        4776: "Normal OS Mode (0x12a8 - 正常开机模式)",
    }
    found = False
    for pid, desc in modes.items():
        if f'"idProduct" = {pid}' in out:
            print(f"[+] 检测到 iPhone 连接状态: {desc}")
            found = True
    if not found:
        print("[-] 未在 USB 总线上检测到 iPhone。")
    return out


def query_iboot_nvram():
    try:
        lib = ctypes.CDLL("/opt/homebrew/lib/libusb-1.0.dylib")
    except OSError:
        print("[!] 未找到 /opt/homebrew/lib/libusb-1.0.dylib，跳过 iBoot 变量读取。")
        return

    lib.libusb_init(None)
    lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
    handle = lib.libusb_open_device_with_vid_pid(None, 0x05AC, 0x1281)
    if not handle:
        print("[i] 设备当前不在 0x1281 (Recovery Mode)，跳过 libusb iBoot 查询。")
        return

    print("[+] 已通过 libusb 连接 iBoot (0x05ac:0x1281)，正在读取底层环境变量：")
    lib.libusb_control_transfer.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_void_p,
        ctypes.c_uint16,
        ctypes.c_uint,
    ]

    def get_var(var: str):
        cmd = f"getenv {var}"
        buf = ctypes.create_string_buffer(cmd.encode("ascii") + b"\x00")
        r = lib.libusb_control_transfer(handle, 0x40, 0, 0, 0, buf, len(cmd) + 1, 1000)
        if r < 0:
            return None
        out = ctypes.create_string_buffer(512)
        r2 = lib.libusb_control_transfer(handle, 0xC0, 0, 0, 0, out, 512, 1000)
        return out.value.decode("utf-8", "ignore").strip() if r2 > 0 else ""

    variables = [
        "build-version",
        "build-style",
        "config_board",
        "auto-boot",
        "boot-command",
        "boot-device",
        "boot-partition",
        "boot-path",
        "display-color-space",
        "loadaddr",
        "ramdisk-size",
        "restore-outcome",
    ]
    for v in variables:
        val = get_var(v)
        if val is not None:
            print(f"    {v:20s} = {val!r}")

    lib.libusb_close(ctypes.c_void_p(handle))


if __name__ == "__main__":
    check_ioreg()
    query_iboot_nvram()
