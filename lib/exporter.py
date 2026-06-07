# -*- coding: utf-8 -*-
"""ヤマト送り状CSVの出力。

- 通常（PCで稼働中）：デスクトップの『ヤマト出荷CSV』フォルダへ
  『YYYYMMDD_ヤマト出荷用出力データ.csv』として直接保存
- 予約（PC停止中にスマホ等から）：DBのexport_jobsに積んでおき、
  PC起動時（アプリ起動 or 常駐エージェント）に自動で書き出す
"""
from __future__ import annotations

import base64
from datetime import date, datetime
from pathlib import Path

from . import db

ROOT = Path(__file__).resolve().parent.parent
# プロジェクトはデスクトップ直下にあるので、その親＝デスクトップ
DESKTOP = ROOT.parent
EXPORT_DIR = DESKTOP / "ヤマト出荷CSV"
BASENAME = "ヤマト出荷用出力データ"


def make_filename(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.strftime('%Y%m%d')}_{BASENAME}.csv"


def _unique_path(directory: Path, filename: str) -> Path:
    """同名ファイルがあれば _2, _3 … を付けて上書きを避ける。"""
    p = directory / filename
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 2
    while True:
        cand = directory / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def save_now(csv_bytes: bytes, d: date | None = None) -> tuple[bool, str]:
    """デスクトップの『ヤマト出荷CSV』へ直接保存。

    returns (成功, 保存パス or エラーメッセージ)
    """
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = _unique_path(EXPORT_DIR, make_filename(d))
        path.write_bytes(csv_bytes)
        return True, str(path)
    except OSError as e:
        return False, str(e)


def reserve(csv_bytes: bytes, d: date | None = None) -> int:
    """PC停止中などで今書けない場合に、出力を予約（DBに保存）する。"""
    b64 = base64.b64encode(csv_bytes).decode()
    return db.enqueue_export(make_filename(d), b64)


def save_or_reserve(csv_bytes: bytes, d: date | None = None) -> dict:
    """まず直接保存を試み、できなければ予約する。

    returns {"mode": "saved"|"reserved", "path": str|"", "error": str}
    """
    ok, info = save_now(csv_bytes, d)
    if ok:
        return {"mode": "saved", "path": info, "error": ""}
    reserve(csv_bytes, d)
    return {"mode": "reserved", "path": "", "error": info}


def process_pending() -> list[str]:
    """予約済み(pending)の出力をすべてデスクトップへ書き出す。

    PC起動時（アプリ起動時のbootstrap、または常駐エージェント）から呼ぶ。
    returns 書き出したパスの一覧
    """
    written = []
    jobs = db.list_export_jobs(status="pending")
    if not jobs:
        return written
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return written  # 書ける場所が無い（クラウド側など）なら何もしない
    for job in jobs:
        try:
            data = base64.b64decode(job["content_b64"])
            path = _unique_path(EXPORT_DIR, job["filename"])
            path.write_bytes(data)
            db.mark_export_done(job["id"], str(path))
            written.append(str(path))
        except OSError:
            break  # 書けないなら次回に持ち越し
    return written
