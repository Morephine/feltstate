#!/usr/bin/env python3
"""brain_demo — canned walk/stop/jump proof that the pad moves the character.

With player 2 joined (join_spam.py), run this, refocus the game within the
countdown, and the second character walks 4s / stops 2s / jumps every third
cycle. It exists to verify the actuator before any real brain sits on it —
the real session replaces this loop with the companion composing
pad_daemon.py calls turn by turn. Windows only (vgamepad/ViGEm).
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
    print("[brain] virtual gamepad ready — refocus the game! 5s")
    for i in range(5, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    print("[brain] demo loop start, Ctrl-C to quit")

    cycle = 0
    try:
        while True:
            cycle += 1
            print(f"[brain] cycle {cycle}: WALK 4s")
            gp.left_joystick_float(x_value_float=0.0, y_value_float=1.0)
            gp.update()
            time.sleep(4)

            print(f"[brain] cycle {cycle}: STOP 2s")
            gp.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
            gp.update()
            time.sleep(2)

            if cycle % 3 == 0:
                print(f"[brain] cycle {cycle}: JUMP")
                gp.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
                gp.update()
                time.sleep(0.2)
                gp.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
                gp.update()
                time.sleep(0.8)
    except KeyboardInterrupt:
        print("\n[brain] quitting, resetting pad")
        gp.reset()
        gp.update()


if __name__ == "__main__":
    main()
