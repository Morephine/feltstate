#!/usr/bin/env python3
"""hotkey_pad — global F-keys to virtual-pad taps, for menus and lobbies.

Keep the game focused and drive player 2's menus from the keyboard:

  F6 -> A (confirm)     F7 -> B (cancel)
  F8/F9 -> D-pad left/right    F10/F11 -> D-pad up/down
  F5 -> Start           F12 -> quit this script

Windows only (vgamepad/ViGEm + the `keyboard` package).
"""

from __future__ import annotations

import sys
import time

try:
    import keyboard
    import vgamepad as vg
except ImportError:
    print("pip install vgamepad keyboard   (Windows only; ViGEm driver required)")
    sys.exit(1)

BTN = vg.XUSB_BUTTON


def main() -> None:
    gp = vg.VX360Gamepad()
    print("[hotkey] virtual gamepad ready")

    def tap(btn):
        gp.press_button(button=btn)
        gp.update()
        time.sleep(0.12)
        gp.release_button(button=btn)
        gp.update()
        print(f"[hotkey] tapped {btn}")

    keyboard.add_hotkey("f6", lambda: tap(BTN.XUSB_GAMEPAD_A))
    keyboard.add_hotkey("f7", lambda: tap(BTN.XUSB_GAMEPAD_B))
    keyboard.add_hotkey("f8", lambda: tap(BTN.XUSB_GAMEPAD_DPAD_LEFT))
    keyboard.add_hotkey("f9", lambda: tap(BTN.XUSB_GAMEPAD_DPAD_RIGHT))
    keyboard.add_hotkey("f10", lambda: tap(BTN.XUSB_GAMEPAD_DPAD_UP))
    keyboard.add_hotkey("f11", lambda: tap(BTN.XUSB_GAMEPAD_DPAD_DOWN))
    keyboard.add_hotkey("f5", lambda: tap(BTN.XUSB_GAMEPAD_START))

    print("[hotkey] F6=A  F7=B  F8/F9=Left/Right  F10/F11=Up/Down  F5=Start  F12=quit")
    keyboard.wait("f12")
    gp.reset()
    gp.update()
    print("[hotkey] bye")


if __name__ == "__main__":
    main()
