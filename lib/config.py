# -*- coding: utf-8 -*-
"""設定値（DB接続先・パスワード等）の読み込み。

優先順位：
1. ローカルの .streamlit/secrets.toml（PCアプリ・常駐エージェント共通）
2. 環境変数
3. Streamlit Cloud の st.secrets（クラウド公開時）

これにより、同じコードがローカル(SQLite)でもクラウド(PostgreSQL)でも動く。
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SECRETS_FILE = ROOT / ".streamlit" / "secrets.toml"


def _from_file(key: str):
    try:
        if _SECRETS_FILE.exists():
            data = tomllib.loads(_SECRETS_FILE.read_text(encoding="utf-8"))
            return data.get(key)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return None


def _from_st_secrets(key: str):
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:  # noqa: BLE001  streamlit外やsecrets未設定でも落とさない
        return None
    return None


def get_secret(key: str, default=None):
    val = _from_file(key)
    if val:
        return val
    val = os.environ.get(key)
    if val:
        return val
    val = _from_st_secrets(key)
    if val:
        return val
    return default


def database_url() -> str | None:
    """PostgreSQL等の接続URL。未設定ならNone（＝ローカルSQLiteを使う）。"""
    return get_secret("DATABASE_URL")


def app_password() -> str | None:
    """アプリのログインパスワード（共有1つ）。未設定ならログイン不要。"""
    return get_secret("APP_PASSWORD")
