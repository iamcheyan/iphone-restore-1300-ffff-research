#!/usr/bin/env python3
"""
当 iPhone 因 EAN 分区被清空而导致恢复模式下屏幕全黑时，
通过 80ms 间隔轮询 macOS ioreg USB 状态并使用中文语音合成 (say) 进行实时读秒引导。
"""
import subprocess
import time

VOICE = "Tingting (中国語（中国本土）)"


def say_async(text: str, rate: int = 230):
    return subprocess.Popen(["say", "-v", VOICE, "-r", str(rate), text])


def say_sync(text: str, rate: int = 210):
    subprocess.run(["say", "-v", VOICE, "-r", str(rate), text])


def get_usb_state() -> str:
    out = subprocess.run(
        ["ioreg", "-p", "IOUSB", "-l", "-w", "0"],
        capture_output=True,
        text=True,
    ).stdout
    if '"idProduct" = 4647' in out or "(DFU Mode)" in out:
        return "DFU"
    if '"idProduct" = 4737' in out or "(Recovery Mode)" in out:
        return "RECOVERY"
    if '"idProduct" = 4780' in out or '"kUSBProductString" = "iPhone"' in out:
        return "IPHONE"
    return "NONE"


def main():
    print("Starting Chinese DFU voice assistant...", flush=True)
    say_sync("语音助手已启动。现在请按一下音量加，按一下音量减，然后同时按住电源键和音量减键。", 210)

    start_time = time.time()
    last_state = get_usb_state()
    print(f"Initial USB state: {last_state}", flush=True)

    while time.time() - start_time < 180:
        state = get_usb_state()
        if state != last_state:
            print(f"[{time.strftime('%H:%M:%S')}] State changed: {last_state} -> {state}", flush=True)
            if state == "NONE":
                say_async("断开了！继续按住，三，二，一，快松开电源键！只按住音量减别松手！", 235)
                reconnected = "NONE"
                for _ in range(120):
                    time.sleep(0.1)
                    reconnected = get_usb_state()
                    if reconnected != "NONE":
                        break
                print(f"[{time.strftime('%H:%M:%S')}] Reconnected as: {reconnected}", flush=True)
                if reconnected == "DFU":
                    say_sync("太棒了！已经成功进入 D F U 模式，现在可以松开所有按键了！", 205)
                    print("SUCCESS: DFU MODE REACHED!", flush=True)
                    break
                elif reconnected == "RECOVERY":
                    say_sync("这次还是恢复模式，可以先松手了。", 205)
                last_state = reconnected
                continue
            elif state == "DFU":
                say_sync("太棒了！已经成功进入 D F U 模式，可以松手了！", 205)
                print("SUCCESS: DFU MODE REACHED!", flush=True)
                break
            last_state = state
        time.sleep(0.08)


if __name__ == "__main__":
    main()
