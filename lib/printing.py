# -*- coding: utf-8 -*-
"""PDFを既定プリンタへ自動印刷する（Windows）。

優先：SumatraPDF（無音で確実に既定プリンタへ）。無ければ os.startfile の print 動詞。
プリンタの電源が入っていれば、そのまま印刷が始まる。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
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


def print_pdf(pdf_bytes: bytes, printer: str | None = None) -> tuple[bool, str]:
    """PDFを印刷する。printer未指定なら既定プリンタ。

    returns (成功, メッセージ)
    """
    tmp = Path(tempfile.gettempdir()) / f"abe_label_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    tmp.write_bytes(pdf_bytes)

    sumatra = _find_sumatra()
    if sumatra:
        try:
            if printer:
                args = [sumatra, "-print-to", printer, "-silent", str(tmp)]
            else:
                args = [sumatra, "-print-to-default", "-silent", str(tmp)]
            subprocess.run(args, timeout=60, check=False)
            return True, "印刷しました（SumatraPDF）"
        except Exception as e:  # noqa: BLE001
            pass  # フォールバックへ

    # フォールバック：OS既定のPDFアプリの印刷
    try:
        os.startfile(str(tmp), "print")  # type: ignore[attr-defined]
        return True, "印刷を実行しました（既定のPDFアプリ）"
    except Exception as e:  # noqa: BLE001
        return False, f"印刷に失敗：{e}（PDFは {tmp} に保存しました）"
