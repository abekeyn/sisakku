# -*- coding: utf-8 -*-
"""バックグラウンドタスク：重い処理を別スレッドで裏で実行し、画面はすぐ次の操作に進める。

使い方（Home.py）
    bg.submit("請求書3件を確定", fn, 引数…, lock_name="billing")   # すぐ戻る。処理は裏で進む
    bg.snapshot()                                                 # 実行中・最近終わったタスクの一覧

- 複数のタスクを同時に（並列で）走らせられる。
- 同じデータ（lock名が同じ）を書き換えるタスクは、壊れないように順番に実行する。
  例：請求書・領収書は大きな1つのデータを丸ごと読み書きするので "billing" で直列、
  出荷・注文は "orders" で直列。ロック名が違うタスク同士は本当に同時に動く。
- fn は Streamlit の画面（st.*）に触れないこと。結果はメッセージ（文字列、または
  (成功か, メッセージ) のタプル）で返す。画面側が定期的に一覧を見て表示する。
- 失敗（例外）は、原因（エラーの種類・内容・発生場所）を結果に残す。
- タスクはこのサーバのプロセス内で動く。サーバが再起動して途中で消えた場合も、
  DBの記録（bg_task_*）から検出して「途中で終了」として画面に出せる（recover）。
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bg")
_TASKS: dict[str, dict] = {}
_LOCKS: dict[str, threading.RLock] = {}
_L = threading.RLock()
_KEEP_SEC = 600          # 終わったタスクを一覧に残す時間
_BOOT = uuid.uuid4().hex[:10]   # このプロセスの識別子（再起動すると変わる）
_REC = "bg_task_"        # DBに残す記録のキー接頭辞（実行中・失敗した分だけ）


def lock(name: str) -> threading.RLock:
    """資源（"billing" など）ごとの再入可能ロック。タスクの外から同じデータを書く処理もこれで囲める。"""
    with _L:
        return _LOCKS.setdefault(name, threading.RLock())


def _persist(t: dict, detail: str = "") -> None:
    """実行中・失敗の記録をDBへ。画面の再読み込みやサーバ再起動のあとでも見られるように。"""
    try:
        from . import db
        db.set_setting(_REC + t["id"], {
            "id": t["id"], "label": t["label"], "status": t["status"], "boot": _BOOT,
            "msg": t["msg"], "detail": detail, "at": datetime.now().isoformat(timespec="seconds")})
    except Exception:  # noqa: BLE001  記録に失敗しても本処理は止めない
        pass


def _unpersist(tid: str) -> None:
    try:
        from . import db
        db.delete_setting(_REC + tid)
    except Exception:  # noqa: BLE001
        pass


def _short_error(e: BaseException) -> tuple[str, str]:
    """(画面に出す1行, 詳しい内容)。発生場所（最後の数フレーム）も付ける。"""
    tb = traceback.extract_tb(e.__traceback__)
    where = ""
    for fr in reversed(tb):
        if "site-packages" not in fr.filename and "threading" not in fr.filename:
            where = f"{fr.filename.replace(chr(92), '/').split('/')[-1]}:{fr.lineno}（{fr.name}）"
            break
    one = f"{type(e).__name__}: {e}"
    if where:
        one += f"　［{where}］"
    return one, "".join(traceback.format_exception(type(e), e, e.__traceback__))[-3000:]


def submit(label: str, fn, *args, lock_name: str = "", kind: str = "", **kwargs) -> str:
    """タスクを裏で実行する。タスクIDを返す（すぐ戻る）。"""
    tid = uuid.uuid4().hex[:8]
    t = {"id": tid, "label": label, "status": "queued", "msg": "", "ok": None, "kind": kind,
         "t0": time.time(), "t1": None, "seen": False, "result": None, "progress": "", "detail": ""}
    with _L:
        _TASKS[tid] = t

    def _run():
        lk = lock(lock_name) if lock_name else None
        try:
            if lk:
                lk.acquire()
            t["status"] = "running"
            t["t_run"] = time.time()
            if kind != "agent_send":
                _persist(t)
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
        except BaseException as e:  # noqa: BLE001
            traceback.print_exc()
            one, detail = _short_error(e)
            t["ok"], t["msg"], t["detail"] = False, f"途中でエラーになりました：{one}", detail
        finally:
            t["status"] = "done" if t["ok"] else "error"
            t["t1"] = time.time()
            if kind != "agent_send":
                if t["ok"]:
                    _unpersist(tid)
                else:
                    _persist(t, t["detail"])     # 失敗は閉じるまで記録を残す
            if lk:
                try:
                    lk.release()
                except RuntimeError:
                    pass

    _POOL.submit(_run)
    return tid


def recover(known_ids=()) -> list[dict]:
    """DBに残っている失敗・途中終了の記録を返す（画面を開いたとき1回呼ぶ）。

    前のプロセスで「実行中」のまま残っているもの＝サーバの再起動などで途中で消えたタスクは、
    「途中で終了」として記録し直して返す。known_ids は画面ですでに表示済みのID。
    """
    out = []
    try:
        from . import db
        for key in db.list_setting_keys(_REC):
            rec = db.get_setting_live(key) or {}
            tid = rec.get("id") or key[len(_REC):]
            if tid in known_ids or tid in _TASKS:
                continue
            if rec.get("status") == "running" or rec.get("status") == "queued":
                if rec.get("boot") == _BOOT:
                    continue                       # このプロセスで実行中（_TASKSにあるはず）
                rec["status"] = "error"
                rec["msg"] = ("途中で終了しました（サーバが再起動された等で、処理が最後まで終わっていません）。"
                              "結果を確認して、必要なら操作をやり直してください。")
                rec["detail"] = ""
                db.set_setting(key, rec)
            if rec.get("status") == "error":
                out.append({"id": tid, "label": rec.get("label", ""), "ok": False,
                            "msg": rec.get("msg", ""), "detail": rec.get("detail", ""),
                            "at": rec.get("at", "")})
    except Exception:  # noqa: BLE001
        pass
    return out


def acknowledge(tid: str) -> None:
    """失敗の表示を閉じたときに、DBの記録も消す。"""
    _unpersist(tid)


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
