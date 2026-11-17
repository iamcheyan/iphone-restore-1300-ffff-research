#!/usr/bin/env python3
"""
零空间占用、毫秒级修改 IPSW 固件包中的 BuildManifest.plist：
1. 使用 macOS APFS `cp -c` 写时复制克隆 12.8 GB 的 .ipsw 文件（耗时 0.01s，不占额外硬盘空间）
2. 修改 BuildManifest.plist 中的 IsiBootEANFirmware / IsiBootNonEssentialFirmware 标记：
   - mode = "nuke": 将所有 IsiBootEANFirmware 和 IsiBootNonEssentialFirmware 设为 False，
     触发 macOS MobileDevice.framework 自动注入 `iBootEANNuke = True`，
     使手机端 restored_update 跳过 _write_ean_image 并强行清空卡死的 ifw1 / ifw2 键。
   - mode = "minimal": 仅保留 AppleLogo (30 KB) 或 AppleLogo + DCP (屏幕驱动)，
     大幅减小 ifw2 写入体积，规避 EAN 空间不足导致的 0xe00002e9 错误。
3. 直接在 ZIP64 归档末尾原地更新 BuildManifest.plist 并重写中央目录（耗时 < 0.1s，无需重新压缩 12.8 GB 数据）。
"""
import argparse
import os
import plistlib
import struct
import subprocess
import time
import zipfile
import zlib


def patch_build_manifest(bm_bytes: bytes, mode: str) -> bytes:
    bm = plistlib.loads(bm_bytes)
    modified_count = 0
    for ident in bm.get("BuildIdentities", []):
        manifest = ident.get("Manifest", {})
        for key, entry in manifest.items():
            info = entry.get("Info", {})
            if mode == "nuke":
                if info.get("IsiBootEANFirmware"):
                    info["IsiBootEANFirmware"] = False
                    modified_count += 1
                if info.get("IsiBootNonEssentialFirmware"):
                    info["IsiBootNonEssentialFirmware"] = False
                    modified_count += 1
            elif mode == "minimal":
                # 仅保留 AppleLogo (30KB) 以最小体积完成 ifw2 写入并触发 swap(ifw2 -> ifw1)
                if info.get("IsiBootEANFirmware") and key not in ("AppleLogo",):
                    info["IsiBootEANFirmware"] = False
                    modified_count += 1
                if info.get("IsiBootNonEssentialFirmware"):
                    info["IsiBootNonEssentialFirmware"] = False
                    modified_count += 1
            elif mode == "dcp":
                # 仅保留 AppleLogo + DCP (屏幕显示协处理器驱动)，剔除 RecoveryMode/Liquid/LowPowerMode 等大图标
                if info.get("IsiBootEANFirmware") and key not in ("AppleLogo", "DCP"):
                    info["IsiBootEANFirmware"] = False
                    modified_count += 1
                if info.get("IsiBootNonEssentialFirmware"):
                    info["IsiBootNonEssentialFirmware"] = False
                    modified_count += 1
    print(f"[+] 模式 '{mode}': 已修改 BuildManifest.plist 中的 {modified_count} 处 EAN 标记")
    return plistlib.dumps(bm, fmt=plistlib.FMT_XML, sort_keys=True)


def inplace_replace_file_in_zip64(zip_path: str, target_name: str, new_data: bytes):
    """
    在已克隆的 ZIP / ZIP64 归档末尾追加新的 target_name 条目，并重写中央目录 (Central Directory)，
    避免重写前方的 12.8 GB 镜像数据，从而保留 APFS 克隆块的完全共享。
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        infolist = zf.infolist()
        cd_start_offset = min(
            info.header_offset for info in infolist
        )  # placeholder, we find actual CD start below

    with open(zip_path, "r+b") as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        # 读取末尾 65KB 寻找 EOCD 和 ZIP64 EOCD Locator
        tail_size = min(file_size, 65536 + 128)
        f.seek(file_size - tail_size)
        tail = f.read(tail_size)

        eocd_pos = tail.rfind(b"PK\x05\x06")
        if eocd_pos == -1:
            raise RuntimeError("未找到 ZIP EOCD 签名")

        # 检查是否存在 ZIP64 EOCD Locator (PK\x06\x07，位于 EOCD 前 20 字节)
        loc_pos = eocd_pos - 20
        if loc_pos >= 0 and tail[loc_pos : loc_pos + 4] == b"PK\x06\x07":
            _, _, zip64_eocd_offset, _ = struct.unpack(
                "<4sIQI", tail[loc_pos : loc_pos + 20]
            )
            f.seek(zip64_eocd_offset)
            z64_eocd = f.read(56)
            (
                sig,
                sz,
                ver_made,
                ver_ext,
                disk_num,
                disk_cd,
                entries_disk,
                entries_total,
                cd_size,
                cd_offset,
            ) = struct.unpack("<4sQHHIIQQQQ", z64_eocd)
            assert sig == b"PK\x06\x06", "无效的 ZIP64 EOCD"
        else:
            raise RuntimeError("非 ZIP64 归档")

        # 读取原始 Central Directory
        f.seek(cd_offset)
        raw_cd = f.read(cd_size)

        # 在原始 cd_offset 位置写入新的 BuildManifest.plist Local File Header + 压缩数据
        new_local_offset = cd_offset
        crc = zlib.crc32(new_data) & 0xFFFFFFFF
        comp_obj = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
        comp_data = comp_obj.compress(new_data) + comp_obj.flush()
        comp_size = len(comp_data)
        uncomp_size = len(new_data)
        name_bytes = target_name.encode("utf-8")

        # 构造 Local File Header (PK\x03\x04)
        lfh = struct.pack(
            "<4sHHHHHIIIHH",
            b"PK\x03\x04",
            20,  # version needed
            0,  # flags
            zipfile.ZIP_DEFLATED,
            0,  # mod time
            0,  # mod date
            crc,
            comp_size if new_local_offset < 0xFFFFFFFF else 0xFFFFFFFF,
            uncomp_size if new_local_offset < 0xFFFFFFFF else 0xFFFFFFFF,
            len(name_bytes),
            0,  # extra len
        )
        # 如果 new_local_offset >= 4GB，Local Header 自身不需要 64bit offset，只需 32bit size 足够容纳 859KB
        lfh = struct.pack(
            "<4sHHHHHIIIHH",
            b"PK\x03\x04",
            45,
            0,
            zipfile.ZIP_DEFLATED,
            0,
            0,
            crc,
            comp_size,
            uncomp_size,
            len(name_bytes),
            0,
        )

        f.seek(new_local_offset)
        f.write(lfh)
        f.write(name_bytes)
        f.write(comp_data)
        new_cd_offset = f.tell()

        # 遍历并更新 Central Directory 中的 BuildManifest.plist 条目
        pos = 0
        new_cd_parts = []
        while pos < len(raw_cd):
            sig = raw_cd[pos : pos + 4]
            if sig != b"PK\x01\x02":
                raise RuntimeError(f"CD 偏移 {pos} 处签名无效: {sig}")
            (
                _,
                v_made,
                v_need,
                flags,
                method,
                mtime,
                mdate,
                c_crc,
                c_csize,
                c_usize,
                fn_len,
                ex_len,
                cm_len,
                d_start,
                int_attr,
                ext_attr,
                rel_off,
            ) = struct.unpack("<4sHHHHHHIIIHHHHHII", raw_cd[pos : pos + 46])
            entry_name = raw_cd[pos + 46 : pos + 46 + fn_len].decode("utf-8", "ignore")
            extra_bytes = raw_cd[pos + 46 + fn_len : pos + 46 + fn_len + ex_len]
            comment_bytes = raw_cd[
                pos + 46 + fn_len + ex_len : pos + 46 + fn_len + ex_len + cm_len
            ]
            next_pos = pos + 46 + fn_len + ex_len + cm_len

            if entry_name == target_name:
                # 使用 ZIP64 Extra Field (0x0001) 记录 64-bit 偏移量 new_local_offset
                z64_extra = struct.pack("<HHQ", 0x0001, 8, new_local_offset)
                new_cdfh = struct.pack(
                    "<4sHHHHHHIIIHHHHHII",
                    b"PK\x01\x02",
                    v_made,
                    45,
                    0,
                    zipfile.ZIP_DEFLATED,
                    mtime,
                    mdate,
                    crc,
                    comp_size,
                    uncomp_size,
                    len(name_bytes),
                    len(z64_extra),
                    len(comment_bytes),
                    d_start,
                    int_attr,
                    ext_attr,
                    0xFFFFFFFF,
                )
                new_cd_parts.append(new_cdfh + name_bytes + z64_extra + comment_bytes)
            else:
                new_cd_parts.append(raw_cd[pos:next_pos])
            pos = next_pos

        new_cd_bytes = b"".join(new_cd_parts)
        new_cd_size = len(new_cd_bytes)
        f.write(new_cd_bytes)

        # 写入新的 ZIP64 EOCD
        new_z64_eocd_offset = f.tell()
        new_z64_eocd = struct.pack(
            "<4sQHHIIQQQQ",
            b"PK\x06\x06",
            44,
            ver_made,
            ver_ext,
            disk_num,
            disk_cd,
            entries_disk,
            entries_total,
            new_cd_size,
            new_cd_offset,
        )
        f.write(new_z64_eocd)

        # 写入新的 ZIP64 EOCD Locator
        new_z64_loc = struct.pack("<4sIQI", b"PK\x06\x07", 0, new_z64_eocd_offset, 1)
        f.write(new_z64_loc)

        # 写入新的 EOCD
        new_eocd = struct.pack(
            "<4sHHHHIIH",
            b"PK\x05\x06",
            0,
            0,
            0xFFFF if entries_disk >= 0xFFFF else entries_disk,
            0xFFFF if entries_total >= 0xFFFF else entries_total,
            0xFFFFFFFF if new_cd_size >= 0xFFFFFFFF else new_cd_size,
            0xFFFFFFFF,
            0,
        )
        f.write(new_eocd)
        f.truncate()


def main():
    parser = argparse.ArgumentParser(description="Patch IPSW BuildManifest.plist for EAN Error 7 (1300.FFFF)")
    parser.add_argument("--input", required=True, help="原始 .ipsw 路径")
    parser.add_argument("--output", required=True, help="输出修复版 .ipsw 路径")
    parser.add_argument(
        "--mode",
        choices=["nuke", "minimal", "dcp"],
        default="nuke",
        help="nuke: 触发 iBootEANNuke 清空 ifw1/ifw2; minimal: 仅保留 AppleLogo; dcp: 仅保留 AppleLogo+DCP",
    )
    args = parser.parse_args()

    src = os.path.expanduser(args.input)
    dst = os.path.expanduser(args.output)

    t0 = time.time()
    with zipfile.ZipFile(src, "r") as zf:
        orig_bm = zf.read("BuildManifest.plist")

    patched_bm = patch_build_manifest(orig_bm, args.mode)

    if os.path.exists(dst):
        os.remove(dst)
    print(f"[+] 正在使用 APFS 写时复制克隆 (cp -c) 创建固件副本: {dst}")
    subprocess.run(["cp", "-c", src, dst], check=True)

    print("[+] 正在原地更新 ZIP64 归档中的 BuildManifest.plist...")
    inplace_replace_file_in_zip64(dst, "BuildManifest.plist", patched_bm)

    # 验证修改后的归档与 plist
    with zipfile.ZipFile(dst, "r") as zf:
        verify_bm = plistlib.loads(zf.read("BuildManifest.plist"))
        ean_list = [
            k
            for k, v in verify_bm["BuildIdentities"][0]["Manifest"].items()
            if v.get("Info", {}).get("IsiBootEANFirmware")
        ]
        print(f"[+] 校验通过！修复版固件 Identity[0] 当前 IsiBootEANFirmware 列表: {ean_list}")
    print(f"[+] 全部完成，耗时 {time.time() - t0:.2f} 秒！输出文件: {dst}")


if __name__ == "__main__":
    main()
