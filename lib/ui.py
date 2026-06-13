# -*- coding: utf-8 -*-
"""ブランド統一のUI基盤（阿部農園／琥珀米）。

世界観：公式サイトのヒーローと同じ「濃紺の夜に、金の光」。
- 背景は濃紺のグラデーション、カードはガラス調、アクセントは琥珀ゴールド
- 見出しは明朝（Shippori Mincho）、絵文字は使わず静かな記号のみ
- ナビは上部タブ、追加・編集はモーダル
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

from . import config

ROOT = Path(__file__).resolve().parent.parent

# ブランドカラー
NAVY = "#1B1D3E"        # 夜の濃紺（背景）
NAVY_DEEP = "#14162E"
GOLD = "#C9A24B"        # 琥珀ゴールド
GOLD_DARK = "#A9842F"
GOLD_LIGHT = "#E9D18C"
TXT = "#F2EDE0"         # 生成り（本文）
TXT_SOFT = "rgba(242,237,224,.62)"
GLASS = "rgba(255,255,255,.05)"
LINE = "rgba(201,162,75,.32)"

# チャネル表示
CHANNELS = {
    "base":    {"label": "BASE",    "color": "#1f7a5a"},
    "line":    {"label": "LINE",    "color": "#06985f"},
    "komeful": {"label": "コメフル", "color": GOLD_DARK},
    "manual":  {"label": "手入力",   "color": "#5a5e7a"},
    "import":  {"label": "取込",     "color": "#5a5e7a"},
}

# 注文ステータス（ダーク背景向けの配色）
STATUS_LABELS = {
    "pending_mill": ("精米待ち", "#F5D08C", "rgba(201,162,75,.22)"),
    "pending_ship": ("発送待ち", "#9DC4FF", "rgba(80,130,230,.22)"),
    "milled":       ("精米済み", "#8FE3C0", "rgba(30,160,110,.22)"),
    "shipped":      ("出荷済み", "#C5C9D6", "rgba(255,255,255,.12)"),
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
            --navy:{NAVY}; --gold:{GOLD}; --gold-l:{GOLD_LIGHT}; --gold-d:{GOLD_DARK};
            --txt:{TXT}; --glass:{GLASS}; --line:{LINE};
        }}

        /* ===== 夜の背景（上部にほのかな金のかがやき） ===== */
        .stApp {{
            background:
              radial-gradient(900px 420px at 50% 0%, rgba(201,162,75,.10), transparent 70%),
              radial-gradient(1100px 760px at 50% -10%, #272A57 0%, {NAVY} 52%, {NAVY_DEEP} 100%) !important;
            background-attachment: fixed !important;
        }}
        header[data-testid="stHeader"] {{ background: transparent !important; }}
        html, body, [class*="css"] {{
            font-family: 'Noto Sans JP', sans-serif;
            color: var(--txt);
        }}

        /* サイドバー完全非表示（タブナビに統一） */
        section[data-testid="stSidebar"] {{ display:none !important; }}
        [data-testid="stSidebarCollapsedControl"] {{ display:none !important; }}

        .block-container, [data-testid="stMainBlockContainer"] {{
            padding-top: 2.6rem !important;
            padding-bottom: 4rem !important;
            max-width: 860px;
        }}

        h1, h2, h3, h4 {{
            font-family: 'Shippori Mincho', 'Yu Mincho', serif !important;
            color: var(--txt) !important;
            letter-spacing: .02em;
        }}
        p, li, label, .stMarkdown {{ color: var(--txt); }}
        [data-testid="stCaptionContainer"], .stCaption, small {{
            color: {TXT_SOFT} !important;
        }}

        /* 漂う金の光（控えめ） */
        .app-motes {{
            position: fixed; inset: 0; z-index: 0; overflow: hidden; pointer-events: none;
        }}
        .app-motes .mote {{
            position: absolute; border-radius: 50%;
            background: radial-gradient(circle, rgba(233,209,140,.8) 0%, rgba(201,162,75,.35) 40%, rgba(201,162,75,0) 70%);
            box-shadow: 0 0 6px 1px rgba(201,162,75,.25);
            opacity: 0;
            animation-name: moteDrift; animation-iteration-count: infinite;
            animation-timing-function: ease-in-out;
        }}
        @keyframes moteDrift {{
            0%   {{ transform: translate(0,0) scale(.7);      opacity: 0; }}
            20%  {{ opacity: .55; }}
            50%  {{ transform: translate(12px,-24px) scale(1.05); opacity: .4; }}
            80%  {{ opacity: .5; }}
            100% {{ transform: translate(-8px,-48px) scale(.7);  opacity: 0; }}
        }}
        .block-container > div {{ position: relative; z-index: 2; }}

        /* ===== ブランドヘッダー ===== */
        .brand-bar {{
            display:flex; align-items:center; justify-content:center; gap:.65rem;
            padding: 0 0 .3rem;
        }}
        .brand-bar img {{
            height: clamp(38px, 9vw, 50px);
            filter: brightness(0) invert(1) drop-shadow(0 0 10px rgba(201,162,75,.35));
            opacity:.95;
        }}
        .brand-bar .t {{
            font-family:'Shippori Mincho',serif; color:var(--txt);
            font-weight:700; font-size: clamp(1rem, 3.4vw, 1.22rem); letter-spacing:.16em;
            white-space:nowrap; text-shadow: 0 1px 14px rgba(0,0,0,.4);
        }}

        /* ===== ナビ（上部タブ） ===== */
        [data-testid="stButtonGroup"] {{ display:flex; justify-content:center; gap:6px; }}
        [data-testid="stButtonGroup"] button {{
            font-weight:700; border-radius:999px !important; padding:.42rem 1.15rem;
            background: rgba(255,255,255,.04) !important;
            border: 1px solid rgba(201,162,75,.35) !important;
            color: var(--txt) !important;
            letter-spacing:.08em;
        }}
        [data-testid="stButtonGroup"] button[kind="segmented_controlActive"],
        [data-testid="stButtonGroup"] button[aria-checked="true"],
        [data-testid="stButtonGroup"] button[aria-pressed="true"] {{
            background: linear-gradient(180deg, var(--gold), var(--gold-d)) !important;
            color: {NAVY} !important; border-color: transparent !important;
            box-shadow: 0 4px 16px rgba(201,162,75,.3);
        }}

        /* ===== ボタン ===== */
        .stButton > button, .stDownloadButton > button, .stLinkButton > a {{
            border-radius: 12px;
            border: 1px solid rgba(201,162,75,.5);
            background: rgba(255,255,255,.04);
            color: var(--txt);
            font-weight: 700; padding: .55rem 1rem; transition: all .15s ease;
        }}
        .stButton > button:hover, .stDownloadButton > button:hover, .stLinkButton > a:hover {{
            background: rgba(201,162,75,.18); border-color: var(--gold-l); color:#fff;
        }}
        .stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{
            background: linear-gradient(180deg, var(--gold), var(--gold-d)) !important;
            color: {NAVY} !important; border:none !important;
            box-shadow: 0 6px 20px rgba(201,162,75,.25);
        }}
        .stButton > button[kind="primary"]:hover {{ filter: brightness(1.07); }}
        .stButton > button:disabled, .stDownloadButton > button:disabled {{
            opacity:.35;
        }}

        /* ===== メトリクス（ガラスカード） ===== */
        [data-testid="stMetric"] {{
            background: var(--glass);
            border: 1px solid rgba(201,162,75,.25);
            border-left: 4px solid var(--gold);
            border-radius: 14px;
            padding: 12px 14px;
            box-shadow: 0 2px 12px rgba(0,0,0,.25);
        }}
        [data-testid="stMetricValue"] {{
            color:#F8F3E6 !important; font-weight:700;
            font-family:'Shippori Mincho',serif;
        }}
        [data-testid="stMetricLabel"] p {{ color: {TXT_SOFT} !important; }}

        /* ===== ステップ見出し ===== */
        .step-head {{ display:flex; align-items:center; gap:.65rem; margin: 1.0rem 0 .1rem; }}
        .step-no {{
            flex:0 0 auto; width:30px; height:30px; border-radius:50%;
            border:1px solid var(--gold); color: var(--gold-l);
            background: rgba(201,162,75,.1);
            display:flex; align-items:center; justify-content:center;
            font-weight:700; font-family:'Shippori Mincho',serif; font-size:1rem;
        }}
        .step-done .step-no {{
            background: linear-gradient(180deg, var(--gold), var(--gold-d));
            color: {NAVY}; border-color: transparent;
            box-shadow: 0 2px 10px rgba(201,162,75,.35);
        }}
        .step-title {{
            font-family:'Shippori Mincho',serif; font-weight:700;
            color:var(--txt); font-size:1.18rem; word-break:keep-all;
            letter-spacing:.04em;
        }}
        .step-sub {{ color:{TXT_SOFT}; font-size:.8rem; margin:.05rem 0 .55rem 2.5rem; }}
        hr.step-rule {{
            border:0; border-top:1px dashed rgba(201,162,75,.28); margin:1.3rem 0 .1rem;
        }}

        /* ===== セクション見出し（金のひし形） ===== */
        .sec-title {{
            display:flex; align-items:center; gap:.55rem;
            font-family:'Shippori Mincho',serif; font-weight:700;
            color:var(--txt); font-size:1.14rem; margin:.5rem 0 .15rem;
            word-break:keep-all; line-height:1.3;
        }}
        .sec-title::before {{
            content:""; width:10px; height:10px; flex:0 0 auto;
            background:linear-gradient(135deg,var(--gold),var(--gold-l));
            transform:rotate(45deg); border-radius:2px;
            box-shadow: 0 0 8px rgba(201,162,75,.4);
        }}
        .sec-sub {{ color:{TXT_SOFT}; font-size:.8rem; margin:-.05rem 0 .45rem 1.55rem; }}

        /* ===== カード ===== */
        .o-card {{
            background: var(--glass);
            border: 1px solid rgba(201,162,75,.22);
            border-radius: 14px; padding: 10px 14px; margin-bottom: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,.22);
        }}
        .o-name {{ font-weight:700; color:#F8F3E6; font-size:1.02rem; }}
        .o-line {{ color: var(--txt); font-size:.92rem; margin-top:1px; }}
        .o-meta {{ color: {TXT_SOFT}; font-size:.8rem; margin-top:1px; }}
        .o-when {{
            display:inline-block; margin-top:4px;
            color:#1B1D3E; background: var(--gold);
            font-weight:700; font-size:.8rem;
            padding:1px 9px; border-radius:8px;
        }}

        /* ===== KPIカード（ダッシュボード） ===== */
        .kpi {{
            background: linear-gradient(160deg, rgba(201,162,75,.10), rgba(38,41,73,.55));
            border: 1px solid rgba(201,162,75,.28);
            border-radius: 16px; padding: 14px 18px; height: 100%;
            box-shadow: 0 4px 18px rgba(0,0,0,.25);
        }}
        .kpi-lbl {{ color: {TXT_SOFT}; font-size:.78rem; letter-spacing:.04em; }}
        .kpi-val {{
            color:#F8F3E6; font-weight:800; font-size:1.7rem;
            line-height:1.2; margin-top:4px;
            font-variant-numeric: tabular-nums;
        }}
        .kpi-val .yen {{ color: var(--gold); font-size:1.05rem; font-weight:700;
                         margin-right:2px; }}
        .kpi-sub {{ color: {TXT_SOFT}; font-size:.76rem; margin-top:4px; }}
        .kpi-sub .up {{ color:#5BC08A; font-weight:700; }}
        .kpi-sub .down {{ color:#E07A7A; font-weight:700; }}
        .prog-title {{ color:#F8F3E6; font-weight:700; font-size:.96rem;
                       margin:2px 0 4px; letter-spacing:.02em; }}

        /* ===== チップ・バッジ ===== */
        .chip {{
            display:inline-block; font-size:.7rem; font-weight:700;
            padding:2px 9px; border-radius:999px; vertical-align:middle;
            white-space:nowrap;
        }}
        .ch-badge {{
            display:inline-block; color:#fff; font-size:.68rem; font-weight:700;
            padding:2px 8px; border-radius:999px; vertical-align:middle;
            opacity:.92;
        }}

        /* ===== 精米キュー行 ===== */
        .mill-row {{
            display:flex; align-items:center; justify-content:space-between;
            background: var(--glass);
            border:1px solid rgba(201,162,75,.22); border-radius:12px;
            padding:8px 12px; margin-bottom:6px;
            color: var(--txt);
        }}
        .mill-big {{
            font-family:'Shippori Mincho',serif; font-weight:700;
            color:#F8F3E6; font-size:1.02rem;
        }}

        hr.brand-rule {{
            height:1px; width:74px; margin:14px auto 6px; border:0;
            background:linear-gradient(90deg, transparent, var(--gold-l), transparent);
        }}

        /* ===== 入力・フォーム類の細部 ===== */
        [data-testid="stForm"] {{ border:1px solid rgba(201,162,75,.2); border-radius:14px; }}
        [data-testid="stExpander"] details {{
            border:1px solid rgba(201,162,75,.25) !important; border-radius:12px;
            background: rgba(255,255,255,.03);
        }}
        a {{ color: var(--gold-l) !important; }}

        /* 入力中の英語ヒント(Press Enter to apply等)は世界観に合わないため非表示 */
        [data-testid="InputInstructions"] {{ display:none !important; }}

        /* ダイアログ */
        [data-testid="stDialog"] h2 {{ font-size:1.15rem; }}

        /* スマホ最適化 */
        @media (max-width: 640px) {{
            .block-container {{ padding: 2.7rem .6rem 4rem !important; }}
            .stButton > button, .stDownloadButton > button {{ width:100%; padding:.68rem; font-size:.95rem; }}
            [data-testid="stMetricValue"] {{ font-size:1.45rem; }}
            [data-testid="stMetricLabel"] p {{ font-size:.76rem; }}
            .step-title {{ font-size:1.06rem; }}
            .sec-title {{ font-size:1.02rem; }}
            [data-testid="stButtonGroup"] button {{ padding:.38rem .8rem; font-size:.86rem; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    """ブランドヘッダー＋背景の光（控えめな粒）。"""
    motes = []
    spots = [
        (10, 22, 4, 30, 0), (24, 70, 3, 36, 5), (40, 14, 5, 32, 2),
        (58, 80, 4, 38, 7), (74, 30, 3, 34, 3), (88, 64, 5, 40, 1),
        (6, 56, 3, 35, 8), (93, 12, 4, 33, 4),
    ]
    for left, top, size, dur, delay in spots:
        motes.append(
            f'<span class="mote" style="left:{left}%;top:{top}%;'
            f'width:{size}px;height:{size}px;'
            f'animation-duration:{dur}s;animation-delay:-{delay}s"></span>'
        )
    logo = _logo_b64("阿部農園ロゴ.png")
    img = f'<img src="data:image/png;base64,{logo}" alt=""/>' if logo else ""
    st.markdown(
        f'<div class="app-motes">{"".join(motes)}</div>'
        f'<div class="brand-bar">{img}<span class="t">精米・発送管理</span></div>',
        unsafe_allow_html=True,
    )


def _login_css() -> str:
    """ログイン画面専用：濃紺背景＋ゆっくり漂う金色の光（サイトのヒーロー風）。"""
    motes = []
    spots = [
        (8, 18, 5, 22, 0), (16, 72, 3, 28, 4), (27, 40, 7, 34, 2),
        (38, 12, 4, 26, 7), (46, 84, 6, 31, 1), (57, 30, 3, 24, 5),
        (63, 60, 8, 38, 3), (71, 16, 4, 27, 8), (78, 78, 5, 30, 2),
        (84, 44, 6, 33, 6), (90, 24, 3, 25, 9), (93, 66, 7, 36, 1),
        (12, 50, 4, 29, 10), (33, 90, 5, 32, 3), (52, 6, 6, 35, 7),
        (68, 92, 4, 28, 5), (88, 88, 5, 30, 8), (4, 38, 6, 34, 2),
    ]
    for left, top, size, dur, delay in spots:
        motes.append(
            f'<span class="mote" style="left:{left}%;top:{top}%;'
            f'width:{size}px;height:{size}px;'
            f'animation-duration:{dur}s;animation-delay:-{delay}s"></span>'
        )
    return (
        """
        <style>
        .stApp {
            background:
              radial-gradient(1200px 800px at 50% 18%, #2a2c5a 0%, #1b1d3e 45%, #131228 100%) !important;
        }
        header[data-testid="stHeader"] { background: transparent !important; }
        .block-container, [data-testid="stMainBlockContainer"] {
            max-width: 440px !important;
            padding-top: 7vh !important;
        }
        .login-motes {
            position: fixed; inset: 0; z-index: 0; overflow: hidden;
            pointer-events: none;
        }
        .login-motes .mote {
            position: absolute; border-radius: 50%;
            background: radial-gradient(circle, rgba(233,209,140,.95) 0%, rgba(201,162,75,.5) 40%, rgba(201,162,75,0) 70%);
            box-shadow: 0 0 8px 2px rgba(201,162,75,.35);
            opacity: .0;
            animation-name: moteDrift;
            animation-iteration-count: infinite;
            animation-timing-function: ease-in-out;
        }
        @keyframes moteDrift {
            0%   { transform: translate(0,0) scale(.7);     opacity: 0; }
            20%  { opacity: .9; }
            50%  { transform: translate(14px,-26px) scale(1.1); opacity: .7; }
            80%  { opacity: .85; }
            100% { transform: translate(-10px,-52px) scale(.7); opacity: 0; }
        }
        .login-aura {
            position: fixed; left:50%; top:20%; transform:translateX(-50%);
            width: min(620px,90vw); height: 420px; z-index:0; pointer-events:none;
            background: radial-gradient(ellipse at center, rgba(201,162,75,.16) 0%, rgba(201,162,75,0) 65%);
            filter: blur(8px);
        }
        .block-container > div { position: relative; z-index: 2; }

        .login-brand { text-align:center; }
        .login-brand img {
            height: clamp(150px, 34vw, 200px); width:auto;
            filter: brightness(0) invert(1) drop-shadow(0 0 18px rgba(201,162,75,.45));
            opacity: .96;
        }
        .login-title {
            font-family: 'Shippori Mincho', serif; font-weight: 700;
            color: #F4EEDF !important; letter-spacing: .14em;
            font-size: clamp(1.7rem, 6.5vw, 2.4rem);
            margin: .35rem 0 .1rem; text-indent: .14em;
            text-shadow: 0 2px 24px rgba(0,0,0,.35);
        }
        .login-sub {
            color: rgba(244,238,223,.62); font-size:.82rem; letter-spacing:.08em;
            margin-bottom: 0;
        }
        /* 織り菱の飾り罫（ロゴの紋様と呼応する、意味のある区切り） */
        .login-orn {
            display:flex; align-items:center; justify-content:center; gap:12px;
            width:230px; margin: 26px auto 14px;
        }
        .login-orn .l {
            flex:1; height:1px;
            background: linear-gradient(90deg, transparent, rgba(201,162,75,.6));
        }
        .login-orn .l.r {
            background: linear-gradient(270deg, transparent, rgba(201,162,75,.6));
        }
        .login-orn .d {
            width:7px; height:7px; flex:0 0 auto;
            background: linear-gradient(135deg, var(--gold), var(--gold-l));
            transform: rotate(45deg); border-radius:1px;
            box-shadow: 0 0 8px rgba(201,162,75,.55);
        }

        [data-testid="stTextInput"] label { display:none; }
        [data-testid="stTextInput"] > div { max-width: 320px; margin: 0 auto; }
        /* 枠はいちばん外側の1枚だけ（内側は全て透明・枠なしで二重枠を防ぐ） */
        [data-testid="stTextInput"] [data-baseweb="input"] {
            background: rgba(255,255,255,.07) !important;
            border: 1px solid rgba(201,162,75,.45) !important;
            border-radius: 11px !important;
            overflow: hidden;
        }
        [data-testid="stTextInput"] [data-baseweb="input"] > div,
        [data-testid="stTextInput"] [data-baseweb="base-input"],
        [data-testid="stTextInput"] [data-baseweb="input"] button {
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
        }
        [data-testid="stTextInput"] input {
            background: transparent !important;
            color: #F4EEDF !important; text-align:center;
            padding: .5rem .8rem !important; font-size: 1.05rem; letter-spacing:.3em;
            -webkit-text-fill-color: #F4EEDF !important;
        }
        [data-testid="stTextInput"] input::placeholder { color: rgba(244,238,223,.4) !important; letter-spacing:.1em;}
        [data-testid="stTextInput"] svg { color: rgba(244,238,223,.6) !important; }
        /* 入力中の英語ヒント(Press Enter to submit form)を消す */
        [data-testid="InputInstructions"] { display:none !important; }

        [data-testid="stForm"] {
            border: none !important; padding: 0 !important;
            max-width: 320px; margin: 0 auto;
        }
        [data-testid="stFormSubmitButton"] { margin-top: 6px; }
        [data-testid="stForm"] .stButton > button,
        [data-testid="stForm"] button[kind="primaryFormSubmit"] {
            background: linear-gradient(180deg, #C9A24B, #A9842F) !important;
            color: #1b1d3e !important; border: none !important;
            font-weight: 700; letter-spacing: .2em; border-radius: 11px;
            padding: .6rem 1rem; box-shadow: 0 6px 20px rgba(201,162,75,.28);
        }
        [data-testid="stForm"] button[kind="primaryFormSubmit"]:hover {
            filter: brightness(1.06);
        }
        </style>
        <div class="login-motes">""" + "".join(motes) + """</div>
        <div class="login-aura"></div>
        """
    )


def require_login() -> None:
    pw = config.app_password()
    if not pw:
        return
    if st.session_state.get("authed"):
        return

    st.markdown(_login_css(), unsafe_allow_html=True)

    logo = _logo_b64("阿部農園ロゴ.png")
    img = f'<img src="data:image/png;base64,{logo}" alt="阿部農園"/>' if logo else ""
    st.markdown(
        f'<div class="login-brand">{img}'
        f'<div class="login-title">精米・発送管理</div>'
        f'<div class="login-sub">Rice, and the time it makes</div>'
        f'<div class="login-orn"><span class="l"></span><span class="d"></span>'
        f'<span class="l r"></span></div></div>',
        unsafe_allow_html=True,
    )

    with st.form("login_form"):
        entered = st.text_input("パスワード", type="password",
                                placeholder="パスワード")
        if st.form_submit_button("ログイン", type="primary", use_container_width=True):
            if entered == str(pw):
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("パスワードが違います。")
    _login_transition()
    st.stop()


def _login_transition() -> None:
    """ログインボタン押下を即座に検知し、再実行の計算中（古い画面が固まる間）を
    ローディング画面で覆う。Streamlitは再実行中に前のフレームを表示し続けるため、
    クライアント側JSでクリック直後にオーバーレイを親ページへ差し込む。"""
    import streamlit.components.v1 as components

    logo = _logo_b64("阿部農園ロゴ.png")
    img = (f"<img src='data:image/png;base64,{logo}'/>" if logo else "")
    css = ("#login-ov{position:fixed;inset:0;z-index:2147483647;display:flex;"
           "flex-direction:column;align-items:center;justify-content:center;gap:20px;"
           "background:radial-gradient(1200px 800px at 50% 32%,#2a2c5a 0%,#1b1d3e 46%,#131228 100%);"
           "transition:opacity .5s ease;}"
           "#login-ov img{height:104px;filter:brightness(0) invert(1) drop-shadow(0 0 18px rgba(201,162,75,.45));"
           "animation:lovP 1.5s ease-in-out infinite;}"
           "@keyframes lovP{0%,100%{opacity:.82;transform:scale(1)}50%{opacity:1;transform:scale(1.045)}}"
           "#login-ov .lov-dia{width:11px;height:11px;background:linear-gradient(135deg,#C9A24B,#E9D18C);"
           "border-radius:2px;box-shadow:0 0 10px rgba(201,162,75,.6);animation:lovT 1.25s ease-in-out infinite;}"
           "@keyframes lovT{0%{transform:rotate(45deg) scale(1)}50%{transform:rotate(225deg) scale(.7)}"
           "100%{transform:rotate(405deg) scale(1)}}"
           "#login-ov .lov-txt{color:rgba(242,237,224,.7);font-size:.82rem;letter-spacing:.35em;padding-left:.35em;}")
    inner = img + "<span class='lov-dia'></span><div class='lov-txt'>読み込み中…</div>"
    remover = ("(function(){var t=Date.now();var iv=setInterval(function(){"
               "var pw=document.querySelector('input[type=password]');"
               "var er=document.querySelector('[data-testid=stAlert]');"
               "if(!pw||er||Date.now()-t>12000){var o=document.getElementById('login-ov');"
               "if(o){o.style.opacity=0;setTimeout(function(){if(o)o.remove();},500);}"
               "clearInterval(iv);}},150);})();")
    script = (
        "<script>(function(){"
        "var doc=window.parent.document;"
        "function ensure(){if(doc.getElementById('lov-style'))return;"
        "var s=doc.createElement('style');s.id='lov-style';s.textContent=" + repr(css) + ";"
        "doc.head.appendChild(s);}"
        "function show(){if(doc.getElementById('login-ov'))return;ensure();"
        "var o=doc.createElement('div');o.id='login-ov';o.innerHTML=" + repr(inner) + ";"
        "doc.body.appendChild(o);"
        "var s=doc.createElement('script');s.textContent=" + repr(remover) + ";doc.body.appendChild(s);}"
        "function attach(){var bs=doc.querySelectorAll('button');for(var i=0;i<bs.length;i++){"
        "if(bs[i].innerText.trim()==='ログイン'&&!bs[i].__ov){bs[i].__ov=1;bs[i].addEventListener('click',show);}}}"
        "attach();try{var mo=new MutationObserver(attach);mo.observe(doc.body,{childList:true,subtree:true});}catch(e){}"
        "})();</script>"
    )
    components.html(script, height=0)


def section(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="sec-title">{title}</div>', unsafe_allow_html=True)
    if sub:
        st.markdown(f'<div class="sec-sub">{sub}</div>', unsafe_allow_html=True)


def step(n: int, title: str, sub: str = "", done: bool = False, first: bool = False) -> None:
    """作業手順のステップ見出し（番号丸＋タイトル＋説明）。"""
    if not first:
        st.markdown('<hr class="step-rule"/>', unsafe_allow_html=True)
    cls = "step-head step-done" if done else "step-head"
    no = "✓" if done else str(n)
    st.markdown(
        f'<div class="{cls}"><span class="step-no">{no}</span>'
        f'<span class="step-title">{title}</span></div>',
        unsafe_allow_html=True,
    )
    if sub:
        st.markdown(f'<div class="step-sub">{sub}</div>', unsafe_allow_html=True)


def channel_badge(channel: str) -> str:
    c = CHANNELS.get(channel, {"label": channel, "color": "#5a5e7a"})
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


# ヤマトの時間帯コード→表示ラベル（"0000"=指定なしは表示しない）
TIME_LABELS = {
    "0812": "午前中", "1416": "14-16時", "1618": "16-18時",
    "1820": "18-20時", "1921": "19-21時",
}


def order_card(order, extra_html: str = "") -> str:
    """注文カードのHTML（チェックボックス等は呼び出し側で添える）。"""
    qty = order["qty"] or 1
    kg = (order["weight_kg"] or 0) * qty
    kg_txt = f"（{kg:g}kg）" if order["needs_milling"] and kg else ""
    note = f'<div class="o-meta">備考：{order["note"]}</div>' if order.get("note") else ""
    if order.get("tracking_no"):
        note += f'<div class="o-meta">伝票番号　{order["tracking_no"]}</div>'
    # 配達日時の指定があれば目立つ形で表示
    ddate = (order.get("delivery_date") or "").strip()
    tlabel = TIME_LABELS.get((order.get("delivery_time") or "").strip(), "")
    if ddate or tlabel:
        when = "　".join(x for x in (ddate, tlabel) if x)
        note += f'<div class="o-when">配達希望　{when}</div>'
    return (
        f'<div class="o-card">'
        f'<span class="o-name">{order["customer_name"]} 様</span>　'
        f'{status_chip(order)} {channel_badge(order["channel"])}'
        f'<div class="o-line">{order["product_name"]} × {qty} {kg_txt}</div>'
        f'<div class="o-meta">〒{order["zip"]}　{order["address"]}{order["address2"] or ""}</div>'
        f'{note}{extra_html}'
        f'</div>'
    )


def kpi(label: str, value: str, sub: str = "", yen: bool = False) -> str:
    """ダッシュボード用KPIカードのHTML。yen=Trueで先頭に金色の¥を付ける。"""
    head = '<span class="yen">¥</span>' if yen else ""
    sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    return (f'<div class="kpi"><div class="kpi-lbl">{label}</div>'
            f'<div class="kpi-val">{head}{value}</div>{sub_html}</div>')


VIEWS = ["ホーム", "注文", "顧客", "分析", "設定"]


def render_nav() -> str:
    """上部タブナビ。選択中のビュー名を返す。"""
    sel = st.segmented_control(
        "ナビ", VIEWS, default=VIEWS[0], key="nav",
        label_visibility="collapsed",
    )
    return sel or VIEWS[0]


_BOOT_TEMPLATE = """
        <style>
        .boot {
            position: fixed; inset: 0; z-index: 2147483646;
            display: flex; flex-direction: column;
            align-items: center; justify-content: center; gap: 20px;
            background:
              radial-gradient(1200px 800px at 50% 32%, #2a2c5a 0%, #1b1d3e 46%, #131228 100%);
            __FADE__
        }
        @keyframes bootFade { to { opacity: 0; visibility: hidden; } }
        .boot img {
            height: 104px;
            filter: brightness(0) invert(1) drop-shadow(0 0 18px rgba(201,162,75,.45));
            animation: bootPulse 1.5s ease-in-out infinite;
        }
        @keyframes bootPulse {
            0%,100% { opacity: .82; transform: scale(1); }
            50%     { opacity: 1;   transform: scale(1.045); }
        }
        .boot .dia {
            width: 11px; height: 11px;
            background: linear-gradient(135deg, #C9A24B, #E9D18C);
            border-radius: 2px; box-shadow: 0 0 10px rgba(201,162,75,.6);
            animation: bootTurn 1.25s ease-in-out infinite;
        }
        @keyframes bootTurn {
            0%   { transform: rotate(45deg)  scale(1); }
            50%  { transform: rotate(225deg) scale(.7); }
            100% { transform: rotate(405deg) scale(1); }
        }
        .boot .txt {
            color: rgba(242,237,224,.7); font-size: .82rem;
            letter-spacing: .35em; padding-left: .35em;
        }
        </style>
        <div class="boot"__DIVSTYLE__>__IMG__
          <span class="dia"></span>
          <div class="txt">読み込み中…</div>
        </div>
        """


def _boot_overlay(fade: bool = True) -> None:
    """全面ローディング画面を描画する。fade=Trueなら約1秒でとけて消える。

    fade=False は「保持」モード（ログインクリック直後の遷移を覆う用途）。
    フェードしないよう、最優先のインラインstyle(!important)で固定する。
    """
    logo = _logo_b64("阿部農園ロゴ.png")
    img = f'<img src="data:image/png;base64,{logo}" alt=""/>' if logo else ""
    fade_rule = "animation: bootFade .55s ease 1.0s forwards;" if fade else ""
    div_style = ("" if fade else
                 ' style="animation:none!important;opacity:1!important;visibility:visible!important"')
    html = (_BOOT_TEMPLATE.replace("__FADE__", fade_rule)
            .replace("__DIVSTYLE__", div_style).replace("__IMG__", img))
    st.html(html)


def loading_gate() -> None:
    """起動直後・ログイン待ちの間に出す全面ローディング画面。

    セッション初回（およびログイン直後）の1回だけ表示する。濃紺の画面に
    ロゴと織り菱のスピナー、『読み込み中…』を出し、約1秒でとけて消える。
    最初の一瞬に出るちらつき（赤いコード等）も覆い隠す。
    """
    if st.session_state.get("_booted"):
        return
    st.session_state["_booted"] = True
    _boot_overlay(fade=True)


def setup_page() -> None:
    st.set_page_config(
        page_title="精米・発送管理｜阿部農園",
        page_icon=_page_icon(), layout="centered",
        initial_sidebar_state="collapsed",
    )
    inject_css()
    loading_gate()
    require_login()
    render_header()
