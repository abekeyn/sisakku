# -*- coding: utf-8 -*-
"""PDFを既定プリンタへ自動印刷する（Windows）。

優先：SumatraPDF（無音で確実に既定プリンタへ）。無ければ os.startfile の print 動詞。

WiFi(WSD)プリンタはスリープ中だと最初の印刷指示が無言で失敗する（ジョブが
作られない）。そこで「印刷ジョブが実際に作られたか」を確認し、作られなければ
スリープと判断して少し待って再試行する（新規ジョブが出来た時のみ成功扱いなので
二重印刷にならない）。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path


def _find_sumatra() -> str | None:
    for p in [
        os.path.expandvars(r"%LOCALAPPDATA%\SumatraPDF\SumatraPDF.exe"),
        os.path.expandvars(r"%ProgramFiles%\SumatraPDF\SumatraPDF.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\SumatraPDF\SumatraPDF.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\SumatraPDF.exe"),
    ]:
        if p and os.path.exists(p):
            return p
    return None


def _ps(cmd: str) -> str:
    """PowerShellを実行して標準出力を返す（失敗時は空文字）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=20,
        )
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _default_printer() -> str:
    return _ps("(Get-CimInstance Win32_Printer -Filter 'Default=True').Name")


def _job_ids(name: str) -> set[str]:
    """そのプリンタの現在のジョブID集合（新規ジョブ検出用）。"""
    if not name:
        return set()
    safe = name.replace("'", "''")
    out = _ps(f"(Get-PrintJob -PrinterName '{safe}' -EA SilentlyContinue "
              f"| Select-Object -ExpandProperty Id) -join ','")
    return {x for x in out.split(",") if x.strip()}


def print_pdf(pdf_bytes: bytes, printer: str | None = None) -> tuple[bool, str]:
    """PDFを印刷する。printer未指定なら既定プリンタ。

    スリープ中のWiFiプリンタにも対応：ジョブが作られるまで最大3回再試行する。
    returns (成功, メッセージ)
    """
    tmp = Path(tempfile.gettempdir()) / f"abe_label_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    tmp.write_bytes(pdf_bytes)

    sumatra = _find_sumatra()
    name = printer or _default_printer()

    if sumatra and name:
        safe_name = name
        mono = ["-print-settings", "monochrome"]  # 白黒（インク節約）
        for attempt in range(3):
            before = _job_ids(safe_name)
            try:
                subprocess.run(
                    [sumatra, "-print-to", safe_name, *mono, "-silent", str(tmp)],
                    timeout=60, check=False,
                )
            except Exception:  # noqa: BLE001
                pass
            # 新規ジョブが現れたら成功（最大6秒待つ）
            for _ in range(12):
                time.sleep(0.5)
                if _job_ids(safe_name) - before:
                    return True, "印刷しました（SumatraPDF・白黒）"
            # ジョブ無し＝プリンタがスリープ/未接続。少し待って起こして再試行
            time.sleep(4)
        return False, ("プリンタにジョブを送れませんでした。プリンタの電源・WiFi接続を"
                       "確認して、もう一度お試しください。")

    # SumatraPDFが無い／既定プリンタ名が取れない場合のフォールバック
    if sumatra:
        try:
            subprocess.run([sumatra, "-print-to-default", "-print-settings",
                            "monochrome", "-silent", str(tmp)], timeout=60, check=False)
            return True, "印刷しました（SumatraPDF・白黒）"
        except Exception:  # noqa: BLE001
            pass
    try:
        os.startfile(str(tmp), "print")  # type: ignore[attr-defined]
        return True, "印刷を実行しました（既定のPDFアプリ）"
    except Exception as e:  # noqa: BLE001
        return False, f"印刷に失敗：{e}（PDFは {tmp} に保存しました）"
