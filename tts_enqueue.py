"""Client for the TTS daemon.

Starts the daemon if it isn't running, then sends one command over the
Unix socket and exits. Used by speak.sh and the queue helper scripts.

Usage:
  tts_enqueue.py enqueue "<text>" --voice af_heart --speed 1.0
  tts_enqueue.py flush
  tts_enqueue.py stop
  tts_enqueue.py ping
  tts_enqueue.py shutdown
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCKET_PATH = "/tmp/tts-daemon.sock"
PID_PATH = "/tmp/tts-daemon.pid"
SKILL_DIR = Path(__file__).parent
DAEMON_SCRIPT = SKILL_DIR / "tts_daemon.py"
KOKORO_PYTHON = Path.home() / ".claude" / "tts-venv" / "bin" / "python"


def daemon_alive() -> bool:
    if not os.path.exists(PID_PATH) or not os.path.exists(SOCKET_PATH):
        return False
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def start_daemon() -> None:
    # Detach from this process so it survives our exit
    log = open("/tmp/tts-daemon.stderr", "a")
    subprocess.Popen(
        [str(KOKORO_PYTHON), str(DAEMON_SCRIPT)],
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def wait_for_socket(timeout: float = 60.0) -> bool:
    """Wait for the daemon's socket to appear *and* accept a ping.

    Model load takes a few seconds on first start, so we have to be
    patient. We don't block on synthesis though — once the daemon
    answers ping, enqueue returns immediately even if synth is still
    spinning up.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(SOCKET_PATH):
            try:
                send_and_recv({"cmd": "ping"}, timeout=1.0)
                return True
            except OSError:
                pass
        time.sleep(0.1)
    return False


def send_and_recv(msg: dict, timeout: float = 5.0) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCKET_PATH)
    s.sendall((json.dumps(msg) + "\n").encode("utf-8"))
    # Read one line of reply
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.decode("utf-8")) if buf else {}


def ensure_daemon() -> None:
    if daemon_alive():
        return
    # Clean up stale socket/pid before starting
    for p in (SOCKET_PATH, PID_PATH):
        try:
            if os.path.exists(p):
                os.unlink(p)
        except OSError:
            pass
    start_daemon()
    if not wait_for_socket():
        print("Failed to start TTS daemon", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_enq = sub.add_parser("enqueue")
    ap_enq.add_argument("text")
    ap_enq.add_argument("--voice", default="af_heart")
    ap_enq.add_argument("--speed", type=float, default=1.0)

    sub.add_parser("flush")
    sub.add_parser("stop")
    sub.add_parser("ping")
    sub.add_parser("shutdown")

    args = ap.parse_args()

    # Shutdown/ping don't auto-start the daemon
    if args.cmd in ("shutdown", "ping"):
        if not daemon_alive():
            print(json.dumps({"ok": False, "error": "daemon not running"}))
            sys.exit(0 if args.cmd == "ping" else 0)
        msg = {"cmd": args.cmd}
        print(json.dumps(send_and_recv(msg)))
        return

    ensure_daemon()

    if args.cmd == "enqueue":
        msg = {
            "cmd": "enqueue",
            "text": args.text,
            "voice": args.voice,
            "speed": args.speed,
        }
    else:
        msg = {"cmd": args.cmd}
    print(json.dumps(send_and_recv(msg)))


if __name__ == "__main__":
    main()
