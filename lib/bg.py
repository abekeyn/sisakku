# -*- coding: utf-8 -*-
"""バックグラウンドタスク：重い処理を別スレッドで裏で実行し、画面はすぐ次の操作に進める。

使い方（Home.py）
    bg.submit("請求書3件を確定", fn, 引数…, lock="billing")   # すぐ戻る。処理は裏で進む
    bg.snapshot()                                           # 実行中・最近終わったタスクの一覧

- 複数のタスクを同時に（並列で）走らせられる。
- 同じデータ（lock名が同じ）を書き換えるタスクは、壊れないように順番に実行する。
  例：請求書・領収書は大きな1つのデータを丸ごと読み書きするので "billing" で直列、
  出荷・注文は "orders" で直列。ロック名が違うタスク同士は本当に同時に動く。
- fn は Streamlit の画面（st.*）に触れないこと。結果はメッセージ（文字列、または
  (成功か, メッセージ) のタプル）で返す。画面側が定期的に一覧を見て表示する。
- タスクはこのサーバのプロセス内で動く。再起動（再デプロイ等）で消える。
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bg")
_TASKS: dict[str, dict] = {}
_LOCKS: dict[str, threading.RLock] = {}
_L = threading.RLock()
_KEEP_SEC = 600          # 終わったタスクを一覧に残す時間


def lock(name: str) -> threading.RLock:
    """資源（"billing" など）ごとの再入可能ロック。タスクの外から同じデータを書く処理もこれで囲める。"""
    with _L:
        return _LOCKS.setdefault(name, threading.RLock())


def submit(label: str, fn, *args, lock_name: str = "", kind: str = "", **kwargs) -> str:
    """タスクを裏で実行する。タスクIDを返す（すぐ戻る）。"""
    tid = uuid.uuid4().hex[:8]
    t = {"id": tid, "label": label, "status": "queued", "msg": "", "ok": None, "kind": kind,
         "t0": time.time(), "t1": None, "seen": False, "result": None, "progress": ""}
    with _L:
        _TASKS[tid] = t

    def _run():
        lk = lock(lock_name) if lock_name else None
        try:
            if lk:
                lk.acquire()
            t["status"] = "running"
            t["t_run"] = time.time()
            res = fn(*args, **kwargs)
            ok = True
            if isinstance(res, tuple) and len(res) >= 2:
                ok, msg = bool(res[0]), str(res[1])
                t["result"] = res[2] if len(res) > 2 else None
            elif isinstance(res, dict):
                ok, msg = bool(res.get("ok", True)), str(res.get("msg", ""))
                t["result"] = res
            else:
                msg = "" if res is None else str(res)
            t["ok"], t["msg"] = ok, msg
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            t["ok"], t["msg"] = False, f"失敗しました：{type(e).__name__}: {e}"
        finally:
            t["status"] = "done" if t["ok"] else "error"
            t["t1"] = time.time()
            if lk:
                try:
                    lk.release()
                except RuntimeError:
                    pass

    _POOL.submit(_run)
    return tid


def set_progress(tid: str, text: str) -> None:
    t = _TASKS.get(tid)
    if t:
        t["progress"] = text


def snapshot() -> list[dict]:
    """新しい順のタスク一覧（コピー）。古い完了分は消す。"""
    now = time.time()
    with _L:
        for k in [k for k, t in _TASKS.items() if t["t1"] and now - t["t1"] > _KEEP_SEC and t["seen"]]:
            _TASKS.pop(k, None)
        return sorted((dict(t) for t in _TASKS.values()), key=lambda t: -t["t0"])


def running_count() -> int:
    with _L:
        return sum(1 for t in _TASKS.values() if t["status"] in ("queued", "running"))


def mark_seen(ids) -> None:
    with _L:
        for i in ids:
            if i in _TASKS:
                _TASKS[i]["seen"] = True


def dismiss(tid: str) -> None:
    with _L:
        _TASKS.pop(tid, None)


def take_result(kind: str):
    """kind のタスクで、まだ受け取っていない最新の結果を1つ取り出す（なければ None）。"""
    with _L:
        done = [t for t in _TASKS.values() if t["kind"] == kind and t["t1"] and not t.get("taken")]
        if not done:
            return None
        t = max(done, key=lambda t: t["t1"])
        t["taken"] = True
        return t["result"]
