# -*- coding: utf-8 -*-
"""ブランド統一のUI基盤（阿部農園／琥珀米）。

設計方針（受注管理システムの定石パターンを採用）：
- サイドバーを廃止し、上部タブナビ（ホーム/注文/顧客/設定）
- ワークキュー型：今日やること（精米→発送）が一画面で完結
- ステータスチップで注文の状態をひと目で表示
- 追加・編集はモーダル（st.dialog）で画面遷移ゼロ
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

from . import config

ROOT = Path(__file__).resolve().parent.parent

# ブランドカラー
NAVY = "#211F4B"       # 阿部農園ロゴの藍
NAVY_SOFT = "#3A3768"
GOLD = "#8C6E2B"       # 琥珀米のゴールド
GOLD_LIGHT = "#C9A24B"
CREAM = "#FBF7EE"
PAPER = "#FFFDF8"
INK = "#2A2620"

# チャネル表示
CHANNELS = {
    "base":    {"label": "BASE",    "color": "#1f7a5a"},
    "line":    {"label": "LINE",    "color": "#06C755"},
    "komeful": {"label": "コメフル", "color": GOLD},
    "manual":  {"label": "手入力",   "color": "#777"},
    "import":  {"label": "取込",     "color": "#999"},
}

# 注文ステータス（pending=精米待ち/発送待ち, milled=精米済み, shipped=出荷済み）
STATUS_LABELS = {
    "pending_mill": ("精米待ち", "#B45309", "#FEF3C7"),   # amber
    "pending_ship": ("発送待ち", "#1D4ED8", "#DBEAFE"),   # blue
    "milled":       ("精米済み", "#047857", "#D1FAE5"),   # green
    "shipped":      ("出荷済み", "#6B7280", "#F3F4F6"),   # gray
}


@lru_cache(maxsize=4)
def _logo_b64(filename: str) -> str:
    p = ROOT / filename
    if not p.exists():
        return ""
    return base64.b64encode(p.read_bytes()).decode()


@lru_cache(maxsize=1)
def _page_icon():
    p = ROOT / "icon.png"
    if not p.exists():
        return "🌾"
    try:
        from PIL import Image
        return Image.open(p)
    except Exception:  # noqa: BLE001
        return "🌾"


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@500;700&family=Noto+Sans+JP:wght@400;500;700&display=swap');

        :root {{
            --navy:{NAVY}; --gold:{GOLD}; --gold-l:{GOLD_LIGHT};
            --cream:{CREAM}; --paper:{PAPER}; --ink:{INK};
        }}

        .stApp {{ background: var(--cream); }}
        html, body, [class*="css"] {{
            font-family: 'Noto Sans JP', sans-serif;
            color: var(--ink);
        }}

        /* サイドバー完全非表示（タブナビに統一） */
        section[data-testid="stSidebar"] {{ display:none !important; }}
        [data-testid="stSidebarCollapsedControl"] {{ display:none !important; }}

        /* 上部の余白（ツールバーと重ならない） */
        .block-container, [data-testid="stMainBlockContainer"] {{
            padding-top: 2.6rem !important;
            padding-bottom: 4rem !important;
            max-width: 860px;
        }}

        h1, h2, h3, h4 {{
            font-family: 'Shippori Mincho', 'Yu Mincho', serif !important;
            color: var(--navy) !important;
            letter-spacing: .02em;
        }}

        /* ヘッダー（コンパクト） */
        .brand-bar {{
            display:flex; align-items:center; justify-content:center; gap:.6rem;
            padding: 0 0 .2rem;
        }}
        .brand-bar img {{ height: clamp(34px, 8vw, 46px); }}
        .brand-bar .t {{
            font-family:'Shippori Mincho',serif; color:var(--navy);
            font-weight:700; font-size: clamp(1rem, 3.4vw, 1.25rem); letter-spacing:.14em;
            white-space:nowrap;
        }}

        /* ナビ（segmented control） */
        [data-testid="stButtonGroup"] {{ display:flex; justify-content:center; }}
        [data-testid="stButtonGroup"] button {{
            font-weight:700; border-radius:999px !important; padding:.45rem 1.05rem;
        }}
        [data-testid="stButtonGroup"] button[aria-checked="true"],
        [data-testid="stButtonGroup"] button[data-selected="true"] {{
            background: var(--navy) !important; color:#fff !important;
            border-color: var(--navy) !important;
        }}

        /* ボタン */
        .stButton > button, .stDownloadButton > button {{
            border-radius: 12px; border: 1.5px solid var(--navy);
            background: var(--paper); color: var(--navy);
            font-weight: 700; padding: .55rem 1rem; transition: all .15s ease;
        }}
        .stButton > button:hover, .stDownloadButton > button:hover {{
            background: var(--navy); color:#fff;
        }}
        .stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{
            background: var(--navy); color:#fff;
        }}
        .stButton > button[kind="primary"]:hover {{ background:#15133a; }}

        /* メトリクスカード */
        [data-testid="stMetric"] {{
            background: var(--paper); border:1px solid #E8DEC8;
            border-left:5px solid var(--gold); border-radius:14px;
            padding:12px 14px; box-shadow:0 1px 3px rgba(33,31,75,.06);
        }}
        [data-testid="stMetricValue"] {{
            color:var(--navy); font-weight:700; font-family:'Shippori Mincho',serif;
        }}

        /* セクション見出し（金のひし形） */
        .sec-title {{
            display:flex; align-items:center; gap:.55rem;
            font-family:'Shippori Mincho',serif; font-weight:700;
            color:var(--navy); font-size:1.18rem; margin:.5rem 0 .15rem;
            word-break:keep-all; line-height:1.3;
        }}
        .sec-title::before {{
            content:""; width:11px; height:11px; flex:0 0 auto;
            background:linear-gradient(135deg,var(--gold),var(--gold-l));
            transform:rotate(45deg); border-radius:2px;
        }}
        .sec-sub {{ color:#8a7f6a; font-size:.8rem; margin:-.05rem 0 .45rem 1.55rem; }}

        /* 注文カード */
        .o-card {{
            background: var(--paper); border:1px solid #E8DEC8;
            border-radius:14px; padding:10px 14px; margin-bottom:8px;
            box-shadow:0 1px 3px rgba(33,31,75,.05);
        }}
        .o-name {{ font-weight:700; color:var(--navy); font-size:1.02rem; }}
        .o-line {{ color:var(--ink); font-size:.92rem; margin-top:1px; }}
        .o-meta {{ color:#8a7f6a; font-size:.8rem; margin-top:1px; }}

        /* チップ */
        .chip {{
            display:inline-block; font-size:.7rem; font-weight:700;
            padding:2px 9px; border-radius:999px; vertical-align:middle;
            white-space:nowrap;
        }}
        .ch-badge {{
            display:inline-block; color:#fff; font-size:.68rem; font-weight:700;
            padding:2px 8px; border-radius:999px; vertical-align:middle;
        }}

        /* 精米キュー行 */
        .mill-row {{
            display:flex; align-items:center; justify-content:space-between;
            background:var(--paper); border:1px solid #E8DEC8; border-radius:12px;
            padding:8px 12px; margin-bottom:6px;
        }}
        .mill-big {{ font-family:'Shippori Mincho',serif; font-weight:700; color:var(--navy); font-size:1.05rem; }}

        hr.brand-rule {{
            height:2px; width:64px; margin:14px auto 6px; border:0;
            background:linear-gradient(90deg,var(--gold),var(--gold-l));
        }}

        /* ダイアログ内の見た目 */
        [data-testid="stDialog"] h2 {{ font-size:1.15rem; }}

        /* スマホ最適化 */
        @media (max-width: 640px) {{
            .block-container {{ padding: 2.7rem .6rem 4rem !important; }}
            .stButton > button, .stDownloadButton > button {{ width:100%; padding:.68rem; font-size:.95rem; }}
            [data-testid="stMetricValue"] {{ font-size:1.45rem; }}
            [data-testid="stMetricLabel"] p {{ font-size:.76rem; }}
            .sec-title {{ font-size:1.06rem; }}
            [data-testid="stButtonGroup"] button {{ padding:.4rem .7rem; font-size:.86rem; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    """コンパクトなブランドヘッダー（ロゴ＋システム名を横並び）。"""
    logo = _logo_b64("阿部農園ロゴ.png")
    img = f'<img src="data:image/png;base64,{logo}" alt=""/>' if logo else ""
    st.markdown(
        f'<div class="brand-bar">{img}<span class="t">精米・発送管理</span></div>',
        unsafe_allow_html=True,
    )


def render_login_header() -> None:
    logo = _logo_b64("阿部農園ロゴ.png")
    img = f'<img src="data:image/png;base64,{logo}" style="height:90px"/>' if logo else ""
    st.markdown(
        f'<div style="text-align:center;padding:14px 0 4px">{img}'
        f'<div style="font-family:\'Shippori Mincho\',serif;color:{GOLD};'
        f'letter-spacing:.25em;margin-top:6px">ロ グ イ ン</div>'
        f'<hr class="brand-rule"/></div>',
        unsafe_allow_html=True,
    )


def require_login() -> None:
    pw = config.app_password()
    if not pw:
        return
    if st.session_state.get("authed"):
        return
    render_login_header()
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


def section(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="sec-title">{title}</div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<div class="sec-sub">{sub}</div>', unsafe_allow_html=True)


def channel_badge(channel: str) -> str:
    c = CHANNELS.get(channel, {"label": channel, "color": "#777"})
    return f'<span class="ch-badge" style="background:{c["color"]}">{c["label"]}</span>'


def status_chip(order) -> str:
    """注文の状態チップHTML。"""
    if order["status"] == "shipped":
        key = "shipped"
    elif order["status"] == "milled":
        key = "milled"
    elif order["needs_milling"] or (order["category"] == "複合" and not order["milling_kg_override"]):
        key = "pending_mill"
    else:
        key = "pending_ship"
    label, fg, bg = STATUS_LABELS[key]
    return f'<span class="chip" style="color:{fg};background:{bg}">{label}</span>'


def order_card(order, extra_html: str = "") -> str:
    """注文カードのHTML（チェックボックス等は呼び出し側で添える）。"""
    qty = order["qty"] or 1
    kg = (order["weight_kg"] or 0) * qty
    kg_txt = f"（{kg:g}kg）" if order["needs_milling"] and kg else ""
    note = f'<div class="o-meta">📝 {order["note"]}</div>' if order.get("note") else ""
    if order.get("tracking_no"):
        note += f'<div class="o-meta">🚚 伝票番号 {order["tracking_no"]}</div>'
    return (
        f'<div class="o-card">'
        f'<span class="o-name">{order["customer_name"]} 様</span>　'
        f'{status_chip(order)} {channel_badge(order["channel"])}'
        f'<div class="o-line">{order["product_name"]} × {qty} {kg_txt}</div>'
        f'<div class="o-meta">〒{order["zip"]}　{order["address"]}{order["address2"] or ""}</div>'
        f'{note}{extra_html}'
        f'</div>'
    )


VIEWS = ["🏠 ホーム", "📋 注文", "👤 顧客", "⚙ 設定"]


def render_nav() -> str:
    """上部タブナビ。選択中のビュー名（絵文字なし）を返す。"""
    sel = st.segmented_control(
        "ナビ", VIEWS, default=VIEWS[0], key="nav",
        label_visibility="collapsed",
    )
    sel = sel or VIEWS[0]
    return sel.split(" ", 1)[1]


def setup_page() -> None:
    st.set_page_config(
        page_title="精米・発送管理｜阿部農園",
        page_icon=_page_icon(), layout="centered",
        initial_sidebar_state="collapsed",
    )
    inject_css()
    require_login()
    render_header()
