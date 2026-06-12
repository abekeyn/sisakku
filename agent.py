# -*- coding: utf-8 -*-
"""常駐エージェント（PCで実行）。

役割：
1. 予約されたヤマトCSVをデスクトップ『ヤマト出荷CSV』へ書き出す（6秒ごと）
2. アプリから「B2自動取得」の指示が来たら、ブラウザ自動操作で
   B2クラウドから発行済データを取得 → 照合 → 出荷完了 → BASE反映

- 監視モード（常駐）:  python agent.py --watch
- 1回だけ実行:        python agent.py
- B2取得を今すぐ:     python agent.py --b2
"""
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import db, exporter  # noqa: E402

INTERVAL = 6  # 監視間隔（秒）


def _process_exports() -> int:
    written = exporter.process_pending()
    for p in written:
        print("書き出し:", p, flush=True)
    return len(written)


def _run_b2_fetch() -> None:
    """B2クラウドから発行済データを自動取得して出荷確定。結果をDBに記録。"""
    from lib import b2_fetch
    started = datetime.now().isoformat(timespec="seconds")
    try:
        r = b2_fetch.fetch_and_process()
        result = {
            "ok": True, "at": started,
            "summary": f'読込{r["rows"]}行／出荷確定{r["shipped"]}件／未照合{r["unmatched"]}行',
            "messages": r["messages"],
        }
        print("B2取得 成功:", result["summary"], flush=True)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "at": started, "summary": f"失敗: {e}", "messages": []}
        print("B2取得 失敗:", e, flush=True)
        traceback.print_exc()
    db.set_setting("b2_fetch_result", result)


def _check_b2_request() -> bool:
    """アプリからの取得指示があれば実行する。"""
    req = db.get_setting("b2_fetch_request")
    done = db.get_setting("b2_fetch_handled")
    if req and req != done:
        db.set_setting("b2_fetch_handled", req)
        _run_b2_fetch()
        return True
    return False


def _run_b2_print() -> None:
    """B2で送り状を発行し、PDFを既定プリンタへ印刷する。"""
    import base64
    from lib import b2_fetch, printing
    started = datetime.now().isoformat(timespec="seconds")
    try:
        b64 = db.get_setting("b2_print_csv")
        if not b64:
            raise RuntimeError("印刷するデータがありません")
        csv_bytes = base64.b64decode(b64)
        r = b2_fetch.issue_and_print(csv_bytes)
        msg = r.get("message", "")
        if r.get("pdf"):
            ok, pmsg = printing.print_pdf(r["pdf"])
            msg += "／" + pmsg
        result = {"ok": bool(r.get("issued")), "at": started, "summary": msg}
        print("B2発行・印刷:", msg, flush=True)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "at": started, "summary": f"失敗: {e}"}
        print("B2発行・印刷 失敗:", e, flush=True)
        traceback.print_exc()
    db.set_setting("b2_print_result", result)


def _check_b2_print() -> bool:
    """アプリからの印刷指示があれば実行する。"""
    req = db.get_setting("b2_print_request")
    done = db.get_setting("b2_print_handled")
    if req and req != done:
        db.set_setting("b2_print_handled", req)
        _run_b2_print()
        return True
    return False


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
    if "--watch" in sys.argv:
        import time
        print(f"常駐エージェント開始（{INTERVAL}秒ごとに監視）", flush=True)
        while True:
            try:
                _process_exports()
                _check_b2_request()
                _check_b2_print()
            except Exception as e:  # noqa: BLE001  一時的なエラーで止めない
                print("一時エラー（次回再試行）:", e, flush=True)
            time.sleep(INTERVAL)
    else:
        n = _process_exports()
        print(f"{n} 件書き出しました。" if n else "予約された出力はありません。")


if __name__ == "__main__":
    main()
