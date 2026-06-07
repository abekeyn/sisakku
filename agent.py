# -*- coding: utf-8 -*-
"""予約出力エージェント（PC起動時に自動実行する用）。

アプリ(Streamlit)を開かなくても、スマホ等から予約された
ヤマト出荷CSVをデスクトップの『ヤマト出荷CSV』フォルダへ書き出す。

スタートアップに登録しておくと、PC起動のたびに自動で実行される。
（クラウド公開してDBを共有すると、PC停止中にスマホから予約 → 起動時に自動保存 が実現する）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import db, exporter  # noqa: E402


def main() -> None:
    db.init_db()
    written = exporter.process_pending()
    if written:
        print(f"{len(written)} 件の予約出力を書き出しました：")
        for p in written:
            print("  ", p)
    else:
        print("予約された出力はありません。")


if __name__ == "__main__":
    main()
