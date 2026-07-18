#!/usr/bin/env python3
"""pad_daemon — a virtual X360 pad as a local HTTP tool service (Windows only).

The companion is the brain; this daemon only exposes tools. It holds no
policy and runs no decision loop — the agent composes calls turn by turn
(see docs/GAME_SHELL.md §7 and this folder's README).

    python pad_daemon.py                 # GAME_PAD_PORT overrides the port

Endpoints (POST bodies are JSON; empty body is fine):
  GET  /status                  daemon health (pad alive / process attached)
  POST /button {btn, hold_ms?}  tap a button (A/B/X/Y/START/BACK/LB/RB/
                                UP/DOWN/LEFT/RIGHT/LSTICK/RSTICK)
  POST /button_down {btn}       press and hold (release with /button_up)
  POST /button_up {btn}
  POST /stick {side, x, y}      set a stick, x/y in [-1.0, 1.0]
  POST /spam {btn, count?, interval_ms?}   tap N times, async
  POST /screenshot              save the primary screen to a PNG -> {path}
  GET  /state                   live partner telemetry via the beacon scan
  POST /quit                    release the pad and exit

Third-party foundations (not this project's design): vgamepad/ViGEm for the
emulated pad, pymem for process-memory access. What's ours is the shape —
tool daemon + the beacon-scan telemetry channel (README, rung 2).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import Structure, byref, c_size_t, c_ulong, c_void_p, sizeof, windll
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import numpy as np
    import pymem
    import vgamepad as vg
except ImportError:
    print("pip install vgamepad pymem numpy   (Windows only; ViGEm driver required)")
    sys.exit(1)

PORT = int(os.environ.get("GAME_PAD_PORT", "8093"))
PROCESS_NAME = os.environ.get("GAME_PROCESS", "ItTakesTwo.exe")
SCREEN_PNG = os.environ.get(
    "GAME_SCREEN_PNG", os.path.join(tempfile.gettempdir(), "pad_screen.png")
)

BTN = vg.XUSB_BUTTON
BUTTON_MAP = {
    "A": BTN.XUSB_GAMEPAD_A,
    "B": BTN.XUSB_GAMEPAD_B,
    "X": BTN.XUSB_GAMEPAD_X,
    "Y": BTN.XUSB_GAMEPAD_Y,
    "START": BTN.XUSB_GAMEPAD_START,
    "BACK": BTN.XUSB_GAMEPAD_BACK,
    "LB": BTN.XUSB_GAMEPAD_LEFT_SHOULDER,
    "RB": BTN.XUSB_GAMEPAD_RIGHT_SHOULDER,
    "LSTICK": BTN.XUSB_GAMEPAD_LEFT_THUMB,
    "RSTICK": BTN.XUSB_GAMEPAD_RIGHT_THUMB,
    "UP": BTN.XUSB_GAMEPAD_DPAD_UP,
    "DOWN": BTN.XUSB_GAMEPAD_DPAD_DOWN,
    "LEFT": BTN.XUSB_GAMEPAD_DPAD_LEFT,
    "RIGHT": BTN.XUSB_GAMEPAD_DPAD_RIGHT,
}

# ── state ──────────────────────────────────────────────────────────────────
gp = vg.VX360Gamepad()
exit_flag = False
pm = None  # pymem handle once attached
beacon_addrs = None  # locked telemetry addresses, or None until the scan succeeds
left_stick = (0, 0)
right_stick = (0, 0)
state_lock = threading.Lock()

# Beacon-scan telemetry (README rung 2). The engine-side script publishes each
# value as `beacon + value`, so live floats are findable BY MAGNITUDE in any
# game build — no version-specific offsets to rot. Moving axes are kept when
# they *change* between two snapshots; the yaw beacon uses a tight tolerance
# and a stability check instead.
BEACON_SPEC = [
    # (axis, beacon, tolerance, expect_changing)
    ("x", 7_000_000, 200_000, True),
    ("y", 8_000_000, 200_000, True),
    ("z", 9_000_000, 200_000, True),
    ("yaw", 10_000_000, 500, False),
]
BEACON_CENTERS = {name: center for name, center, _, _ in BEACON_SPEC}


class _MBI(Structure):
    _fields_ = [
        ("BaseAddress", c_void_p),
        ("AllocationBase", c_void_p),
        ("AllocationProtect", c_ulong),
        ("__a1", c_ulong),
        ("RegionSize", c_size_t),
        ("State", c_ulong),
        ("Protect", c_ulong),
        ("Type", c_ulong),
        ("__a2", c_ulong),
    ]


def _scan_addrs(pm):
    """Walk committed R/W regions; collect float32 candidates near each beacon,
    then keep the ones whose change/stability matches, preferring adjacent
    pairs (real engine values tend to sit next to their neighbours)."""
    kernel32 = windll.kernel32
    MEM_COMMIT = 0x1000
    PAGE_RW = 0x04 | 0x40
    cands = {name: [] for name, *_ in BEACON_SPEC}
    addr = 0
    mbi = _MBI()
    while addr < 0x7FFFFFFFFFFF:
        rc = kernel32.VirtualQueryEx(pm.process_handle, c_void_p(addr), byref(mbi), sizeof(mbi))
        if rc == 0:
            break
        if mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_RW):
            if mbi.RegionSize < 256 * 1024 * 1024:
                try:
                    chunk = pm.read_bytes(mbi.BaseAddress, mbi.RegionSize)
                    arr = np.frombuffer(chunk[: (len(chunk) // 4) * 4], dtype=np.float32)
                    for name, target, tol, _ in BEACON_SPEC:
                        mask = np.abs(arr - target) < tol
                        for idx in np.where(mask)[0][:200]:
                            cands[name].append(mbi.BaseAddress + int(idx) * 4)
                except Exception:
                    pass
        addr = (mbi.BaseAddress or 0) + mbi.RegionSize
        if addr <= 0:
            break

    snap1 = {n: {a: pm.read_float(a) for a in cands[n][:300]} for n in cands}
    time.sleep(0.4)
    snap2 = {n: {a: pm.read_float(a) for a in snap1[n]} for n in snap1}
    addrs = {}
    for name, _t, _tol, expect_changing in BEACON_SPEC:
        s1, s2 = snap1[name], snap2[name]
        keep = (
            [a for a in s1 if a in s2 and s1[a] != s2[a]]
            if expect_changing
            else [a for a in s1 if a in s2 and s1[a] == s2[a]]
        )
        if len(keep) > 1:
            keep_set = set(keep)
            pair = next((a for a in keep if (a + 4) in keep_set), None)
            addrs[name] = pair if pair else keep[0]
        elif keep:
            addrs[name] = keep[0]
    return addrs if all(name in addrs for name, *_ in BEACON_SPEC) else None


def attach_watcher():
    """Background: attach to the game process, then lock the beacons.
    Re-tries forever; /state simply reports not-ready until it succeeds."""
    global pm, beacon_addrs
    while not exit_flag:
        if pm is None:
            try:
                pm = pymem.Pymem(PROCESS_NAME)
                print(f"[ipc] attached {PROCESS_NAME}")
            except Exception:
                time.sleep(3)
                continue
        if beacon_addrs is None:
            print("[ipc] scanning beacons (~10s)...")
            addrs = _scan_addrs(pm)
            if addrs:
                beacon_addrs = addrs
                print(f"[ipc] beacons locked: {addrs}")
            else:
                time.sleep(5)
                continue
        time.sleep(5)


def read_partner_state():
    if not pm or not beacon_addrs:
        return None
    try:
        return {
            name: pm.read_float(beacon_addrs[name]) - BEACON_CENTERS[name]
            for name, *_ in BEACON_SPEC
        }
    except Exception:
        return None


def stick_worker():
    """Re-assert sticks every 50 ms — ViGEm lets an un-refreshed stick decay."""
    while not exit_flag:
        with state_lock:
            lx, ly = left_stick
            rx, ry = right_stick
        gp.left_joystick(x_value=lx, y_value=ly)
        gp.right_joystick(x_value=rx, y_value=ry)
        gp.update()
        time.sleep(0.05)


def capture_screen() -> bool:
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen(0,0,0,0,$bmp.Size);"
        f"$bmp.Save('{SCREEN_PNG}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose();"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps], timeout=8, capture_output=True
        )
        return r.returncode == 0
    except Exception:
        return False


# ── HTTP ───────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def _respond(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        raw = self.rfile.read(n).decode("utf-8", errors="replace").strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        if self.path == "/status":
            self._respond(
                200,
                {
                    "gamepad_alive": True,
                    "process_attached": pm is not None,
                    "beacons_locked": beacon_addrs is not None,
                },
            )
        elif self.path == "/state":
            self._respond(
                200,
                {"in_game": beacon_addrs is not None, "partner": read_partner_state()},
            )
        else:
            self._respond(404, {"error": "unknown endpoint"})

    def do_POST(self):
        global left_stick, right_stick, exit_flag
        body = self._body()
        if self.path == "/button":
            name = str(body.get("btn", "")).upper()
            if name not in BUTTON_MAP:
                self._respond(400, {"error": f"unknown btn, valid: {list(BUTTON_MAP)}"})
                return
            hold_ms = int(body.get("hold_ms", 120))
            btn = BUTTON_MAP[name]
            gp.press_button(button=btn)
            gp.update()
            time.sleep(hold_ms / 1000)
            gp.release_button(button=btn)
            gp.update()
            self._respond(200, {"ok": True, "btn": name, "hold_ms": hold_ms})
        elif self.path in ("/button_down", "/button_up"):
            name = str(body.get("btn", "")).upper()
            if name not in BUTTON_MAP:
                self._respond(400, {"error": "unknown btn"})
                return
            if self.path == "/button_down":
                gp.press_button(button=BUTTON_MAP[name])
            else:
                gp.release_button(button=BUTTON_MAP[name])
            gp.update()
            self._respond(200, {"ok": True, "btn": name})
        elif self.path == "/stick":
            side = str(body.get("side", "left")).lower()
            x = max(-1.0, min(1.0, float(body.get("x", 0))))
            y = max(-1.0, min(1.0, float(body.get("y", 0))))
            xv, yv = int(x * 32767), int(y * 32767)
            with state_lock:
                if side == "left":
                    left_stick = (xv, yv)
                else:
                    right_stick = (xv, yv)
            self._respond(200, {"ok": True, "side": side, "x": x, "y": y})
        elif self.path == "/spam":
            name = str(body.get("btn", "A")).upper()
            count = int(body.get("count", 10))
            interval_ms = int(body.get("interval_ms", 350))
            if name not in BUTTON_MAP:
                self._respond(400, {"error": "unknown btn"})
                return
            btn = BUTTON_MAP[name]

            def _spam():
                for _ in range(count):
                    if exit_flag:
                        return
                    gp.press_button(button=btn)
                    gp.update()
                    time.sleep(0.12)
                    gp.release_button(button=btn)
                    gp.update()
                    time.sleep(interval_ms / 1000)

            threading.Thread(target=_spam, daemon=True).start()
            self._respond(200, {"ok": True, "btn": name, "count": count, "async": True})
        elif self.path == "/screenshot":
            if capture_screen():
                self._respond(200, {"ok": True, "path": SCREEN_PNG})
            else:
                self._respond(500, {"error": "capture failed"})
        elif self.path == "/quit":
            self._respond(200, {"ok": True, "msg": "shutting down"})
            exit_flag = True
            threading.Timer(1, lambda: os._exit(0)).start()
        else:
            self._respond(404, {"error": "unknown endpoint"})


def main():
    print("=" * 60)
    print(f"pad_daemon — port {PORT} · process {PROCESS_NAME}")
    print("=" * 60)
    threading.Thread(target=attach_watcher, daemon=True).start()
    threading.Thread(target=stick_worker, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
