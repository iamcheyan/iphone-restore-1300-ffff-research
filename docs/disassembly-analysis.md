# `restored_update` 与 `MobileDevice.framework` 逆向反编译详解

本文档详细记录了对 iOS `27.2.1` (`24B156`) RestoreRamDisk 中的 `/usr/local/bin/restored_update` 以及 macOS `/Library/Apple/System/Library/PrivateFrameworks/MobileDevice.framework/Versions/A/MobileDevice` 的 ARM64e 逆向分析过程。

---

## 一、从 IPSW 中提取并挂载未加密的 RestoreRamDisk

在 `iPhone17,3_27.2.1_24B156_Restore.ipsw` 中，根据 `BuildManifest.plist` 可知 `RestoreRamDisk` 对应文件为 `141-09491-250.dmg`（约 247 MB）。

检查其文件头：
```text
00000000: 3084 0ec0 0015 1604 494d 3450 1604 7264  0.......IM4P..rd
00000010: 736b 1601 3004 840e c000 00a3 032a 9f07  sk..0........*..
00000020: 6b87 9b01 0000 0000 0000 000a 0000 0000  k...............
00000030: 0000 0001 0000 8000 0000 004e 5853 4200  ...........NXSB.
```
可以看到，前 27 字节（`0x00`–`0x1A`）是 ASN.1 DER 编码的 `IM4P` (`rdsk`) 容器头，从偏移 `0x1B`（第 28 字节）开始直接就是一个未加密、未压缩的 **APFS 容器（魔数 `NXSB`）**。

通过剥离前 27 字节即可直接在 macOS 上只读挂载：
```bash
python3 -c '
import zipfile
with zipfile.ZipFile("iPhone17,3_27.2.1_24B156_Restore.ipsw") as z:
    data = z.read("141-09491-250.dmg")[27:]
open("/tmp/rdsk_apfs.dmg", "wb").write(data)
'
hdiutil attach -readonly -nobrowse /tmp/rdsk_apfs.dmg
# 挂载点：/Volumes/ramdisk
```

---

## 二、`restored_update` 中 `format_ean_storage` 的反汇编分析 (`0x100029560`)

### 2.1 ARM64e 汇编代码
```armasm
0000000100029560	mov	w8, #0x1
0000000100029564	str	w8, [sp, #0x18]
0000000100029568	ldr	w0, [sp, #0x1c]          ; AppleNVMeEAN IOConnect 句柄
000000010002956c	add	x4, sp, #0x10            ; output: &isFormatted
0000000100029570	add	x5, sp, #0x18            ; outputCnt: 1
0000000100029574	mov	w1, #0x0                 ; selector = 0 (isFormatted)
0000000100029578	mov	x2, #0x0
000000010002957c	mov	w3, #0x0
0000000100029580	bl	0x1001bb794              ; _IOConnectCallScalarMethod
0000000100029584	cbz	w0, 0x1000295cc
0000000100029588	str	x0, [sp]
000000010002958c	adrp	x0, 438 ; 0x1001df000
0000000100029590	add	x0, x0, #0x798           ; "error checking if EAN storage is formatted: 0x%x\n"
0000000100029594	bl	0x1000115ac
...
00000001000295cc	ldr	x8, [sp, #0x10]          ; x8 = isFormatted
00000001000295d0	cbz	x8, 0x100029604          ; 若 isFormatted == 0，跳去执行格式化 (selector = 1)
00000001000295d4	adrp	x0, 438 ; 0x1001df000
00000001000295d8	add	x0, x0, #0x7ca           ; "EAN storage is already formatted\n"
00000001000295dc	bl	0x1000115ac
00000001000295e0	adrp	x1, 687 ; 0x1002d8000
00000001000295e4	add	x1, x1, #0xc98           ; @"SkipEANClean"
00000001000295e8	mov	x0, x20
00000001000295ec	mov	w2, #0x0
00000001000295f0	bl	0x100043ce4
00000001000295f4	cbz	w0, 0x100029634
00000001000295f8	adrp	x0, 438 ; 0x1001df000
00000001000295fc	add	x0, x0, #0x7f9           ; "Skipping clean EAN images step\n"
0000000100029600	b	0x100029554
0000000100029604	ldr	w0, [sp, #0x1c]
0000000100029608	mov	w1, #0x1                 ; selector = 1 (formatEAN)
000000010002960c	mov	x2, #0x0
0000000100029610	mov	w3, #0x0
0000000100029614	mov	x4, #0x0
0000000100029618	mov	x5, #0x0
000000010002961c	bl	0x1001bb794              ; _IOConnectCallScalarMethod
0000000100029620	cbz	w0, 0x100029684
...
0000000100029634	adrp	x0, 438 ; 0x1001df000
0000000100029638	add	x0, x0, #0x819           ; "Cleaning EAN images\n"
000000010002963c	bl	0x1000115ac
0000000100029640	ldr	w0, [sp, #0x1c]
0000000100029644	mov	w1, #0x9                 ; selector = 9 (cleanEANImages)
0000000100029648	mov	x2, #0x0
000000010002964c	mov	w3, #0x0
0000000100029650	mov	x4, #0x0
0000000100029654	mov	x5, #0x0
0000000100029658	bl	0x1001bb794              ; _IOConnectCallScalarMethod
```

### 2.2 C 伪代码还原
```c
int format_ean_storage(CFDictionaryRef options) {
    io_service_t service = IOServiceGetMatchingService(kIOMasterPortDefault, IOServiceMatching("AppleNVMeEAN"));
    if (!service) {
        log("Ean storage not present for this device. Skipping ean format.\n");
        return 0;
    }
    io_connect_t conn = 0;
    IOServiceOpen(service, mach_task_self(), 0, &conn);

    uint64_t isFormatted = 0;
    uint32_t outCnt = 1;
    kern_return_t kr = IOConnectCallScalarMethod(conn, 0 /* isFormatted */, NULL, 0, &isFormatted, &outCnt);
    if (kr != KERN_SUCCESS) {
        log("error checking if EAN storage is formatted: 0x%x\n", kr);
        return 0x53;
    }

    if (isFormatted != 0) {
        log("EAN storage is already formatted\n");
        if (get_boolean_option(options, CFSTR("SkipEANClean"), false)) {
            log("Skipping clean EAN images step\n");
            return 0;
        }
        log("Cleaning EAN images\n");
        kr = IOConnectCallScalarMethod(conn, 9 /* cleanImages */, NULL, 0, NULL, NULL);
        // 注意：selector 9 会清理普通 EAN 图像（如旧的 DCP/Logo），但保留 iBoot 引导键 ifw1 / ifw2！
        log("EAN storage cleaned successfully\n");
        return 0;
    } else {
        kr = IOConnectCallScalarMethod(conn, 1 /* format */, NULL, 0, NULL, NULL);
        log("EAN storage formatted successfully\n");
        return 0;
    }
}
```

---

## 三、`restored_update` 中 `update_iBoot` 与 `_write_ean_image` 的反汇编分析 (`0x100057900`–`0x100057d34`)

### 3.1 ARM64e 汇编代码
```armasm
; 1. 向 Host 请求 IsiBootEANFirmware 字典
0000000100057904	ldr	x8, [x27, #0x58]         ; callbacks->copy_ibfw_data
0000000100057908	mov	x0, x28
000000010005790c	blraaz	x8
0000000100057910	mov	x23, x0
0000000100057914	cbz	x0, 0x100058460
0000000100057918	mov	x0, x23
000000010005791c	bl	_CFDictionaryGetCount
0000000100057920	cmp	x0, #0x1
0000000100057924	str	x23, [sp, #0x70]
0000000100057928	str	x28, [sp, #0x50]
000000010005792c	b.lt	0x100057a20              ; 若 IsiBootEANFirmware 数量 < 1，直接跳过 ifw 写入！

; 2. 打包 IsiBootEANFirmware 并检查 ifw1 / ifw2
0000000100057930	adrp	x0, 416 ; 0x1001f7000
0000000100057934	add	x0, x0, #0x35b           ; "Writing out IsiBootEANFirmware files to EAN iBoot keys.\n"
0000000100057938	bl	0x1000115ac
000000010005793c	mov	x0, x23
0000000100057940	mov	w1, #0x1
0000000100057944	bl	0x100062260              ; _create_ibfw (读取 ifw1/ifw2 确定 generation，并拼接为 7815168 字节镜像)
0000000100057948	str	x0, [sp, #0x78]
000000010005794c	cbz	x0, 0x100058478
0000000100057950	str	xzr, [sp, #0x88]
0000000100057954	adrp	x28, 647 ; 0x1002de000
0000000100057958	add	x28, x28, #0x798         ; @"ifw2"
000000010005795c	add	x2, sp, #0x88
0000000100057960	mov	x0, x28
0000000100057964	mov	x1, #0x0
0000000100057968	bl	0x100062600              ; 读取 EAN 中的 "ifw2" 并提取其 bootHash
...
00000001000579f0	bl	_memcmp                  ; 对比 ifw2Hash 与当前启动的 BootManifestHash
00000001000579f4	cmp	w0, #0x0
00000001000579f8	cset	w22, ne
00000001000579fc	adrp	x8, 416 ; 0x1001f7000
0000000100057a00	add	x8, x8, #0x96c           ; "ifw2 hash matches boot hash, switching to ifw1 for staging.\n"
0000000100057a04	adrp	x9, 416 ; 0x1001f7000
0000000100057a08	add	x9, x9, #0x9a9           ; "ifw2 hash does not match boot hash - using ifw2 to stage new data.\n"
0000000100057a0c	csel	x0, x9, x8, ne
...
; 3. 先写 staging key (ifw2)，后删 primary key (ifw1) —— 导致已存在 ifw2 时写入 7.81MB 失败！
0000000100057aa0	bl	0x100051ba8              ; 调用 _write_ean_dict 写入 "ifw2" (7815168 字节)
0000000100057aa4	cbz	w0, 0x100057ae8          ; 返回 0 则跳到 0x100057ae8 打印 "_write_ibfw failed.\n"！
0000000100057aa8	mov	x0, x27
0000000100057aac	bl	0x100051d14              ; 调用 _erase_ean_key (selector = 3)
0000000100057ab0	cbz	x0, 0x100057b34
0000000100057ab4	cbz	w22, 0x100057bd4
0000000100057ab8	adrp	x0, 647 ; 0x1002de000
0000000100057abc	add	x0, x0, #0x778           ; @"ifw1"
0000000100057ac0	mov	x1, x28                  ; @"ifw2"
0000000100057ac4	bl	0x1000627f8              ; swap("ifw1", "ifw2")

; 4. 检查隐藏开关 iBootEANNuke
0000000100057cf4	ldr	x0, [x28, #0x8]          ; ctx->options
0000000100057cf8	cbz	x0, 0x100057d34
0000000100057cfc	adrp	x1, 647 ; 0x1002de000
0000000100057d00	add	x1, x1, #0x758           ; @"iBootEANNuke"
0000000100057d04	mov	w2, #0x0
0000000100057d08	bl	_AMSupportCFDictionaryGetBoolean
0000000100057d0c	cbz	w0, 0x100057d34
0000000100057d10	adrp	x0, 416 ; 0x1001f7000
0000000100057d14	add	x0, x0, #0x4d8           ; "Nuking iBootEAN keys.\n"
0000000100057d18	bl	0x1000115ac
0000000100057d1c	adrp	x0, 647 ; 0x1002de000
0000000100057d20	add	x0, x0, #0x778           ; @"ifw1"
0000000100057d24	bl	0x100051e84              ; nuke_ean_key(@"ifw1")
0000000100057d28	adrp	x0, 647 ; 0x1002de000
0000000100057d2c	add	x0, x0, #0x798           ; @"ifw2"
0000000100057d30	bl	0x100051e84              ; nuke_ean_key(@"ifw2")
```

### 3.2 `_write_ean_image` 底层调用 (`0x100061dc0`)
```armasm
0000000100061de8	adrp	x0, 404 ; 0x1001f5000
0000000100061dec	add	x0, x0, #0xbfc           ; "Writing EAN key %c%c%c%c, (%lu of %lu) imageSize=%llu\n"
0000000100061df4	bl	0x1000115ac
0000000100061df8	ldr	w0, [x19]                ; AppleNVMeEAN IOConnect 句柄
0000000100061e00	sub	x2, x29, #0x60           ; scalarInput: [key_fourcc ('ifw2'), buf_ptr, imageSize (7815168)]
0000000100061e0c	mov	w1, #0x2                 ; selector = 2 (writeImage)
0000000100061e10	mov	w3, #0x3                 ; inputCnt = 3
0000000100061e1c	bl	_IOConnectCallMethod
0000000100061e20	mov	x22, x0
...
0000000100061e34	cbz	w22, 0x100061e4c
0000000100061e38	str	x22, [sp]
0000000100061e3c	adrp	x0, 404 ; 0x1001f5000
0000000100061e40	add	x0, x0, #0xc33           ; "_write_ean_image failed kernResult=0x%x\n"
0000000100061e44	bl	0x1000115ac
```

---

## 四、macOS `MobileDevice.framework` 自动注入 `iBootEANNuke` 的反汇编分析 (`0x15936c`)

在 `/Library/Apple/System/Library/PrivateFrameworks/MobileDevice.framework/Versions/A/MobileDevice` (`arm64e` 架构) 中：

```armasm
000000000015936c	ldr	x1, [sp, #0x68]
0000000000159370	add	x3, sp, #0x48
0000000000159374	mov	x0, x25
0000000000159378	ldr	x2, [sp, #0x38]
000000000015937c	bl	_AMAuthInstallBundleCopyPersonalizediBootEANFirmwareURLs
0000000000159380	cbnz	w0, 0x1593b8
0000000000159384	ldr	x0, [sp, #0x48]          ; personalizedEANURLs 字典
0000000000159388	cbz	x0, 0x1593b8
000000000015938c	bl	0x45e640                 ; _CFDictionaryGetCount
0000000000159390	cbnz	x0, 0x1593b8             ; 若 count != 0 则跳过；若 count == 0：
0000000000159394	adrp	x1, 841 ; 0x4a2000
0000000000159398	add	x1, x1, #0x8d0           ; @"iBootEANNuke" (CString 位于 0x3f4805)
000000000015939c	mov	x0, x23                  ; restoreOptions 字典
00000000001593a0	ldr	x2, [sp, #0x20]          ; kCFBooleanTrue
00000000001593a4	bl	0x45e6c0                 ; _CFDictionarySetValue(restoreOptions, @"iBootEANNuke", kCFBooleanTrue)
00000000001593a8	adrp	x1, 841 ; 0x4a2000
00000000001593ac	add	x1, x1, #0x8f0           ; @"Setting kAMRestoreOptionsiBootEANNuke." (CString 位于 0x3f4812)
00000000001593b0	mov	w0, #0x6
00000000001593b4	bl	_AMRLog
```

以及 `_AMAuthInstallBundleCopyPersonalizediBootEANFirmwareURLs` (`0x28fcdc`) 的实现：
```armasm
_AMAuthInstallBundleCopyPersonalizediBootEANFirmwareURLs:
000000000028fcdc	mov	x4, x3
000000000028fce0	adrp	x3, 561 ; 0x4c0000
000000000028fce4	add	x3, x3, #0x3b0           ; kAMAuthInstallBuildIdentityIsiBootEANFirmware (@"IsiBootEANFirmware")
000000000028fce8	b	_AMAuthInstallBundleCopyPersonalizedURLsMatchingType
```

这证明了：只要将 IPSW 内 `BuildManifest.plist` 中对应 BuildIdentity 下各组件的 `Info -> IsiBootEANFirmware` 设为 `false`，`_AMAuthInstallBundleCopyPersonalizediBootEANFirmwareURLs` 返回的字典即为空字典（`count == 0`），从而由 `MobileDevice.framework` 原生触发 `iBootEANNuke = true`。

> **实测补充说明**：
> 在 `iPhoneUpdater-2.log` 实测中发现，`MobileDevice.framework` 虽然在本地 `restoreOptions` 字典上设置了 `iBootEANNuke = true` 并打印了 `Setting kAMRestoreOptionsiBootEANNuke.`，但在构造通过 USB 发送给手机端 `restored_update` 的 `RestoreOptions` 消息字典时，**遗漏了转发 `iBootEANNuke` 键**。因此手机端仅因 `count == 0` 跳过了 `_write_ean_image` 并完成了 100% 刷机流程（`Restore completed, status: 0`），而没有执行 `0x100057d10` 处的 `Nuking iBootEAN keys.`。

---

## 五、解包 `RestoreKernelCache` (`kernelcache.release.d47`) 与 `AppleNVMeEAN.cpp` 内核驱动分析

为了查明为什么即便将 `ifw2` 写入体积缩减至 **32 KB (`imageSize=32768`)**（见 `iPhoneUpdater-6.log`），`AppleNVMeEAN` 的 `selector = 2` (`writeImage`) 依然返回 `0xe00002e9` (`kIOReturnDeviceError`)，我们进一步解包了 `kernelcache.release.d47`：

### 5.1 解压 IM4P + LZFSE (`bvx2`) 内核缓存

`kernelcache.release.d47` 前 99 字节为 ASN.1 `IM4P` (`krnl`) 头部，从偏移 `99` (`0x63`) 开始是标准的 **LZFSE (`bvx2`)** 压缩流。使用 macOS 系统自带的 `libcompression.dylib` 即可直接解压出 96.4 MB 的 `MH_FILESET` Mach-O 内核镜像：

```python
import ctypes, zipfile

libcomp = ctypes.CDLL("/usr/lib/libcompression.dylib")
with zipfile.ZipFile("iPhone17,3_27.2.1_24B156_Restore.ipsw") as z:
    data = z.read("kernelcache.release.d47")

bvx_off = data.find(b"bvx2")  # offset = 99
comp_stream = data[bvx_off:]
dst_size = 160 * 1024 * 1024
dst_buf = ctypes.create_string_buffer(dst_size)
decompressed_len = libcomp.compression_decode_buffer(
    dst_buf, dst_size, comp_stream, len(comp_stream), None, 0x800  # COMPRESSION_LZFSE
)
open("/tmp/kc_raw.macho", "wb").write(dst_buf.raw[:decompressed_len])
```

### 5.2 `AppleNVMeEAN.cpp` 与 `AppleNVMeEANUC.cpp` 内核断言与校验逻辑

在解压后的内核镜像偏移 `0x9e5a0f`–`0x9e67c9` 处，我们定位到内核驱动 `com.apple.driver.AppleNVMe` 的两个核心源文件：
- `/Library/Caches/com.apple.xbs/Sources/IONVMeFamily/Embedded/AppleNVMeEAN.cpp`
- `/Library/Caches/com.apple.xbs/Sources/IONVMeFamily/Embedded/AppleNVMeEANUC.cpp`

其中包含以下关键断言与错误字符串：

```c
// 1. UserClient 入参及可恢复性检查 (AppleNVMeEANUC.cpp)
assert(arguments->scalarInputCount == 3);
assert(fProvider->isImageDataRecoverable(key) != UNRECOVERABLE);

// 2. 容量边界检查 (AppleNVMeEAN.cpp)
assert(fIsFormatted);
assert((fHeader->totalBytesWritten + (size_t)(inImageSize - fHeader->image[inImageIndex].imageSize)) <= fHeader->totalSize);

// 3. 覆写/擦除已有键值时的镜像完整性校验 (AppleNVMeEAN.cpp)
// 当覆写已存在的 ifw2 时，驱动会调用 eraseImage() -> verifyImages() 检查现有镜像的 CRC32 并执行 NAND Unmap：
"nvme: EAN verifyImages() failed during eraseImage()\n"
"nvme: Image checksum failed. ExpectedCRC = 0x%x @ OldOffset = 0x%x, inImageIndex = %d, ErrorCRC = 0x%x @ NewOffset = 0x%x, key = 0x%x, size=0x%x unmapOffset = 0x%x, unmapSize = 0x%x \n"
"nvme: EAN Error: failed to query the unmap states of image index = %d\n"
```

### 5.3 结论：为什么「存储写满 100% 卡死」会导致 `ifw2` 永久无法覆写？

1. 当手机此前因存储空间 100% 写满而卡死断电时，EAN 分区中的暂存槽位 `ifw2`（或 `fHeader` 中的 `totalBytesWritten` / 镜像 CRC）处于半写入或损坏状态，抑或底层 ANS2 闪存控制器的 FTL 因满盘耗尽预留块（OP 区）而将该引导块锁入只读/不可恢复状态（`UNRECOVERABLE`）。
2. 刷机时，`format_ean_storage` 只检查 `fIsFormatted`（`fHeader->signature` 有效），因此跳过 `selector = 1`（重新格式化整个 EAN 分区头），只执行了 `selector = 9`（清理非 `ifw1`/`ifw2` 的普通图像）。
3. 随后在 `update_iBoot` 阶段，无论向 `ifw2` 写入 7.81 MB 还是 32 KB，`AppleNVMeEAN::eanWrite` 在尝试覆写已存在的 `ifw2` 条目时，都会触发对现有 `ifw2` 镜像的 `eraseImage()` / `verifyImages()` 校验或底层 ANS2 Unmap/Write 操作，并因旧 `ifw2` 的 CRC 损坏或物理块锁死而立即返回 `0xe00002e9` (`kIOReturnDeviceError`)。

