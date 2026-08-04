# -*- coding: utf-8 -*-
"""GitHub Actionsのworkflow_dispatchをAPI経由で起動する。

請求書PDFの生成にはLibreOfficeが必要。常時起動のStreamlitアプリ本体に
直接インストールするとビルドが壊れるリスクがある（実際に一度壊れて復旧した）。
そのため、PDFを実際に作る処理は既にLibreOffice導入済みで動作実績のある
GitHub Actions（.github/workflows/granada-invoice.yml）に任せ、
アプリ側は「起動を指示するだけ」にする。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config

REPO = "abekeyn/sisakku"
WORKFLOW = "granada-invoice.yml"


def is_configured() -> bool:
    return bool(config.get_secret("GITHUB_PAT", ""))


def trigger(action: str, **inputs) -> tuple[bool, str]:
    """workflow_dispatchを起動する。inputsはすべて文字列化してActionsのinputsへ渡す。"""
    token = config.get_secret("GITHUB_PAT", "")
    if not token:
        return False, "GITHUB_PAT が未設定です（Streamlit Cloudのsecretsに登録してください）"
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
    body = {
        "ref": "main",
        "inputs": {"action": action,
                   **{k: str(v) for k, v in inputs.items() if v is not None and v != ""}},
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True, "GitHub Actionsで処理を開始しました"
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode(errors="replace")
        except Exception:  # noqa: BLE001
            detail = ""
        return False, f"GitHub API エラー {e.code}: {detail[:200]}"
    except Exception as e:  # noqa: BLE001
        return False, f"GitHub API 呼び出しに失敗: {e}"
