# -*- coding: utf-8 -*-
"""常駐エージェントの親（見張り役）。agent.py --watch を起動し、終了したらすぐ起動し直す。

エージェントが原因不明で止まり、印刷・送り状発行・領収書の保存が何時間も待たされたことがあった
（2026-11-06・11-10）。このプログラムがエージェントの終了を見張り、数秒で自動的に立ち上げ直す。
終了の理由（終了コードと時刻）は data/agent_supervisor.log に残す。二重起動はしない。
スタートアップ登録.bat は、これを起動する（PC起動のたびに自動で常駐）。
"""
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "data" / "agent_supervisor.log"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 200_000:
            LOG.write_text("", encoding="utf-8")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def main() -> None:
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 48731))      # 二重起動の防止（持っている間は他が起動できない）
    except OSError:
        return
    py = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    fast = 0
    _log("見張り役を開始")
    while True:
        t0 = time.time()
        p = subprocess.Popen([str(py), str(ROOT / "agent.py"), "--watch"], cwd=str(ROOT),
                             creationflags=NO_WINDOW)
        code = p.wait()
        ran = time.time() - t0
        _log(f"エージェントが終了（終了コード {code}・{ran:.0f}秒動作）→ 起動し直します")
        fast = fast + 1 if ran < 20 else 0     # すぐ落ちる状態が続くときは間隔を広げる
        time.sleep(min(5 * (2 ** min(fast, 5)), 120))


if __name__ == "__main__":
    main()
