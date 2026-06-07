# -*- coding: utf-8 -*-
"""ブランド統一のUI（阿部農園／琥珀米のデザイン）。

- 配色：クリーム背景 × 墨色文字 × 藍(阿部農園) × 琥珀ゴールド(琥珀米)
- 見出しは明朝体（和モダン・高級感）
- スマホ対応（centeredレイアウト＋メディアクエリ）
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

from . import config

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def _page_icon():
    """ブラウザのタブ／スマホのホーム画面アイコン（正方形ロゴ）。"""
    p = ROOT / "icon.png"
    if not p.exists():
        return "🌾"
    try:
        from PIL import Image
        return Image.open(p)
    except Exception:  # noqa: BLE001
        return "🌾"

# ブランドカラー
NAVY = "#211F4B"       # 阿部農園ロゴの藍
NAVY_SOFT = "#3A3768"
GOLD = "#8C6E2B"       # 琥珀米のゴールド
GOLD_LIGHT = "#C9A24B"
CREAM = "#FBF7EE"
PAPER = "#FFFDF8"
INK = "#2A2620"

# チャネル表示（ラベルと色）
CHANNELS = {
    "base":   {"label": "BASE",   "color": "#1f7a5a"},
    "line":   {"label": "LINE",   "color": "#06C755"},
    "komeful":{"label": "コメフル", "color": GOLD},
    "manual": {"label": "手入力",  "color": "#777"},
    "import": {"label": "取込",    "color": "#999"},
}


@lru_cache(maxsize=4)
def _logo_b64(filename: str) -> str:
    p = ROOT / filename
    if not p.exists():
        return ""
    return base64.b64encode(p.read_bytes()).decode()


def inject_css() -> None:
    """全ページ共通のブランドCSSを注入する。"""
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;700&family=Noto+Sans+JP:wght@400;500;700&display=swap');

        :root {{
            --navy:{NAVY}; --gold:{GOLD}; --gold-l:{GOLD_LIGHT};
            --cream:{CREAM}; --paper:{PAPER}; --ink:{INK};
        }}

        /* 背景・本文 */
        .stApp {{ background: var(--cream); }}
        html, body, [class*="css"] {{
            font-family: 'Noto Sans JP', sans-serif;
            color: var(--ink);
        }}

        /* 上部ツールバーにロゴが隠れないよう、本文の上に余白を確保 */
        .block-container, [data-testid="stMainBlockContainer"], [data-testid="stAppViewBlockContainer"] {{
            padding-top: 3.4rem !important;
        }}
        .brand-header {{ margin-top: .3rem; }}

        /* 見出しは明朝 */
        h1, h2, h3, h4 {{
            font-family: 'Shippori Mincho', 'Yu Mincho', 'Hiragino Mincho ProN', serif !important;
            color: var(--navy) !important;
            letter-spacing: .02em;
        }}

        /* ボタン（基本＝白地に藍枠、primary＝藍地） */
        .stButton > button, .stDownloadButton > button {{
            border-radius: 12px;
            border: 1.5px solid var(--navy);
            background: var(--paper);
            color: var(--navy);
            font-weight: 700;
            padding: .6rem 1rem;
            transition: all .15s ease;
        }}
        .stButton > button:hover, .stDownloadButton > button:hover {{
            background: var(--navy); color: #fff; border-color: var(--navy);
        }}
        .stButton > button[kind="primary"] {{
            background: var(--navy); color: #fff;
        }}
        .stButton > button[kind="primary"]:hover {{
            background: #15133a;
        }}

        /* メトリクス（指標カード） */
        [data-testid="stMetric"] {{
            background: var(--paper);
            border: 1px solid #E8DEC8;
            border-left: 5px solid var(--gold);
            border-radius: 14px;
            padding: 14px 16px;
            box-shadow: 0 1px 3px rgba(33,31,75,.06);
        }}
        [data-testid="stMetricValue"] {{
            color: var(--navy); font-weight: 700;
            font-family: 'Shippori Mincho', serif;
        }}

        /* タブ */
        .stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
        .stTabs [aria-selected="true"] {{ color: var(--navy) !important; }}
        .stTabs [data-baseweb="tab-highlight"] {{ background: var(--gold) !important; }}

        /* ブランドヘッダー */
        .brand-header {{ text-align:center; padding: 8px 0 4px; overflow:visible; }}
        .brand-header img {{
            display:block; margin:0 auto;
            height: clamp(46px, 12vw, 88px);
            width:auto; max-width:92%; object-fit:contain;
        }}
        .brand-sub {{
            font-family:'Shippori Mincho',serif; color: var(--gold);
            font-size: .95rem; letter-spacing:.25em; margin-top:2px;
        }}
        .brand-rule {{
            height:2px; width:64px; margin:10px auto 2px;
            background: linear-gradient(90deg, var(--gold), var(--gold-l));
            border:0;
        }}

        /* 発送カード */
        .ship-card {{
            background: var(--paper); border:1px solid #E8DEC8;
            border-radius: 14px; padding: 12px 14px; margin-bottom: 10px;
            box-shadow: 0 1px 3px rgba(33,31,75,.05);
        }}
        .ship-name {{ font-weight:700; color:var(--navy); font-size:1.05rem; }}
        .ship-item {{ color:var(--ink); font-size:.95rem; margin-top:2px; }}
        .ch-badge {{
            display:inline-block; color:#fff; font-size:.7rem; font-weight:700;
            padding:2px 8px; border-radius:999px; vertical-align:middle;
        }}

        /* セクション見出し（金のひし形マーカー＝織り菱モチーフ） */
        .sec-title {{
            display:flex; align-items:center; gap:.55rem;
            font-family:'Shippori Mincho','Yu Mincho',serif;
            font-weight:700; color:var(--navy); font-size:1.25rem;
            margin: .4rem 0 .2rem;
        }}
        .sec-title::before {{
            content:""; width:11px; height:11px; flex:0 0 auto;
            background:linear-gradient(135deg,var(--gold),var(--gold-l));
            transform:rotate(45deg); border-radius:2px;
        }}
        .sec-sub {{ color:#8a7f6a; font-size:.82rem; margin:-.1rem 0 .5rem 1.6rem; }}

        /* 改行崩れ防止：見出しは折り返しを抑える */
        h1, .sec-title {{ word-break:keep-all; overflow-wrap:normal; line-height:1.3; }}

        /* スマホ最適化 */
        @media (max-width: 640px) {{
            .block-container {{ padding: 3rem .6rem 3rem !important; }}
            .brand-sub {{ letter-spacing:.12em; font-size:.82rem; white-space:nowrap; }}
            .stButton > button, .stDownloadButton > button {{ width:100%; padding:.7rem; font-size:.95rem; }}
            [data-testid="stMetricValue"] {{ font-size: 1.5rem; }}
            [data-testid="stMetricLabel"] p {{ font-size:.78rem; }}
            .sec-title {{ font-size:1.1rem; }}
            h1 {{ font-size: 1.3rem !important; }}
            h3 {{ font-size: 1.05rem !important; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(subtitle: str = "") -> None:
    """阿部農園ロゴ＋サブタイトルのヘッダー。"""
    logo = _logo_b64("阿部農園ロゴ.png")
    img = f'<img src="data:image/png;base64,{logo}" alt="阿部農園"/>' if logo else ""
    sub = f'<div class="brand-sub">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div class="brand-header">{img}{sub}<hr class="brand-rule"/></div>',
        unsafe_allow_html=True,
    )


def section(title: str, sub: str = "") -> None:
    """金のひし形マーカー付きのセクション見出し（絵文字を使わないシンプル版）。"""
    st.markdown(f'<div class="sec-title">{title}</div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<div class="sec-sub">{sub}</div>', unsafe_allow_html=True)


def channel_badge(channel: str) -> str:
    """チャネルのHTMLバッジ文字列を返す。"""
    c = CHANNELS.get(channel, {"label": channel, "color": "#777"})
    return f'<span class="ch-badge" style="background:{c["color"]}">{c["label"]}</span>'


def require_login() -> None:
    """共有パスワードでのログイン保護（APP_PASSWORD設定時のみ有効）。"""
    pw = config.app_password()
    if not pw:
        return  # 未設定（ローカル等）は保護なし
    if st.session_state.get("authed"):
        return
    render_header("ロ グ イ ン")
    with st.form("login_form"):
        entered = st.text_input("パスワード", type="password")
        if st.form_submit_button("ログイン", type="primary", use_container_width=True):
            if entered == str(pw):
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("パスワードが違います。")
    st.caption("阿部農園 精米・発送管理システム")
    st.stop()


def setup_page(title: str, icon: str = "🌾", subtitle: str = "", layout: str = "centered") -> None:
    """各ページ先頭の定型処理（ページ設定＋CSS＋ログイン＋ヘッダー）。"""
    st.set_page_config(page_title=title, page_icon=_page_icon(), layout=layout)
    inject_css()
    require_login()
    if subtitle is not None:
        render_header(subtitle)
