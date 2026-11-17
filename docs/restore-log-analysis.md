# `iPhoneUpdater.log` 恢复日志关键时间线与检查点解读

在 macOS 上通过 Finder 恢复 iPhone 时，详细日志保存在：
`~/Library/Logs/iPhone Updater Logs/iPhoneUpdater.log`（及递增编号文件）。

以下是本次故障中单次恢复（总耗时 59 秒，停留在进度 5%）的关键检查点轨迹：

---

## 1. 设备识别与初始状态 (`T+0s`)

```text
AMPDevicesAgent: 1.7.0.163
AMPDevicesAgent: Apple Mobile Device version: 1860.1.5
AMPDevicesAgent: Software payload version: 24B156
Device info:
	Board ID: 0x8
	Chip ID: 0x8140
	ECID: 0x1574**********
	Serial Number: GXT*******
	Starting State: Recovery
	Production Fused: YES
```
- `Chip ID: 0x8140` + `Board ID: 0x8` 对应 `d47ap`（iPhone 16，`iPhone17,3`）。
- 匹配的刷机变体（Variant）为 `Customer Erase Install (IPSW)`。

---

## 2. 引导 `iBEC` 与 20 个恢复期引导组件 (`T+3s ~ T+19s`)

```text
[20:40:56.9497] bootstrapping restore with iBEC
[20:40:56.9549] sending file: .../Firmware/dfu/iBEC_iBEC.d47.RELEASE.img4
[20:41:04.1630] iBoot build-version = mBoot-20457.42.5
[20:41:04.2494] Sending RestoreANS to device. (1 of 20)
[20:41:04.8756] Sending iBootData to device. (2 of 20)
[20:41:04.8843] Sending RestoreDCP to device. (3 of 20)
...
[20:41:12.xxxx] Sending RestoreRamDisk / RestoreKernelCache
```
- 注意：在这一阶段，Mac 通过 USB 发送了 `RestoreDCP` 和 `RestoreLogo` 到手机内存，所以在进入 RestoreOS 内存盘的瞬间，手机屏幕能够点亮并显示苹果 Logo 与进度条。

---

## 3. NAND 格式化与 EAN 清理 (`T+49s`)

手机进入 RestoreOS (`restored_update`) 后，依次执行检查点：

- `[0x064E] clean_nand`：`NAND format complete`（主 APFS 存储分区格式化成功）
- `[0x061F] format_effaceable_storage`：`Device does not support effaceable storage. Skipping effaceable format.`
- `[0x068A] format_ean_storage`：
  ```text
  entering format_ean_storage
  EAN storage is already formatted
  Cleaning EAN images
  EAN storage cleaned successfully
  ```
- `[0x0621] create_filesystem_partitions`：`APFS Erase Install` 成功创建新分区。
- 随后通过 `apfs_invert_asr_img` 成功传输并写入了 **10,311,696,384 字节（10.31 GB）** 的系统根镜像！

---

## 4. 固件更新与 `0x1300` (`update_iBoot`) 致命错误 (`T+54s`)

在 `[0x0635] update_device_firmware` -> `[0x1300] update_iBoot` 阶段：

1. 手机向 Mac 请求 6 个 `IsiBootEANFirmware` 文件：
   - `(1 of 6) WCHFirmwareUpdater` (246,361 bytes)
   - `(2 of 6) RecoveryMode` (2,637,278 bytes)
   - `(3 of 6) Liquid` (1,048,613 bytes)
   - `(4 of 6) DCP` (2,586,688 bytes)
   - `(5 of 6) Ap,LowPowerMode` (1,265,075 bytes)
   - `(6 of 6) AppleLogo` (30,739 bytes)
2. 手机端同时完成了电池固件 `BatFWUpdater` (`Veridian`) 的校验与更新。
3. 随后执行 `Writing out IsiBootEANFirmware files to EAN iBoot keys.`：
   ```text
   Reading EAN key ifw1.
   read failed, kernResult = 0xe00002e9.
   Key not found.
   Reading EAN key ifw2.
   Will use Generation 3.
   Reading EAN key ifw2.
   ifw2 hash does not match boot hash - using ifw2 to stage new data.
   Writing EAN key ifw2, (1 of 1) imageSize=7815168
   _write_ean_image failed kernResult=0xe00002e9
   Failed to write to EAN namespace
   _write_ibfw failed.
   Update[iBoot] error - Error Domain=RamrodErrorDomain Code=7 "Generic updater error: -1"
   CHECKPOINT FAILURE:(FAILURE:-1) FIRMWARE:[0x1300] update_iBoot [0]D(Generic updater error: -1)
   ```
4. 最终生成检查点指纹 `1300.FFFF`（`0x1300` = `update_iBoot`，`0xFFFF` = `-1`）：
   ```text
   Restore completed, status: 7
   Elapsed time (in seconds): 59
   Restore Checkpoint Fingerprint: 1300.FFFF
   Failure Description:
   Depth:0 Code:-1 Error:AMRestorePerformRestoreModeRestoreWithError failed with error: -1
   Depth:1 Code:-1 Error:Failed to handle message type StatusMsg (Generic error)
   Depth:2 Code:-1 Error:failed to update device firmware
   Depth:3 Code:-1 Error:ramrod firmware update failed
   Depth:4 Code:7 Error:Generic updater error: -1
   ```
