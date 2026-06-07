# -*- coding: utf-8 -*-
"""予約出力エージェント（PCで常駐）。

クラウド（スマホ等）で「ヤマトCSVを作成」すると、注文がクラウドDBに
『予約』として積まれる。このエージェントがそれを監視し、PCのデスクトップ
『ヤマト出荷CSV』フォルダへ自動で書き出す。

- 監視モード（常駐）:  python agent.py --watch
- 1回だけ実行:        python agent.py

スタートアップに登録すると、PC起動中はずっと監視し、
PC停止中に予約された分も起動時にまとめて書き出す。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import db, exporter  # noqa: E402

INTERVAL = 6  # 監視間隔（秒）


def _process_once() -> int:
    written = exporter.process_pending()
    for p in written:
        print("書き出し:", p, flush=True)
    return len(written)


def main() -> None:
    db.init_db()
    if "--watch" in sys.argv:
        print(f"予約出力エージェント開始（{INTERVAL}秒ごとに監視）", flush=True)
        while True:
            try:
                _process_once()
            except Exception as e:  # noqa: BLE001  一時的なエラーで止めない
                print("一時エラー（次回再試行）:", e, flush=True)
            time.sleep(INTERVAL)
    else:
        n = _process_once()
        print(f"{n} 件書き出しました。" if n else "予約された出力はありません。")


if __name__ == "__main__":
    main()
