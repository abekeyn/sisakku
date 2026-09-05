# -*- coding: utf-8 -*-
"""常駐エージェント（PCで実行）。

役割：
1. 予約されたヤマトCSVをデスクトップ『ヤマト出荷CSV』へ書き出す（6秒ごと）
2. アプリから「B2自動取得」の指示が来たら、ブラウザ自動操作で
   B2クラウドから発行済データを取得 → 照合 → 出荷完了 → BASE反映

B2クラウド操作（Playwright）はどれも「発行・印刷」「集荷依頼」「過去取得」の
別プロセス(python agent.py --b2 等)として起動し、常駐本体はそれを監視するだけ
にしている。PCのスリープ／ネットワーク不調でブラウザ自動操作がまれに応答不能
になることがあり、以前は常駐プロセス自体が巻き込まれて完全に停止し、手動で
プロセスを再起動するまで何もできなくなっていた（2026-09-05に実際に発生）。
別プロセス化しておけば、タイムアウトで確実に強制終了でき、常駐本体や他の
定期処理（CSV書き出し・請求同期等）は影響を受けずに動き続ける。

- 監視モード（常駐）:  python agent.py --watch
- 1回だけ実行:        python agent.py
- B2取得を今すぐ:     python agent.py --b2
"""
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import db, exporter  # noqa: E402

INTERVAL = 6  # 監視間隔（秒）
AGENT_PATH = str(Path(__file__).resolve())
AGENT_DIR = str(Path(__file__).resolve().parent)

# B2クラウド操作（Playwright）を子プロセスで実行する際の上限時間（秒）。
# 通常は1〜2分で終わるが、ハング時に手動介入なしで自己回復できるよう上限を設ける。
_JOB_TIMEOUTS = {
    "b2_fetch": 360,
    "b2_print": 360,
    "b2_pickup": 240,
    "b2_history": 900,
}
_JOBS: dict[str, dict] = {}  # name -> {"proc", "deadline", "result_key", "label"}


def _progress(key: str):
    """進捗をDBに書き込むコールバックを返す（アプリ側がポーリングして表示）。"""
    def cb(pct: int, step: str) -> None:
        db.set_setting(key, {"pct": int(pct), "step": step,
                             "at": datetime.now().isoformat(timespec="seconds")})
    return cb


def _kill_tree(pid: int) -> None:
    """子プロセスと、その配下で起動されたブラウザ等をまとめて強制終了する。"""
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15)
    except Exception:  # noqa: BLE001
        pass


def _launch_job(name: str, flag: str, result_key: str, label: str) -> None:
    """B2クラウド操作を別プロセスで起動する（既に実行中なら何もしない）。"""
    if name in _JOBS:
        return
    proc = subprocess.Popen(
        [sys.executable, AGENT_PATH, flag], cwd=AGENT_DIR,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _JOBS[name] = {"proc": proc, "deadline": time.time() + _JOB_TIMEOUTS[name],
                   "result_key": result_key, "label": label}


def _poll_jobs() -> None:
    """実行中のB2操作を確認し、終わっていれば片付け、超過していれば強制終了する。

    結果(*_result)自体は子プロセス側が完了時にDBへ書き込む。ここでは
    タイムアウト検知と後始末（プロセスツリーの強制終了・失敗結果の記録）だけ行う。
    """
    for name in list(_JOBS):
        job = _JOBS[name]
        proc = job["proc"]
        if proc.poll() is not None:
            del _JOBS[name]
            continue
        if time.time() > job["deadline"]:
            print(f'{job["label"]}：応答がないためタイムアウトで強制終了します', flush=True)
            _kill_tree(proc.pid)
            db.set_setting(job["result_key"], {
                "ok": False, "at": datetime.now().isoformat(timespec="seconds"),
                "summary": "タイムアウトしました（PCのスリープやネットワーク不調の可能性があります）。"
                           "もう一度お試しください。",
            })
            del _JOBS[name]


def _process_exports() -> int:
    written = exporter.process_pending()
    for p in written:
        print("書き出し:", p, flush=True)
    return len(written)


def _run_b2_fetch() -> None:
    """B2クラウドから発行済データを自動取得して出荷確定。結果をDBに記録。"""
    from lib import b2_fetch
    started = datetime.now().isoformat(timespec="seconds")
    prog = _progress("b2_fetch_progress")
    prog(5, "PCで処理を開始しました")
    try:
        r = b2_fetch.fetch_and_process(progress=prog)
        result = {
            "ok": True, "at": started,
            "summary": f'読込{r["rows"]}行／出荷確定{r["shipped"]}件／未照合{r["unmatched"]}行',
            "messages": r["messages"],
        }
        # 集荷依頼の個数の既定値に使う（今回出荷確定した件数）
        if r.get("shipped"):
            db.set_setting("last_shipped_count", r["shipped"])
        prog(100, "完了")
        print("B2取得 成功:", result["summary"], flush=True)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "at": started, "summary": f"失敗: {e}", "messages": []}
        prog(100, "失敗")
        print("B2取得 失敗:", e, flush=True)
        traceback.print_exc()
    db.set_setting("b2_fetch_result", result)


def _check_b2_request() -> bool:
    """アプリからの取得指示があれば実行する。"""
    req = db.get_setting("b2_fetch_request")
    done = db.get_setting("b2_fetch_handled")
    if req and req != done:
        db.set_setting("b2_fetch_handled", req)
        _launch_job("b2_fetch", "--b2", "b2_fetch_result", "伝票番号の取得・出荷確定")
        return True
    return False


def _run_b2_print() -> None:
    """B2で送り状を発行し、PDFを既定プリンタへ印刷する。"""
    import base64
    from lib import b2_fetch, printing
    started = datetime.now().isoformat(timespec="seconds")
    prog = _progress("b2_print_progress")
    prog(5, "PCで処理を開始しました")
    try:
        b64 = db.get_setting("b2_print_csv")
        if not b64:
            raise RuntimeError("印刷するデータがありません")
        csv_bytes = base64.b64decode(b64)
        r = b2_fetch.issue_and_print(csv_bytes, progress=prog)
        msg = r.get("message", "")
        if r.get("pdf"):
            prog(97, "プリンタへ送信中")
            ok, pmsg = printing.print_pdf(r["pdf"])
            msg += "／" + pmsg
        result = {"ok": bool(r.get("issued")), "at": started, "summary": msg}
        prog(100, "完了")
        print("B2発行・印刷:", msg, flush=True)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "at": started, "summary": f"失敗: {e}"}
        prog(100, "失敗")
        print("B2発行・印刷 失敗:", e, flush=True)
        traceback.print_exc()
    db.set_setting("b2_print_result", result)


def _check_b2_print() -> bool:
    """アプリからの印刷指示があれば実行する。"""
    req = db.get_setting("b2_print_request")
    done = db.get_setting("b2_print_handled")
    if req and req != done:
        db.set_setting("b2_print_handled", req)
        _launch_job("b2_print", "--b2-print", "b2_print_result", "送り状の発行・印刷")
        return True
    return False


def _run_b2_history() -> None:
    """ヤマトから過去の発行済データを取得し、顧客マスタ・過去注文を更新。"""
    from lib import b2_fetch
    started = datetime.now().isoformat(timespec="seconds")
    days = int(db.get_setting("b2_history_days") or 370)
    prog = _progress("b2_history_progress")
    prog(5, "PCで処理を開始しました")
    try:
        r = b2_fetch.fetch_history(days=days, progress=prog)
        result = {
            "ok": True, "at": started,
            "summary": f'顧客 +{r["customers"]}名／過去注文 +{r["orders"]}件 を取り込みました',
        }
        prog(100, "完了")
        print("過去取得:", result["summary"], flush=True)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "at": started, "summary": f"失敗: {e}"}
        prog(100, "失敗")
        print("過去取得 失敗:", e, flush=True)
        traceback.print_exc()
    db.set_setting("b2_history_result", result)


def _check_b2_history() -> bool:
    """アプリからの過去取得指示があれば実行する。"""
    req = db.get_setting("b2_history_request")
    done = db.get_setting("b2_history_handled")
    if req and req != done:
        db.set_setting("b2_history_handled", req)
        _launch_job("b2_history", "--b2-history", "b2_history_result", "過去データの取得")
        return True
    return False


def _run_b2_pickup() -> None:
    """ヤマトの集荷依頼を自動で行う。結果をDBに記録。"""
    from lib import b2_fetch
    started = datetime.now().isoformat(timespec="seconds")
    p = db.get_setting("b2_pickup_payload") or {}
    prog = _progress("b2_pickup_progress")
    prog(5, "PCで処理を開始しました")
    try:
        r = b2_fetch.request_pickup(
            date_label=p.get("date", ""), time_label=p.get("time", ""),
            count=int(p.get("count", 1)), note=p.get("note", ""),
            dry_run=bool(p.get("dry_run")),
            explore=bool(p.get("explore")), progress=prog,
        )
        result = {"ok": bool(r.get("ok")), "at": started, "summary": r.get("message", "")}
        prog(100, "完了")
        print("集荷依頼:", result["summary"], flush=True)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "at": started, "summary": f"失敗: {e}"}
        prog(100, "失敗")
        print("集荷依頼 失敗:", e, flush=True)
        traceback.print_exc()
    db.set_setting("b2_pickup_result", result)


def _check_b2_pickup() -> bool:
    """アプリからの集荷依頼指示があれば実行する。"""
    req = db.get_setting("b2_pickup_request")
    done = db.get_setting("b2_pickup_handled")
    if req and req != done:
        db.set_setting("b2_pickup_handled", req)
        _launch_job("b2_pickup", "--b2-pickup", "b2_pickup_result", "集荷依頼")
        return True
    return False


_granada_next_check = 0.0  # 次にグラナダ同期を確認するUNIX時刻（負荷軽減のため間引く）


def _check_granada_sync() -> None:
    """クラウドで送信済みのグラナダ請求書を、ローカルExcel台帳へ追記。
    DB側の synced_to_xlsx で冪等。5分ごとに確認（毎月の送信後に1回だけ追記される）。"""
    global _granada_next_check
    import time as _t
    if _t.time() < _granada_next_check:
        return
    _granada_next_check = _t.time() + 300
    try:
        from lib import billing
        synced = billing.sync_local_xlsx()
        for s in synced:
            print("請求書を台帳へ同期:", s.get("client"), s.get("sheet"), flush=True)
    except Exception as e:  # noqa: BLE001
        print("請求台帳同期をスキップ（次回再試行）:", e, flush=True)


_receipt_next_check = 0.0  # 次に領収書同期を確認するUNIX時刻（負荷軽減のため間引く）


def _check_receipt_sync() -> None:
    """クラウドで発行された領収書・給与明細PDFをローカルフォルダへ書き出す。

    領収書は各請求先の発行書類/○○様/、給与明細は発行書類/給与明細/。
    DB側の synced_to_folder で冪等。5分ごとに確認。"""
    global _receipt_next_check
    import time as _t
    if _t.time() < _receipt_next_check:
        return
    _receipt_next_check = _t.time() + 300
    try:
        from lib import billing
        synced = billing.sync_receipts()
        for s in synced:
            print("領収書をフォルダへ保存:", s.get("client"), s.get("path"), flush=True)
    except Exception as e:  # noqa: BLE001
        print("領収書フォルダ同期をスキップ（次回再試行）:", e, flush=True)
    try:
        from lib import payslip
        for s in payslip.sync_payslips():
            print("給与明細をフォルダへ保存:", s.get("employee"), s.get("path"), flush=True)
    except Exception as e:  # noqa: BLE001
        print("給与明細フォルダ同期をスキップ（次回再試行）:", e, flush=True)


def main() -> None:
    db.init_db()
    if "--b2-test" in sys.argv:
        # 出荷確定せず、取得と照合の確認だけ行う
        from lib import b2_fetch
        r = b2_fetch.fetch_and_process(dry_run=True)
        print(f'[テスト] 読込{r["rows"]}行／照合可能{len(r["messages"])}件／未照合{r["unmatched"]}行')
        for m in r["messages"]:
            print(" ", m)
        return
    if "--b2" in sys.argv:
        _run_b2_fetch()
        return
    if "--b2-print" in sys.argv:
        _run_b2_print()
        return
    if "--b2-pickup" in sys.argv:
        _run_b2_pickup()
        return
    if "--b2-history" in sys.argv:
        _run_b2_history()
        return
    if "--pickup-explore" in sys.argv:
        # 集荷依頼ページの構造を調べる（本番前の調整用）
        from lib import b2_fetch
        r = b2_fetch.request_pickup(explore=True, headful="--headful" in sys.argv)
        print(r.get("message", ""))
        return
    if "--watch" in sys.argv:
        print(f"常駐エージェント開始（{INTERVAL}秒ごとに監視）", flush=True)
        while True:
            try:
                _process_exports()
                _check_b2_request()
                _check_b2_print()
                _check_b2_pickup()
                _check_b2_history()
                _poll_jobs()
                _check_granada_sync()
                _check_receipt_sync()
            except Exception as e:  # noqa: BLE001  一時的なエラーで止めない
                print("一時エラー（次回再試行）:", e, flush=True)
            time.sleep(INTERVAL)
    else:
        n = _process_exports()
        print(f"{n} 件書き出しました。" if n else "予約された出力はありません。")


if __name__ == "__main__":
    main()
