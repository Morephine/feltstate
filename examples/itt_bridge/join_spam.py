#!/usr/bin/env python3
"""join_spam — hold the door: spam the A button until player 2 has joined.

The join dance (README, "run order"): run this, walk the game to its
"waiting for player 2" screen, and the emulated pad joins by itself.
Ctrl-C once the second seat is filled. Windows only (vgamepad/ViGEm).
"""

from __future__ import annotations

import sys
import time

try:
    import vgamepad as vg
except ImportError:
    print("pip install vgamepad   (Windows only; ViGEm driver required)")
    sys.exit(1)


def main() -> None:
    gp = vg.VX360Gamepad()
    print("[spam] virtual gamepad ready — spamming A every 0.5s, Ctrl-C to stop")
    cnt = 0
    try:
        while True:
            cnt += 1
            gp.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
            gp.update()
            time.sleep(0.15)
            gp.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
            gp.update()
            time.sleep(0.35)
            if cnt % 10 == 0:
                print(f"  spammed A x {cnt}")
    except KeyboardInterrupt:
        print(f"\n[spam] stopped after {cnt} presses")
        gp.reset()
        gp.update()


if __name__ == "__main__":
    main()
