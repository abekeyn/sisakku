# -*- coding: utf-8 -*-
"""在庫管理画面（Streamlit）。集計・計算は lib/stock.py にある。

タブ
- ダッシュボード … 在庫・出庫・予測・アラートを一画面で見る
- 在庫の登録・修正 … 棚卸（実数）／入庫・その他の増減／記録の修正・削除
- 出庫の明細 … 何を出庫として数えているか。二重計上の除外など手修正
- 設定 … 保管場所・品種・しきい値
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from . import db, ledger, stock, ui

GOLD = "#C9A24B"
GREEN = "#8FE3C0"
AX_LBL = "#CFCBDD"
AX_LINE = "#3A3D63"
PALETTE = ["#C9A24B", "#6FB7A0", "#7D8FE0", "#D98C8C", "#B58AD9", "#8FA3B8", "#E2B46A"]

_CSS = """
<style>
.stk-card { background: linear-gradient(160deg, rgba(201,162,75,.08), rgba(38,41,73,.55));
            border:1px solid rgba(201,162,75,.25); border-radius:14px; padding:14px 16px; height:100%; }
.stk-card .nm { font-weight:700; color:#F8F3E6; font-size:.98rem; }
.stk-card .big { font-size:1.55rem; font-weight:800; color:#F8F3E6; font-variant-numeric:tabular-nums; }
.stk-card .sub { color: rgba(242,237,224,.62); font-size:.78rem; margin-top:3px; }
.stk-bar { height:8px; border-radius:6px; background:rgba(255,255,255,.10); overflow:hidden; margin:8px 0 4px; }
.stk-bar > span { display:block; height:100%; background:linear-gradient(90deg,#EBCC72,#B5862B); }
.stk-alert { display:flex; gap:10px; align-items:flex-start; padding:9px 0;
             border-bottom:1px solid rgba(255,255,255,.07); font-size:.88rem; color:#F2EDE0; }
.stk-alert:last-child { border-bottom:none; }
.stk-tag { flex:none; padding:1px 9px; border-radius:6px; font-size:.74rem; font-weight:700; white-space:nowrap; }
.stk-tag.danger { background:rgba(224,122,122,.22); color:#F5A9A9; }
.stk-tag.warn   { background:rgba(245,208,140,.18); color:#F5D08C; }
.stk-tag.info   { background:rgba(157,196,255,.16); color:#9DC4FF; }
.stk-risk-高 { color:#F5A9A9; } .stk-risk-中 { color:#F5D08C; } .stk-risk-低 { color:#8FE3C0; }
.stk-fc { border-radius:12px; padding:12px 14px; margin-bottom:10px; font-weight:600; }
.stk-fc.danger { background:rgba(224,122,122,.14); border:1px solid rgba(224,122,122,.35); color:#F5A9A9; }
.stk-fc.warn   { background:rgba(245,208,140,.12); border:1px solid rgba(245,208,140,.35); color:#F5D08C; }
.stk-fc.ok     { background:rgba(143,227,192,.10); border:1px solid rgba(143,227,192,.30); color:#8FE3C0; }
.stk-rec { margin:6px 0 0; padding:0; list-style:none; color:#F2EDE0; font-size:.88rem; }
.stk-rec li { padding:3px 0; } .stk-rec li::before { content:"✔ "; color:#8FE3C0; }
.stk-legend { display:flex; justify-content:space-between; font-size:.86rem; padding:3px 0; color:#F2EDE0; }
.stk-legend i { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:8px; }
</style>
"""


def _n(v: float, digits: int | None = None) -> str:
    """kg表示（3桁区切り）。100kg以上は整数、それ未満は小数1桁。"""
    if digits is None:
        digits = 0 if abs(v) >= 100 else 1
    return f"{v:,.{digits}f}"


def _pct(v: float | None) -> str:
    if v is None:
        return "前月データなし"
    cls = "up" if v >= 0 else "down"
    arrow = "▲" if v >= 0 else "▼"
    return f'前月比 <span class="{cls}">{arrow} {abs(v):.1f}%</span>'


def _mlabel(ym: str, cur_ym: str) -> str:
    base = f"{int(ym[5:])}月"
    return base if ym <= cur_ym else f"{base}(予測)"


def _chart_cfg(ch: alt.Chart | alt.LayerChart) -> alt.Chart:
    return (ch.configure_view(stroke=None)
            .configure(background="rgba(0,0,0,0)")
            .configure_axis(labelColor=AX_LBL, titleColor=AX_LBL, domainColor=AX_LINE,
                            tickColor=AX_LINE, gridColor="rgba(255,255,255,.07)")
            .configure_legend(labelColor=AX_LBL, titleColor=AX_LBL))


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------
def render() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    st.subheader("📦 在庫管理")
    st.caption("在庫は「棚卸（実数）＋入庫 − 出庫」で計算します。出庫は、請求先は**発行済みの領収書**"
               "（領収書のない分は確定済みの請求書。書類がまだ無い出荷は注文のまま）、消費者向けは**出荷確定した注文**を数えます。"
               "月末の数字が違うときは「在庫の登録・修正」で直せます。")

    cfg = stock.load_config()
    fy_now = stock.crop_year_of(stock.today())
    fys = [fy_now - i for i in range(4)]
    CUSTOM = "自由に指定"

    c_fy, c_flt = st.columns([1, 2])
    period = c_fy.selectbox("対象期間", fys + [CUSTOM], key="stk_fy",
                            format_func=lambda y: y if y == CUSTOM else f"{y}年産（{y}/10〜{y + 1}/9）")
    period_kw: dict = {}
    if period == CUSTOM:
        d0, d1 = stock.crop_year_range(fy_now)
        rng = c_fy.date_input("期間（開始日〜終了日）", value=(d0, d1), key="stk_range",
                              help="月単位で集計します（開始日・終了日を含む月）。最大36か月。")
        if isinstance(rng, (list, tuple)) and len(rng) == 2 and rng[0] <= rng[1]:
            period_kw = {"start": rng[0], "end": rng[1]}
        else:
            c_fy.caption("開始日と終了日の両方を選んでください。")
            period_kw = {"start": d0, "end": d1}
        if len(stock.range_months(period_kw["start"], period_kw["end"], 999)) > 36:
            c_fy.caption("36か月を超える分は表示しません。")
    else:
        period_kw = {"fy": period}
    with c_flt.expander("絞り込み（品種・保管場所・形態）"):
        f2, f3, f4 = st.columns(3)
        varieties = ["すべて"] + sorted({*cfg["varieties"], cfg["default_variety"]})
        variety = f2.selectbox("品種", varieties, key="stk_var")
        locs = ["すべて"] + stock.location_names(cfg) + [stock.UNSET_LOC]
        location = f3.selectbox("保管場所", locs, key="stk_loc")
        form = f4.selectbox("形態", ["すべて", "玄米", "精米"], key="stk_form")

    m = stock.build(**period_kw, variety=variety, location=location, form=form)
    unfiltered = (variety, location, form) == ("すべて", "すべて", "すべて")
    base = m if unfiltered else stock.build(**period_kw)

    t_dash, t_edit, t_out, t_cl, t_set = st.tabs(
        ["ダッシュボード", "在庫の登録・修正", "出庫の明細", "卸先の月間使用量", "設定"])
    with t_dash:
        _dashboard(m)
    with t_edit:
        _editor(base, cfg)
    with t_out:
        _out_detail(base, cfg)
    with t_cl:
        _client_plan_editor(base, cfg)
    with t_set:
        _settings(cfg)


# ---------------------------------------------------------------------------
# ダッシュボード
# ---------------------------------------------------------------------------
def _dashboard(m: dict) -> None:
    if not m["has_counts"]:
        st.info("まだ現在の在庫が登録されていません。「在庫の登録・修正」タブで、保管場所ごとの"
                "実数（棚卸）を入れると、在庫・欠品の見通しが正しく表示されます。"
                "（出庫の実績や推移は、登録前でも下に表示されます）")

    known = m["has_base"]
    cur_ym = m["cur_ym"]

    _plan_panel(m)
    st.write("")
    _client_usage_panel(m)
    st.write("")
    _plan_history_panel(m)
    st.write("")

    def stock_val(v: float) -> str:
        return f'{_n(v)}<span style="font-size:1rem;font-weight:600"> kg</span>' if known else "—"

    risk_html = "—"
    risk_sub = "棚卸を登録すると表示されます" if not known else "出庫のペースから判定"
    if m["risk"]:
        risk_html = f'<span class="stk-risk-{m["risk"]}">{m["risk"]}</span>'
        risk_sub = m["forecast_text"] and (
            (f'{stock._period_text(m["run_out"])}ごろに在庫がなくなる見込み' if m["risk"] != "低" else "当面は問題ありません")
            if m["run_out"] else "")

    k = st.columns(3)
    k[0].markdown(ui.kpi("総在庫量", stock_val(m["total"]),
                         (f'{_pct(m["total_pct"])}<br>玄米 {_n(m["by_form"]["玄米"])}kg ／ 精米 {_n(m["by_form"]["精米"])}kg'
                          if known else "棚卸を登録すると表示されます")),
                  unsafe_allow_html=True)
    k[1].markdown(ui.kpi("利用可能在庫", stock_val(m["available"]),
                         "総在庫から確保分を引いた量" if known else "棚卸を登録すると表示されます"),
                  unsafe_allow_html=True)
    k[2].markdown(ui.kpi("確保在庫（未出荷の注文）",
                         f'{_n(m["reserved_kg"])}<span style="font-size:1rem;font-weight:600"> kg</span>',
                         f'未出荷の注文 {m["reserved_n"]}件分'),
                  unsafe_allow_html=True)
    st.write("")
    k = st.columns(3)
    mom = m["mom_pct"]
    mom_sub = ("前月の同じ時期のデータなし" if mom is None else
               f'前月同時期比 <span class="{"up" if mom >= 0 else "down"}">{"▲" if mom >= 0 else "▼"} {abs(mom):.1f}%</span>')
    k[0].markdown(ui.kpi("今月の出庫量", f'{_n(m["mtd"])}<span style="font-size:1rem;font-weight:600"> kg</span>', mom_sub),
                  unsafe_allow_html=True)
    ml = m["months_left"]
    k[1].markdown(ui.kpi("在庫継続見込み",
                         (f'{ml:.1f}<span style="font-size:1rem;font-weight:600"> か月</span>' if ml is not None and known else "—"),
                         f'直近3か月の平均 {_n(m["monthly_avg"])}kg／月'), unsafe_allow_html=True)
    k[2].markdown(ui.kpi("欠品リスク", risk_html, risk_sub), unsafe_allow_html=True)

    # ---- 推移と予測 ----
    ui.section("出庫量の推移と予測", "棒＝月ごとの出庫量（薄い棒は予測）／線＝在庫残量")
    st.altair_chart(_trend_chart(m), use_container_width=True)

    # ---- アラート ＋ 予測 ＋ 構成 ----
    a, b, c = st.columns([1.35, 1.0, 0.95])
    with a:
        ui.section("アラート・お知らせ")
        _alerts(m)
    with b:
        ui.section("在庫予測")
        _forecast(m)
    with c:
        ui.section("在庫の構成")
        _composition(m)

    # ---- 取引先別 月次表 ----
    ui.section("取引先別 月次出庫実績", "単位：kg／将来の月は直近3か月の平均からの予測")
    _customer_table(m)

    # ---- 保管場所別・出庫先の流れ（普段は閉じておく） ----
    with st.expander("保管場所別の在庫・在庫の流れ"):
        c1, c2 = st.columns([1, 1.25])
        with c1:
            ui.section("保管場所別の在庫")
            _locations(m)
        with c2:
            ui.section("在庫の流れ（保管場所 → 出庫先）", f'{m["label"]}の出庫量')
            svg = _sankey_svg(m)
            if svg:
                st.markdown(svg, unsafe_allow_html=True)
            else:
                st.caption("この期間の出庫がまだありません。")


def _kg(v: float) -> str:
    return f'{_n(v)}<span style="font-size:1rem;font-weight:600"> kg</span>'


def _plan_panel(m: dict) -> None:
    """月の使える量（白米換算）と、今月あと何kg売れるか・売らないと余る量。"""
    pl = m["plan"]
    ui.section("今月の販売キャパ（白米換算）",
               f'玄米は白米に換算（歩留まり{pl["yield"] * 100:.0f}%）。次の新米（{int(pl["harvest_ym"][5:])}月）までの'
               f'{pl["n_months"]}か月で割った「1か月に使える量」です')
    if not m["has_base"] or pl["start_w"] <= 0:
        st.info("在庫がまだ登録されていません。「在庫の登録・修正」で月末在庫を入れると、ここに月の使える量が出ます。")
        return
    k = st.columns(4)
    k[0].markdown(ui.kpi("全体の在庫（白米換算）", _kg(pl["total_w"]),
                         f'玄米 {_n(pl["genmai"])}kg → 白米 {_n(pl["genmai_w"])}kg ＋ 精米 {_n(pl["seimai"])}kg'),
                  unsafe_allow_html=True)
    k[1].markdown(ui.kpi("1か月に使える量", _kg(pl["target_w"]),
                         f'月初の在庫 {_n(pl["start_w"])}kg ÷ {pl["n_months"]}か月'), unsafe_allow_html=True)
    left = pl["cap_left"]
    sub_left = f'今月ここまで {_n(pl["mtd_w"])}kg 出庫' + (
        f'／うち出荷待ち {_n(pl["reserved_w"])}kg' if pl["reserved_w"] > 0 else "")
    k[2].markdown(ui.kpi("今月あと使える量", (_kg(left) if left >= 0 else
                                          f'超過 {_kg(-left)}'), sub_left), unsafe_allow_html=True)
    sp = pl["surplus_w"]
    if sp is None:
        k[3].markdown(ui.kpi("新米までの見込み", "—", ""), unsafe_allow_html=True)
    elif sp >= 0:
        k[3].markdown(ui.kpi("新米までの見込み", f'余り {_kg(sp)}',
                             f'今のペース 月{_n(pl["pace_w"])}kg のままなら'), unsafe_allow_html=True)
    else:
        k[3].markdown(ui.kpi("新米までの見込み", f'不足 {_kg(-sp)}',
                             f'今のペース 月{_n(pl["pace_w"])}kg のままなら'), unsafe_allow_html=True)

    ratio = min(max(pl["mtd_w"] / pl["target_w"], 0.0), 1.0) if pl["target_w"] > 0 else 0.0
    st.markdown(
        f'<div class="stk-card" style="margin-top:10px"><div class="nm">今月の使用状況</div>'
        f'<div class="stk-bar"><span style="width:{ratio * 100:.0f}%"></span></div>'
        f'<div class="sub">{_n(pl["mtd_w"])}kg 使用 ／ {_n(pl["target_w"])}kg（'
        f'{pl["mtd_w"] / pl["target_w"] * 100 if pl["target_w"] else 0:.0f}%）</div></div>',
        unsafe_allow_html=True)

    if sp is not None and sp > 0:
        gap = max(pl["need_gap"], 0)
        st.markdown(
            f'<div class="stk-fc warn">このペースだと、次の新米までに白米換算で約{_n(sp)}kg余ります</div>'
            f'<div class="stk-card"><div class="sub">余らせないためには、月{_n(pl["target_w"])}kg'
            f'（今のペースより月{_n(gap)}kg多く）売る必要があります。'
            f'今月はあと{_n(max(left, 0))}kg売れば計画どおりです。</div></div>',
            unsafe_allow_html=True)
    elif sp is not None:
        st.markdown(
            f'<div class="stk-fc danger">このペースだと、新米の前に白米換算で約{_n(-sp)}kg足りなくなります</div>'
            f'<div class="stk-card"><div class="sub">1か月に使える量は{_n(pl["target_w"])}kgです。'
            f'今のペース（月{_n(pl["pace_w"])}kg）を抑えるか、追加の仕入を検討してください。</div></div>',
            unsafe_allow_html=True)
    if not pl["has_hist"]:
        st.caption("※ 今月より前の出庫実績がまだ少ないため、今月ここまでの出庫を月のペースとして見ています。")

    rows = [{"月": "月初", "ペース": "今のペース", "在庫kg": round(pl["start_w"], 1)},
            {"月": "月初", "ペース": "売り切る計画", "在庫kg": round(pl["start_w"], 1)}]
    for x in pl["line"]:
        lab = f'{int(x["ym"][5:])}月末'
        rows.append({"月": lab, "ペース": "今のペース", "在庫kg": round(x["pace"], 1)})
        rows.append({"月": lab, "ペース": "売り切る計画", "在庫kg": round(x["target"], 1)})
    order = ["月初"] + [f'{int(x["ym"][5:])}月末' for x in pl["line"]]
    df = pd.DataFrame(rows)
    ch = alt.Chart(df).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=40)).encode(
        x=alt.X("月:N", sort=order, axis=alt.Axis(title=None, labelAngle=0)),
        y=alt.Y("在庫kg:Q", axis=alt.Axis(title="白米換算の在庫 (kg)")),
        color=alt.Color("ペース:N", scale=alt.Scale(
            domain=["今のペース", "売り切る計画"], range=[GOLD, GREEN]),
            legend=alt.Legend(orient="top", title=None, direction="horizontal")),
        tooltip=[alt.Tooltip("月:N"), alt.Tooltip("ペース:N"), alt.Tooltip("在庫kg:Q", format=",.0f", title="kg")])
    st.altair_chart(_chart_cfg(ch.properties(height=240)), use_container_width=True)


def _client_usage_panel(m: dict) -> None:
    """卸先ごとの「月の使用量（計画）」に対する今月の使用状況と、このままだとどれくらい余るか。"""
    u = m["client_usage"]
    pl = m["plan"]
    now = m["now"]
    ui.section("卸先の今月の使用状況（月の使用量に対して）",
               f'{now.month}月の{now.day}日まで。使用量は白米換算・領収書（なければ確定済みの請求書）で数えています')
    planned = [d for d in u["rows"] if d["plan"] > 0]
    if not planned:
        st.info("卸先ごとの月の使用量がまだ登録されていません。「卸先の月間使用量」タブで入れると、"
                "ここに今月の使用状況と余る量が出ます。")
        return
    t = u["total"]
    k = st.columns(4)
    k[0].markdown(ui.kpi("卸先の月の使用量（計画）", _kg(t["plan"]), f"{len(planned)}か所の合計"),
                  unsafe_allow_html=True)
    k[1].markdown(ui.kpi("今月ここまでの使用", _kg(t["used"]),
                         f'計画の {t["used"] / t["plan"] * 100 if t["plan"] else 0:.0f}%'),
                  unsafe_allow_html=True)
    k[2].markdown(ui.kpi("月末までの見込み", _kg(t["proj"]), "ここまで＋直近3か月の平均ペース"),
                  unsafe_allow_html=True)
    sp = t["surplus"]
    k[3].markdown(ui.kpi("このままだと今月分は", (f'余り {_kg(sp)}' if sp >= 0 else f'超過 {_kg(-sp)}'),
                         "計画に対する、今月の使い残し" if sp >= 0 else "計画を上回る見込み"),
                  unsafe_allow_html=True)

    if pl.get("month_room") is not None and pl["start_w"] > 0:
        room = pl["month_room"]
        msg = (f'卸の計画（{_n(pl["wholesale_plan"])}kg）＋消費者向けの月平均（{_n(pl["consumer_avg"])}kg）'
               f'＝{_n(pl["month_expected"])}kg に対して、1か月に使える量は{_n(pl["target_w"])}kgです。')
        if room >= 0:
            msg += f'計画どおり売れても、約{_n(room)}kg枠が余ります。'
        else:
            msg += f'計画どおり売れると、約{_n(-room)}kg枠を超えます。'
        st.markdown(f'<div class="stk-card" style="margin:10px 0"><div class="sub">{msg}</div></div>',
                    unsafe_allow_html=True)

    frac = u["frac"]
    html = []
    for d in planned:
        ratio = min(d["used"] / d["plan"], 1.0) if d["plan"] else 0.0
        over = d["used"] > d["plan"]
        sp = d["surplus"]
        if d["used"] >= d["plan"]:
            status = f'<span style="color:#F5A9A9">計画を{_n(d["used"] - d["plan"])}kg超えました</span>'
        elif sp is not None and sp > 0.5:
            status = f'あと{_n(d["remaining"])}kg ／ このままだと<b>{_n(sp)}kg余る</b>見込み'
        elif sp is not None and sp < -0.5:
            status = f'あと{_n(d["remaining"])}kg ／ このままだと<span style="color:#F5A9A9">{_n(-sp)}kg超える</span>見込み'
        else:
            status = f'あと{_n(d["remaining"])}kg ／ ほぼ計画どおり'
        bar_color = "#E07A7A" if over else "#EBCC72"
        html.append(
            f'<div class="stk-card" style="margin-bottom:8px;padding:10px 14px">'
            f'<div style="display:flex;justify-content:space-between;gap:12px">'
            f'<span class="nm">{d["name"]}</span>'
            f'<span style="font-variant-numeric:tabular-nums"><b>{_n(d["used"])}</b> / {_n(d["plan"])} kg</span></div>'
            f'<div class="stk-bar" style="position:relative"><span style="width:{ratio * 100:.0f}%;'
            f'background:{bar_color}"></span>'
            f'<i style="position:absolute;left:{frac * 100:.0f}%;top:-2px;height:12px;width:2px;'
            f'background:#9DC4FF" title="今日の位置"></i></div>'
            f'<div class="sub">{status}</div></div>')
    st.markdown("".join(html), unsafe_allow_html=True)
    st.caption("青い線は「今月の経過（今日の位置）」です。棒がそれより左なら、計画より遅れています。")
    unreg = [d["name"] for d in planned if d.get("unregistered")]
    if unreg:
        st.caption("請求先に未登録の卸先（" + "、".join(unreg) + "）は、使用量の集計ができないため、計画だけを表示しています。"
                   "請求先マスタに登録すると、使用状況も出ます。")
    extra = [d for d in u["rows"] if d["plan"] <= 0 and d["used"] > 0]
    if extra:
        names = "、".join(f'{d["name"]}（{_n(d["used"])}kg）' for d in extra)
        st.caption(f"月の使用量が未設定の卸先（今月使った分）：{names}")


def _plan_history_panel(m: dict) -> None:
    """毎月、想定（卸先の月の使用量の合計）に対して、実際にいくら使って、いくら余ったか。"""
    h = m["plan_history"]
    rows = h["rows"]
    ui.section("毎月の想定に対する余り",
               "想定＝卸先の月の使用量の合計。余り＝想定 − 実績（白米換算）。マイナスは想定を超えた分です")
    if not rows or rows[0]["plan"] <= 0:
        st.caption("卸先の月の使用量がまだ登録されていません。「卸先の月間使用量」タブで入れると出ます。")
        return
    cur_ym = m["cur_ym"]
    df = pd.DataFrame([{
        "月": f'{int(r["ym"][5:])}月' + ("（今月・途中）" if r["cur"] else ""),
        "想定kg": round(r["plan"]), "実績kg": round(r["used"]),
        "余りkg": round(r["surplus"]),
        "月末の見込み余りkg": round(r["proj_surplus"]) if r["cur"] else None,
        "累計の余りkg": round(r["cum"]) if not r["cur"] else None,
    } for r in rows])
    cum_done = sum(r["surplus"] for r in rows if not r["cur"])
    done = [r for r in rows if not r["cur"]]
    recent = h["recent"]
    prev, cur = recent[1], recent[0]

    def _sur(v: float) -> str:
        return _kg(v) if v >= 0 else f'超過 {_kg(-v)}'

    k = st.columns(3)
    k[0].markdown(ui.kpi(f'先月（{int(prev["ym"][5:])}月）の余り', _sur(prev["surplus"]),
                         f'想定 {_n(prev["plan"])}kg − 実績 {_n(prev["used"])}kg'), unsafe_allow_html=True)
    k[1].markdown(ui.kpi(f'今月（{int(cur["ym"][5:])}月）の余り（ここまで）', _sur(cur["surplus"]),
                         f'月末の見込みは {_n(rows[-1]["proj_surplus"])}kg' if rows else ""),
                  unsafe_allow_html=True)
    if done:
        k[2].markdown(ui.kpi("今期の余り（終わった月の累計）", _sur(cum_done), f'{len(done)}か月分（今月は除く）'),
                      unsafe_allow_html=True)
    else:
        k[2].markdown(ui.kpi("毎月の想定", _kg(rows[0]["plan"]), "卸先の月の使用量の合計"), unsafe_allow_html=True)
    st.caption("先月などの過去の月は、いまの想定（卸先の月の使用量）に対して計算しています。")

    # 月を選んで、その単月の卸先別の想定・実績・余りを見る（初期値は先月）
    sel_opts = [r["ym"] for r in recent[1:7]] + [cur["ym"]]
    pick = st.segmented_control(
        "単月の内訳を見る", sel_opts, default=prev["ym"], key="stk_ph_month",
        format_func=lambda ym: f'{int(ym[5:])}月' + ("（今月）" if ym == cur["ym"] else ""),
        label_visibility="collapsed") or prev["ym"]
    rec = next(r for r in recent if r["ym"] == pick)
    crow = [{"卸先": c["name"] + ("（未登録）" if c["unregistered"] else ""),
             "想定kg": round(c["plan"]), "実績kg": round(c["used"]), "余りkg": round(c["plan"] - c["used"])}
            for c in rec["clients"]]
    crow.append({"卸先": "合計", "想定kg": round(rec["plan"]), "実績kg": round(rec["used"]),
                 "余りkg": round(rec["surplus"])})
    st.markdown(f'**{int(pick[5:])}月の単月：想定{_n(rec["plan"])}kg − 実績{_n(rec["used"])}kg ＝ '
                f'{"余り " if rec["surplus"] >= 0 else "超過 "}{_n(abs(rec["surplus"]))}kg**'
                + ("（今月は途中のため、ここまでの実績）" if pick == cur["ym"] else ""))
    st.dataframe(pd.DataFrame(crow).style.format("{:,.0f}", subset=["想定kg", "実績kg", "余りkg"]),
                 use_container_width=True, hide_index=True)

    cdf = pd.DataFrame([{"月": f'{int(r["ym"][5:])}月', "余りkg": round(r["surplus"], 1),
                         "区分": "今月（途中）" if r["cur"] else ("余り" if r["surplus"] >= 0 else "超過")}
                        for r in rows])
    order = list(cdf["月"])
    bars = alt.Chart(cdf).mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5, size=38).encode(
        x=alt.X("月:N", sort=order, axis=alt.Axis(title=None, labelAngle=0)),
        y=alt.Y("余りkg:Q", axis=alt.Axis(title="想定に対する余り (kg)")),
        color=alt.Color("区分:N", scale=alt.Scale(domain=["余り", "超過", "今月（途中）"],
                                                  range=[GOLD, "#E07A7A", "rgba(201,162,75,.45)"]),
                        legend=alt.Legend(orient="top", title=None, direction="horizontal")),
        tooltip=[alt.Tooltip("月:N"), alt.Tooltip("余りkg:Q", format=",.0f", title="余り(kg)")])
    labels = alt.Chart(cdf).mark_text(dy=-8, color="#F2EDE0", fontSize=11).encode(
        x=alt.X("月:N", sort=order), y="余りkg:Q", text=alt.Text("余りkg:Q", format=",.0f"))
    st.altair_chart(_chart_cfg((bars + labels).properties(height=220)), use_container_width=True)
    num_cols = [c for c in df.columns if c != "月"]
    st.dataframe(df.style.format("{:,.0f}", subset=num_cols, na_rep="－"),
                 use_container_width=True, hide_index=True)
    if h["unregistered"]:
        st.caption("※ 請求先に未登録の卸先（" + "、".join(h["unregistered"]) + "）は、実績を集計できないので、"
                   "想定がそのまま余りに入っています。")
    with st.expander("卸先ごとの余り（月別）"):
        mrows = []
        for c in h["per_client"]:
            row = {"卸先": c["name"] + ("（未登録）" if c["unregistered"] else ""), "想定kg": round(c["plan"])}
            for r in rows:
                row[f'{int(r["ym"][5:])}月'] = round(c["months"].get(r["ym"], 0.0))
            mrows.append(row)
        tot = {"卸先": "合計", "想定kg": round(sum(c["plan"] for c in h["per_client"]))}
        for r in rows:
            tot[f'{int(r["ym"][5:])}月'] = round(sum(c["months"].get(r["ym"], 0.0) for c in h["per_client"]))
        mrows.append(tot)
        st.dataframe(pd.DataFrame(mrows), use_container_width=True, hide_index=True)
        st.caption("数字は 想定 − 実績（kg）。マイナスは想定を超えて使った分です。")


def _client_plan_editor(m: dict, cfg: dict) -> None:
    """卸先ごとの月の使用量（マスタ）。ダッシュボードの「卸先の今月の使用状況」の元になる。"""
    from . import billing
    ui.section("卸先の月間使用量（マスタ）",
               "卸先ごとに、1か月に使う量（kg・白米換算）を入れます。ダッシュボードで、今月どれくらい使っていて、"
               "このままだとどれくらい余るかを見られます。")
    # 「有効」は月末の自動作成の対象かどうかだけを表す（手入力で請求する卸先は無効のことが多い）ので、ここでは全員出す
    clients = list(billing.get_clients())
    plans = cfg.get("client_monthly_kg") or {}
    usage = {d["id"]: d for d in m["client_usage"]["rows"]}
    ver = st.session_state.setdefault("stk_cl_ver", 0)
    if st.button("空欄を直近3か月の平均で埋める", key=f"stk_cl_fill_{ver}"):
        for c in clients:
            avg = (usage.get(c["id"]) or {}).get("avg3", 0.0)
            if not plans.get(c["id"]) and avg > 0:
                plans[c["id"]] = round(avg)
        cfg["client_monthly_kg"] = plans
        stock.save_config(cfg)
        st.session_state["stk_cl_ver"] = ver + 1
        st.rerun()
    rows = [{"id": c["id"], "卸先": _short_name(c.get("name", "")),
             "月の使用量kg": float(plans.get(c["id"]) or 0),
             "直近3か月の平均kg": round((usage.get(c["id"]) or {}).get("avg3", 0.0), 1),
             "今月ここまでkg": round((usage.get(c["id"]) or {}).get("used", 0.0), 1)}
            for c in clients]
    rows.sort(key=lambda r: (-r["月の使用量kg"], -r["直近3か月の平均kg"], r["卸先"]))
    ed = st.data_editor(
        pd.DataFrame(rows), hide_index=True, use_container_width=True, key=f"stk_cl_{ver}",
        disabled=["卸先", "直近3か月の平均kg", "今月ここまでkg"],
        column_config={
            "id": None,
            "月の使用量kg": st.column_config.NumberColumn("月の使用量(kg)　← ここに入れる", min_value=0.0, step=5.0, format="%.0f"),
            "直近3か月の平均kg": st.column_config.NumberColumn("参考：直近3か月の平均(kg)", format="%.1f"),
            "今月ここまでkg": st.column_config.NumberColumn("今月ここまで(kg)", format="%.1f"),
        })
    ym_opts = []
    _y, _m = stock.today().year, stock.today().month
    for _ in range(24):
        ym_opts.append(f"{_y}-{_m:02d}")
        _m -= 1
        if _m == 0:
            _y, _m = _y - 1, 12
    cur_start = cfg.get("plan_start_ym") or stock.fy_months(stock.crop_year_of(stock.today()))[0]
    if cur_start not in ym_opts:
        ym_opts.append(cur_start)
    start_ym = st.selectbox("この月の使用量を、いつから使う想定にするか", ym_opts,
                            index=ym_opts.index(cur_start), key=f"stk_cl_start_{ver}",
                            help="「毎月の想定に対する余り」は、この月から数えます（それより前の月は想定がなかったとみなす）。")
    st.markdown("**請求先マスタにない卸先**（まだ取引・請求が始まっていない先、「その他」など）")
    extra_rows = [{"卸先": k[5:], "月の使用量kg": float(v)} for k, v in plans.items()
                  if k.startswith("name:") and _f_ui(v) > 0]
    ed2 = st.data_editor(
        pd.DataFrame(extra_rows, columns=["卸先", "月の使用量kg"]),
        hide_index=True, use_container_width=True, num_rows="dynamic", key=f"stk_cl_x_{ver}",
        column_config={"卸先": st.column_config.TextColumn("卸先の名前"),
                       "月の使用量kg": st.column_config.NumberColumn("月の使用量(kg)", min_value=0.0, step=5.0, format="%.0f")})
    st.caption("「その他」という名前で入れると、一般のお客様（消費者向け）の出庫を実績として照らし合わせます。"
               "0または空欄は計画なしとして扱います。卸先の追加・名前の変更は、請求タブの請求先マスタで行います。")

    # 合計（保存前の入力どおり）：月間と、12か月ぶんの年間必要量（白米換算）
    m_total = sum(float(v) for v in ed["月の使用量kg"] if not pd.isna(v)) + \
        sum(float(v) for v in ed2["月の使用量kg"] if not pd.isna(v))
    st.markdown(
        f'<div class="stk-card"><div class="nm">合計（白米換算）</div>'
        f'<div class="big">月 {m_total:,.0f} kg　／　年間 {m_total * 12:,.0f} kg（{m_total * 12 / 1000:,.2f}トン）</div>'
        f'<div class="sub">月の合計 × 12か月</div></div>', unsafe_allow_html=True)

    if st.button("月の使用量を保存", type="primary", key=f"stk_cl_save_{ver}"):
        new = {}
        for _, r in ed.iterrows():
            v = r["月の使用量kg"]
            if not pd.isna(v) and float(v) > 0:
                new[r["id"]] = float(v)
        for _, r in ed2.iterrows():
            nm = (r["卸先"] or "").strip() if isinstance(r["卸先"], str) else ""
            v = r["月の使用量kg"]
            if nm and not pd.isna(v) and float(v) > 0:
                new["name:" + nm] = float(v)
        for k in [k for k in plans if not k.startswith("name:") and k not in {c["id"] for c in clients}]:
            new[k] = plans[k]       # 非表示（無効）の請求先の分は消さない
        cfg["client_monthly_kg"] = new
        cfg["plan_start_ym"] = start_ym
        stock.save_config(cfg)
        st.session_state["stk_cl_ver"] = ver + 1
        st.success("保存しました。ダッシュボードに反映されます。")
        st.rerun()


def _f_ui(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _short_name(n: str) -> str:
    return (n or "").replace("　", " ").strip().rstrip("様").strip()


def _locations(m: dict) -> None:
    cfg = m["cfg"]
    names = stock.location_names(cfg)
    for extra in m["by_loc"]:
        if extra not in names and abs(m["by_loc"][extra]["total"]) > 0.05:
            names.append(extra)
    if not names:
        st.caption("保管場所が未設定です。「設定」タブで登録できます。")
        return
    total = sum(max(d["total"], 0) for d in m["by_loc"].values()) or 1.0
    if not m["has_base"]:
        # 棚卸も入庫も無い間は、出庫だけ引いたマイナスの数字になるので出さない
        for name in names:
            st.markdown(
                f'<div class="stk-card" style="margin-bottom:10px"><div class="nm">{name}</div>'
                f'<div class="big">—</div><div class="sub">棚卸を登録すると表示されます</div></div>',
                unsafe_allow_html=True)
        return
    for name in names:
        d = m["by_loc"].get(name, {"total": 0.0, "玄米": 0.0, "精米": 0.0})
        cap = next((l.get("capacity_kg") or 0 for l in cfg["locations"] if l["name"] == name), 0)
        if cap:
            ratio, note = min(max(d["total"], 0) / cap, 1.0), f"容量 {_n(cap)}kg の {max(d['total'], 0) / cap * 100:.0f}%"
        else:
            ratio, note = max(d["total"], 0) / total, f"在庫全体の {max(d['total'], 0) / total * 100:.0f}%"
        st.markdown(
            f'<div class="stk-card" style="margin-bottom:10px"><div class="nm">{name}</div>'
            f'<div class="big">{_n(d["total"])} <span style="font-size:.9rem;font-weight:600">kg</span></div>'
            f'<div class="stk-bar"><span style="width:{ratio * 100:.0f}%"></span></div>'
            f'<div class="sub">{note}　｜　玄米 {_n(d["玄米"])}kg ／ 精米 {_n(d["精米"])}kg</div></div>',
            unsafe_allow_html=True)


def _trend_chart(m: dict):
    months, cur = m["months"], m["cur_ym"]
    order = [_mlabel(x, cur) for x in months]
    rows = []
    for x in months:
        past = x <= cur
        v = m["out_month"].get(x, 0.0) if past else (m["monthly_avg"] if m["monthly_avg"] else 0.0)
        rows.append({"月": _mlabel(x, cur), "出庫kg": round(v, 1),
                     "区分": "実績" if past else "予測"})
    df = pd.DataFrame(rows)
    bars = alt.Chart(df).mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6).encode(
        x=alt.X("月:N", sort=order, axis=alt.Axis(title=None, labelAngle=0, labelFontSize=12)),
        y=alt.Y("出庫kg:Q", axis=alt.Axis(title="出庫量 (kg)")),
        color=alt.Color("区分:N", scale=alt.Scale(domain=["実績", "予測"], range=[GOLD, "rgba(201,162,75,.42)"]),
                        legend=alt.Legend(orient="top", title=None, direction="horizontal")),
        tooltip=[alt.Tooltip("月:N"), alt.Tooltip("出庫kg:Q", title="出庫量(kg)", format=",.1f"), alt.Tooltip("区分:N")])
    labels = alt.Chart(df).mark_text(dy=-8, color="#F2EDE0", fontSize=11).encode(
        x=alt.X("月:N", sort=order), y="出庫kg:Q",
        text=alt.Text("出庫kg:Q", format=",.0f"))
    layer = bars + labels
    if m["stock_line"] and m["has_base"]:
        sdf = pd.DataFrame([{"月": _mlabel(x, cur), "在庫kg": v, "区分": "予測" if fc else "実績"}
                            for x, v, fc in m["stock_line"]])
        line = alt.Chart(sdf).mark_line(color=GREEN, strokeWidth=2, point=alt.OverlayMarkDef(color=GREEN, size=45)).encode(
            x=alt.X("月:N", sort=order),
            y=alt.Y("在庫kg:Q", axis=alt.Axis(title="在庫残量 (kg)", orient="right", grid=False)),
            tooltip=[alt.Tooltip("月:N"), alt.Tooltip("在庫kg:Q", title="在庫(kg)", format=",.1f")])
        layer = alt.layer(layer, line).resolve_scale(y="independent")
    return _chart_cfg(layer.properties(height=300))


def _alerts(m: dict) -> None:
    if not m["alerts"]:
        st.caption("現在、お知らせはありません。")
        return
    html = "".join(
        f'<div class="stk-alert"><span class="stk-tag {a["level"]}">{a["tag"]}</span><span>{a["text"]}</span></div>'
        for a in m["alerts"])
    st.markdown(f'<div class="stk-card">{html}</div>', unsafe_allow_html=True)


def _forecast(m: dict) -> None:
    risk = m["risk"]
    if risk is None:
        st.markdown('<div class="stk-card"><div class="sub">在庫を登録し、出庫の実績がたまると、'
                    '欠品の見通しをここに表示します。</div></div>', unsafe_allow_html=True)
        return
    cls = {"高": "danger", "中": "warn", "低": "ok"}[risk]
    head = {"高": f'{stock._period_text(m["run_out"]) if m["run_out"] else ""}ごろに在庫が底をつく可能性があります',
            "中": f'{stock._period_text(m["run_out"]) if m["run_out"] else ""}ごろに在庫が少なくなる見込みです',
            "低": "当面、在庫は足りる見込みです"}[risk]
    recs = []
    if risk in ("高", "中"):
        if m["by_form"].get("玄米", 0) > 0 and m["reserved_by_form"].get("精米", 0) + m["monthly_avg"] > m["by_form"].get("精米", 0):
            recs.append("玄米のとう精（精米）を進める")
        recs.append("追加の仕入・新米の入荷予定を確認する")
        recs.append("出荷量の調整（優先順位の見直し）を検討する")
    else:
        recs.append("現在の出庫ペースなら追加の対応は不要です")
        recs.append("月に1回ほど棚卸をして、実数とのずれを直す")
    st.markdown(
        f'<div class="stk-fc {cls}">{head}</div>'
        f'<div class="stk-card"><div class="sub">{m["forecast_text"] or ""}</div>'
        f'<div class="nm" style="margin-top:10px">おすすめの対応</div>'
        f'<ul class="stk-rec">{"".join(f"<li>{r}</li>" for r in recs)}</ul></div>',
        unsafe_allow_html=True)


def _composition(m: dict) -> None:
    mode = st.segmented_control("見方", ["品種別", "形態別", "保管場所別"], default="品種別",
                                key="stk_comp", label_visibility="collapsed") or "品種別"
    if mode == "品種別":
        data = defaultdict(float)
        for r in m["stock_rows"]:
            data[r["variety"]] += r["kg"]
    elif mode == "形態別":
        data = {f: v for f, v in m["by_form"].items()}
    else:
        data = {k: v["total"] for k, v in m["by_loc"].items()}
    data = {k: v for k, v in data.items() if v > 0.05}
    total = sum(data.values())
    if total <= 0:
        st.caption("在庫が登録されると、ここに内訳が表示されます。")
        return
    df = pd.DataFrame([{"名称": k, "kg": v} for k, v in data.items()])
    names = list(data)
    colors = {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(names)}
    arc = alt.Chart(df).mark_arc(innerRadius=58, outerRadius=92).encode(
        theta="kg:Q",
        color=alt.Color("名称:N", scale=alt.Scale(domain=names, range=[colors[n] for n in names]), legend=None),
        tooltip=[alt.Tooltip("名称:N"), alt.Tooltip("kg:Q", format=",.1f", title="kg")])
    center = alt.Chart(pd.DataFrame({"t": [f"{_n(total)} kg"]})).mark_text(
        size=17, fontWeight="bold", color="#F8F3E6").encode(text="t:N")
    st.altair_chart(_chart_cfg((arc + center).properties(height=210)), use_container_width=True)
    st.markdown("".join(
        f'<div class="stk-legend"><span><i style="background:{colors[n]}"></i>{n}</span>'
        f'<span>{_n(v)} kg（{v / total * 100:.0f}%）</span></div>' for n, v in data.items()),
        unsafe_allow_html=True)


def _customer_table(m: dict) -> None:
    months, cur = m["months"], m["cur_ym"]
    rows = []
    for r in m["table"]:
        row = {"取引先": r["取引先"]}
        for x in months:
            row[_mlabel(x, cur)] = r.get(x)
        row["合計"] = r["合計"]
        rows.append(row)
    df = pd.DataFrame(rows).astype({c: "float64" for c in rows[0] if c != "取引先"})
    st.dataframe(df.style.format("{:,.1f}", subset=[c for c in df.columns if c != "取引先"], na_rep="－"),
                 use_container_width=True, hide_index=True)
    if m.get("others"):
        with st.expander(f"「その他の一般のお客様（{len(m['others'])}名）」の内訳を見る"):
            st.caption("領収書・請求書を出す卸先は、すべて個別に表示しています。"
                       "ここは一般のお客様（消費者向け）のうち、出庫量が少ない方々で、表では1行にまとめています。")
            st.dataframe(pd.DataFrame(m["others"]), use_container_width=True, hide_index=True,
                         column_config={"合計(kg)": st.column_config.NumberColumn(format="%.1f")})
    st.download_button("↓ CSVで保存", df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f'取引先別月次出庫_{m["label"].replace("/", "").replace("〜", "-")}.csv', mime="text/csv", key="stk_csv")


def _sankey_svg(m: dict) -> str:
    flow = {k: v for k, v in m["flow"].items() if v > 0}
    if not flow:
        return ""
    by_c = defaultdict(float)
    by_l = defaultdict(float)
    for (loc, ck), v in flow.items():
        by_c[ck] += v
        by_l[loc] += v
    # 卸先（b:）は個別に出す（多いときは出庫量の多い8か所）。一般のお客様は上位3名だけ個別にして、残りは1つにまとめる
    ordered = [ck for ck, _ in sorted(by_c.items(), key=lambda kv: -kv[1])]
    top = [ck for ck in ordered if ck.startswith("b:")][:8] + [ck for ck in ordered if not ck.startswith("b:")][:3]
    top = sorted(top, key=lambda ck: -by_c[ck])
    right = top + (["__other__"] if len(by_c) > len(top) else [])
    rname = {ck: m["cust_name"].get(ck, "") for ck in top}
    others_all_consumer = all(not ck.startswith("b:") for ck in by_c if ck not in top)
    rname["__other__"] = (f"その他の一般のお客様（{len(by_c) - len(top)}名）" if others_all_consumer
                          else f"その他（{len(by_c) - len(top)}名）")
    rval = {ck: by_c[ck] for ck in top}
    if "__other__" in right:
        rval["__other__"] = sum(v for ck, v in by_c.items() if ck not in top)
    lefts = [l for l, _ in sorted(by_l.items(), key=lambda kv: -kv[1])]
    total = sum(by_l.values())

    W, NW, GAP, MIN_H = 780, 14, 8, 30
    lx, rx = 130, W - 240
    n_max = max(len(lefts), len(right))
    sc = (300 - GAP * (n_max - 1) - 10) / total
    # ノードの高さ＝出庫量に比例（ただし右は文字が重ならない最小の高さを確保）
    lh = {l: by_l[l] * sc for l in lefts}
    rh = {r: max(rval[r] * sc, MIN_H) for r in right}
    l_total = sum(lh.values()) + GAP * (len(lefts) - 1)
    r_total = sum(rh.values()) + GAP * (len(right) - 1)
    H = max(l_total, r_total) + 10          # 実際に積んだ高さに合わせる（下が見切れないように）
    lcol = {l: PALETTE[i % len(PALETTE)] for i, l in enumerate(lefts)}
    parts = [f'<svg viewBox="0 0 {W} {H:.0f}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">']
    ly, ry = {}, {}
    y = 5.0 + (H - 10 - l_total) / 2        # 左は縦中央に寄せる
    for l in lefts:
        ly[l] = y
        y += lh[l] + GAP
    y = 5.0
    for r in right:
        ry[r] = y
        y += rh[r] + GAP
    lcur, rcur = dict(ly), dict(ry)
    for l in lefts:
        for r in right:
            v = sum(v for (loc, ck), v in flow.items()
                    if loc == l and ((ck == r) if r != "__other__" else (ck not in top)))
            if v <= 0:
                continue
            h = v * sc
            y0, y1 = lcur[l], rcur[r]
            lcur[l] += h
            rcur[r] += h
            x0, x1 = lx + NW, rx
            c = (x0 + x1) / 2
            parts.append(
                f'<path d="M{x0},{y0:.1f} C{c},{y0:.1f} {c},{y1:.1f} {x1},{y1:.1f} '
                f'L{x1},{y1 + h:.1f} C{c},{y1 + h:.1f} {c},{y0 + h:.1f} {x0},{y0 + h:.1f} Z" '
                f'fill="{lcol[l]}" fill-opacity="0.28"/>')
    for l in lefts:
        h = lh[l]
        parts.append(f'<rect x="{lx}" y="{ly[l]:.1f}" width="{NW}" height="{h:.1f}" rx="3" fill="{lcol[l]}"/>')
        parts.append(f'<text x="{lx - 8}" y="{ly[l] + h / 2:.1f}" text-anchor="end" fill="#F2EDE0" font-size="13" '
                     f'dominant-baseline="middle">{l}</text>')
        parts.append(f'<text x="{lx - 8}" y="{ly[l] + h / 2 + 15:.1f}" text-anchor="end" fill="#B9B5C8" font-size="11" '
                     f'dominant-baseline="middle">{_n(by_l[l])}kg</text>')
    for r in right:
        h = rh[r]
        parts.append(f'<rect x="{rx}" y="{ry[r]:.1f}" width="{NW}" height="{h:.1f}" rx="3" fill="{GOLD}"/>')
        nm = rname[r][:16]
        parts.append(f'<text x="{rx + NW + 8}" y="{ry[r] + h / 2 - 7:.1f}" fill="#F2EDE0" font-size="13" '
                     f'dominant-baseline="middle">{nm}</text>')
        parts.append(f'<text x="{rx + NW + 8}" y="{ry[r] + h / 2 + 8:.1f}" fill="#B9B5C8" font-size="11" '
                     f'dominant-baseline="middle">{_n(rval[r])}kg</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 在庫の登録・修正
# ---------------------------------------------------------------------------
def _editor(m: dict, cfg: dict) -> None:
    ver = st.session_state.setdefault("stk_ver", 0)
    locs = stock.location_names(cfg)
    varieties = sorted({*cfg["varieties"], cfg["default_variety"]})

    _month_end_editor(cfg, locs, varieties)
    if st.session_state.get("stk_flash"):
        st.success(st.session_state.pop("stk_flash"))

    st.divider()
    ui.section("入庫・移動・その他の増減を記録する", "収穫・仕入・とう精・保管場所の移動・ロス／サンプルなど")
    _entry_form(cfg, locs, varieties)

    st.divider()
    ui.section("記録の修正・削除", "入力した棚卸・入庫の記録です。行を選んで削除、数字は直接書き換えられます。")
    _history_editor(cfg, locs, varieties)


def _month_end_editor(cfg: dict, locs: list[str], varieties: list[str]) -> None:
    """月末（または今日）の在庫を、帳簿の数字を見ながら直接書き換えて確定する。

    帳簿上の在庫が最初から「実数」の欄に入っているので、違うところだけ直せばよい。
    直した行だけを、その日付の棚卸として登録する（以後の入出庫はこの実数から計算し直す）。
    """
    ver = st.session_state.setdefault("stk_ver", 0)
    today = stock.today()
    opts: list[tuple[str, date]] = [(f"今日（{today.month}/{today.day}）", today)]
    y, mo = today.year, today.month
    for _ in range(13):
        end = stock._month_end(f"{y}-{mo:02d}")
        if end < today:
            opts.append((f"{y}年{mo}月末（{end.month}/{end.day}）", end))
        mo -= 1
        if mo == 0:
            y, mo = y - 1, 12
    ui.section("月末の在庫を直す", "帳簿の数字が入っています。実際に数えた数字と違う行だけ書き換えて、下のボタンで確定してください。")
    labels = [o[0] for o in opts]
    default = 1 if today.day <= 7 and len(opts) > 1 else 0   # 月初は前月末を直すことが多い
    pick = st.selectbox("いつの在庫", labels, index=default, key=f"stk_me_pick_{ver}")
    as_of = dict(opts)[pick]

    book = stock.compute_stock(db.list_ledger_entries(), stock.load_out_rows(), cfg, as_of)
    rows = [{"保管場所": k[2], "品種": k[1], "形態": k[0], "帳簿の在庫kg": round(v, 1), "実数kg": round(v, 1)}
            for k, v in sorted(book.items(), key=lambda kv: (kv[0][2], kv[0][1], kv[0][0]))]
    if not rows:
        rows = [{"保管場所": (cfg["default_location"].get(f) or (locs[0] if locs else "")),
                 "品種": cfg["default_variety"], "形態": f, "帳簿の在庫kg": 0.0, "実数kg": 0.0}
                for f in ("精米", "玄米")]
    df = pd.DataFrame(rows)
    loc_opts = locs + ([stock.UNSET_LOC] if any(r["保管場所"] == stock.UNSET_LOC for r in rows) else [])
    ed = st.data_editor(
        df, num_rows="dynamic", hide_index=True, use_container_width=True,
        key=f"stk_me_{ver}_{as_of.isoformat()}",
        column_config={
            "保管場所": st.column_config.SelectboxColumn("保管場所", options=loc_opts or [""], required=True),
            "品種": st.column_config.SelectboxColumn("品種", options=varieties, required=True),
            "形態": st.column_config.SelectboxColumn("形態", options=["玄米", "精米"], required=True),
            "帳簿の在庫kg": st.column_config.NumberColumn("帳簿の在庫(kg)", disabled=True, format="%.1f"),
            "実数kg": st.column_config.NumberColumn("実数(kg)　← ここを直す", min_value=0.0, step=1.0, format="%.1f"),
        })
    st.caption("表の下の ＋ で行を足すと、帳簿にない保管場所・品種も登録できます。")
    if st.button(f"この内容で{pick}の在庫を確定", type="primary", key=f"stk_me_save_{ver}"):
        bk = {(r["形態"], r["品種"], r["保管場所"]): r["帳簿の在庫kg"] for r in rows}
        out = []
        for _, r in ed.iterrows():
            if pd.isna(r["実数kg"]) or not r["保管場所"] or not r["品種"] or not r["形態"]:
                continue
            k = (r["形態"], r["品種"], r["保管場所"])
            if k in bk and abs(bk[k] - float(r["実数kg"])) < 0.05:
                continue  # 帳簿どおり（変更なし）
            out.append({"form": r["形態"], "variety": r["品種"], "location": r["保管場所"],
                        "kg": float(r["実数kg"])})
        if not out:
            st.warning("帳簿から変わった行がありません。直す行の「実数」を書き換えてください。")
        else:
            n = stock.save_counts(out, as_of)
            st.session_state["stk_ver"] = ver + 1
            st.session_state["stk_flash"] = f"{pick}の在庫を{n}行、実数で確定しました。"
            st.rerun()


_KIND_LABELS = {
    "produce": "自家生産入庫（収穫）", "buy": "買受（仕入）",
    "adjust": "その他の増減（＋/−）", "mill": "とう精（玄米→精米）",
    "move": "保管場所の移動（外部倉庫→自宅など）",
}


def _entry_form(cfg: dict, locs: list[str], varieties: list[str]) -> None:
    with st.form("stk_entry_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        d = c1.date_input("日付", value=stock.today())
        kind = c2.selectbox("種別", list(_KIND_LABELS), format_func=_KIND_LABELS.get)
        var = c3.selectbox("品種", varieties, index=varieties.index(cfg["default_variety"])
                           if cfg["default_variety"] in varieties else 0)
        c4, c5, c6 = st.columns(3)
        form = c4.selectbox("形態", ["玄米", "精米"], help="とう精のときは「投入する玄米」の形態は玄米固定です。")
        dl = cfg["default_location"].get("玄米")
        loc = c5.selectbox("保管場所（移動のときは移動元）", locs or [""], index=(locs.index(dl) if dl in locs else 0),
                           help="とう精のときは玄米を取り出す場所。")
        to_loc = c6.selectbox("移動先（「保管場所の移動」のときだけ使います）", [""] + locs,
                              index=0, help="例：外部倉庫から自宅の冷蔵庫へ移した分")
        c7, _c8 = st.columns(2)
        qty = c7.number_input("数量（kg）", step=1.0, format="%.1f",
                              help="とう精のときは投入する玄米kg。移動のときは移す量。増減は＋/−で入力できます。")
        out_kg = st.number_input("（とう精のみ）できた精米kg", min_value=0.0, step=1.0, format="%.1f")
        note = st.text_input("メモ（任意）", placeholder="例：令和8年産 収穫分／JAから仕入")
        if st.form_submit_button("記録する", type="primary"):
            if not qty:
                st.error("数量を入れてください。")
            elif kind == "mill" and out_kg <= 0:
                st.error("とう精は、できた精米kgも入れてください。")
            elif kind == "move" and (not to_loc or to_loc == loc or qty < 0):
                st.error("移動は、移動元と違う移動先を選び、数量はプラスで入れてください。")
            else:
                flags = ledger.load_flags()
                db.add_ledger_entry({
                    "entry_date": d.isoformat(), "kind": kind,
                    "rice_type": ledger.product_rice_type(var, flags),
                    "form": "玄米" if kind == "mill" else form,
                    "qty_kg": float(qty), "qty_out_kg": float(out_kg) if kind == "mill" else 0,
                    "counterparty": "", "note": note, "location": loc, "variety": var,
                    "to_location": to_loc if kind == "move" else ""})
                st.success("記録しました。")
                st.rerun()


def _history_editor(cfg: dict, locs: list[str], varieties: list[str]) -> None:
    entries = list(reversed(db.list_ledger_entries()))[:80]
    if not entries:
        st.caption("まだ記録がありません。")
        return
    rows = [{"id": e["id"], "日付": ledger.parse_date(e["entry_date"]) or date.today(),
             "種別": ledger.kind_label(e["kind"]), "形態": e.get("form") or "玄米",
             "品種": e.get("variety") or "", "保管場所": e.get("location") or "", "移動先": e.get("to_location") or "",
             "数量kg": float(e.get("qty_kg") or 0), "精米産出kg": float(e.get("qty_out_kg") or 0),
             "メモ": e.get("note") or ""} for e in entries]
    df = pd.DataFrame(rows)
    ed = st.data_editor(
        df, num_rows="dynamic", hide_index=True, use_container_width=True, key="stk_hist",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "日付": st.column_config.DateColumn("日付", format="YYYY/MM/DD"),
            "種別": st.column_config.TextColumn("種別", disabled=True),
            "形態": st.column_config.SelectboxColumn("形態", options=["玄米", "精米"]),
            "品種": st.column_config.SelectboxColumn("品種", options=[""] + varieties),
            "保管場所": st.column_config.SelectboxColumn("保管場所", options=[""] + locs),
            "移動先": st.column_config.SelectboxColumn("移動先", options=[""] + locs, help="保管場所の移動のみ"),
            "数量kg": st.column_config.NumberColumn("数量(kg)", format="%.1f"),
            "精米産出kg": st.column_config.NumberColumn("精米産出(kg)", format="%.1f", help="とう精のみ"),
        })
    if st.button("変更を保存（削除を含む）", key="stk_hist_save"):
        keep = set()
        n_upd = 0
        orig = {r["id"]: r for r in rows}
        for _, r in ed.iterrows():
            if pd.isna(r["id"]):
                continue  # この画面では新規追加はしない（②のフォームを使う）
            i = int(r["id"])
            keep.add(i)
            new = {
                "entry_date": pd.Timestamp(r["日付"]).date().isoformat(), "form": r["形態"],
                "variety": r["品種"] or "", "location": r["保管場所"] or "",
                "to_location": r["移動先"] or "",
                "qty_kg": float(r["数量kg"] or 0), "qty_out_kg": float(r["精米産出kg"] or 0),
                "note": r["メモ"] or ""}
            o = orig[i]
            if (new["entry_date"] != o["日付"].isoformat() or new["form"] != o["形態"]
                    or new["variety"] != o["品種"] or new["location"] != o["保管場所"]
                    or new["to_location"] != o["移動先"]
                    or abs(new["qty_kg"] - o["数量kg"]) > 1e-9 or abs(new["qty_out_kg"] - o["精米産出kg"]) > 1e-9
                    or new["note"] != o["メモ"]):
                db.update_ledger_entry(i, new)
                n_upd += 1
        n_del = 0
        for i in orig:
            if i not in keep:
                db.delete_ledger_entry(i)
                n_del += 1
        st.session_state["stk_flash"] = f"記録を更新しました（修正{n_upd}件・削除{n_del}件）。"
        st.rerun()


# ---------------------------------------------------------------------------
# 出庫の明細
# ---------------------------------------------------------------------------
def _out_detail(m: dict, cfg: dict) -> None:
    st.caption("在庫から出たものとして数えている一覧です。請求先は**発行済みの領収書・確定済みの請求書**、"
               "消費者向けは**出荷確定した注文**を数えます。請求先でも、書類がまだ無い出荷は注文のまま数え、"
               "書類が発行されたらそちらで数え直します（二重に数えません）。"
               "同じ伝票番号の注文は自動で1件にまとめます。違うと思う行は「除外」にチェックすると、在庫の計算から外せます。")
    for w in (m.get("receipt_waiting") or [])[:6]:
        st.caption(f'🧾 {w["client"]}：請求書・領収書の発行前の出荷 約{w["kg"]:,.0f}kg（注文のまま計上中。書類を発行するとそちらで数えます）')
    months = set(m["months"])
    rows = [r for r in m["all_out"] if f'{r["date"].year}-{r["date"].month:02d}' in months]
    if not rows:
        st.info("この期間の出庫はまだありません。")
        return
    rows = sorted(rows, key=lambda r: (r["date"], r["id"]), reverse=True)
    df = pd.DataFrame([{
        "id": r["id"], "日付": r["date"], "取引先": r["customer"], "元": r["source"],
        "品種": r["variety"], "形態": r["form"], "kg": r["kg"], "保管場所": r["location"],
        "状態": ("書類で計上済み（注文は数えない）" if r.get("skip") else
                 "重複（自動除外）" if r["dup"] else "計上" if not r["excluded"] else "手動で除外"),
        "メモ": r["note"], "除外": bool(r["excluded"])} for r in rows])
    flt = st.segmented_control("表示", ["すべて", "計上のみ", "除外・重複・書類で計上済み"], default="すべて",
                               key="stk_out_flt", label_visibility="collapsed") or "すべて"
    if flt == "計上のみ":
        df = df[df["状態"] == "計上"]
    elif flt == "除外・重複・書類で計上済み":
        df = df[df["状態"] != "計上"]
    ed = st.data_editor(
        df, hide_index=True, use_container_width=True, key=f"stk_out_{flt}",
        disabled=[c for c in df.columns if c != "除外"],
        column_config={"id": None, "日付": st.column_config.DateColumn("日付", format="YYYY/MM/DD"),
                       "kg": st.column_config.NumberColumn("kg", format="%.1f"),
                       "除外": st.column_config.CheckboxColumn("除外", help="チェックすると在庫の計算から外します")})
    if st.button("除外の設定を保存", key="stk_out_save"):
        shown = set(df["id"])
        picked = {r["id"] for _, r in ed.iterrows()
                  if r["除外"] and not r["状態"].startswith(("重複", "請求先"))}
        new_ex = [i for i in (cfg.get("excluded_out") or []) if i not in shown] + sorted(picked)
        cfg["excluded_out"] = new_ex
        stock.save_config(cfg)
        st.success("保存しました。")
        st.rerun()


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
def _settings(cfg: dict) -> None:
    ui.section("保管場所", "冷蔵庫・倉庫など。容量（kg）を入れると、満杯に近いときにお知らせします（0＝容量なし）。")
    ldf = pd.DataFrame([{"名前": l["name"], "容量kg": float(l.get("capacity_kg") or 0)} for l in cfg["locations"]])
    led = st.data_editor(ldf, num_rows="dynamic", hide_index=True, use_container_width=True, key="stk_set_locs",
                         column_config={"容量kg": st.column_config.NumberColumn("容量(kg)", min_value=0.0, step=10.0)})
    names = [n for n in led["名前"].tolist() if isinstance(n, str) and n.strip()]

    ui.section("出庫を引く保管場所", "注文・請求書は保管場所を持たないため、形態ごとにここから出たものとして計算します。")
    d1, d2 = st.columns(2)
    opts = names or [""]
    dloc = {}
    for col, f in zip((d1, d2), ("精米", "玄米")):
        cur = cfg["default_location"].get(f)
        dloc[f] = col.selectbox(f"{f}の出庫元", opts, index=opts.index(cur) if cur in opts else 0, key=f"stk_dloc_{f}")

    ui.section("品種ごとの出庫元（任意）",
               "品種によって保管場所が違うときに指定します（例：コシヒカリは飯島、天のつぶは肥料組合）。"
               "「（既定）」のままなら上の設定を使います。")
    vl = cfg.get("variety_location") or {}
    vopts = ["（既定）"] + names
    vloc_df = pd.DataFrame([{"品種": v, "玄米の出庫元": vl.get(v, {}).get("玄米") or "（既定）",
                             "精米の出庫元": vl.get(v, {}).get("精米") or "（既定）"}
                            for v in (cfg["varieties"] or [cfg["default_variety"]])])
    vloc_ed = st.data_editor(
        vloc_df, hide_index=True, use_container_width=True, key="stk_set_vloc",
        disabled=["品種"],
        column_config={"玄米の出庫元": st.column_config.SelectboxColumn("玄米の出庫元", options=vopts),
                       "精米の出庫元": st.column_config.SelectboxColumn("精米の出庫元", options=vopts)})

    ui.section("品種")
    vtxt = st.text_area("品種の一覧（1行に1つ）", "\n".join(cfg["varieties"]), height=110, key="stk_set_vars",
                        help="商品名・請求書の品目名にこの名前が含まれていれば、その品種として数えます。")
    vlist = [v.strip() for v in vtxt.splitlines() if v.strip()]
    dvar = st.selectbox("どれにも当てはまらないときの品種", vlist or [cfg["default_variety"]],
                        index=(vlist.index(cfg["default_variety"]) if cfg["default_variety"] in vlist else 0),
                        key="stk_set_dvar")

    ui.section("計算・お知らせのしきい値")
    t1, t2, t3 = st.columns(3)
    hi = t1.number_input("欠品リスク「高」（か月未満）", min_value=0.1, value=float(cfg["risk_high_months"]), step=0.5)
    mid = t2.number_input("欠品リスク「中」（か月未満）", min_value=0.2, value=float(cfg["risk_mid_months"]), step=0.5)
    stale = t3.number_input("棚卸が古いとお知らせ（日）", min_value=7, value=int(cfg["stale_count_days"]), step=5)
    inv = st.checkbox("領収書のない請求先は、確定済みの請求書で出庫に数える",
                      value=bool(cfg.get("count_sent_invoices", True)),
                      help="振込払いなどで領収書を出さない請求先（例：グラナダ）の出荷を、確定した請求書の数量で在庫から引きます。"
                           "領収書を発行した請求書は領収書で数えるので二重にはなりません。")
    uninv = st.checkbox("請求書・領収書がまだ無い請求先の出荷も、注文のまま出庫に数える",
                        value=bool(cfg.get("count_uninvoiced_orders", True)),
                        help="請求先（卸先）の出荷は、書類が発行されるまで在庫から引かれず、実際より多く見えてしまうのを防ぎます。"
                             "書類が発行されると（出荷日から31日以内）そちらで数え直すので、二重にはなりません。")
    y1, y2 = st.columns(2)
    myield = y1.number_input("精米の歩留まり（玄米→白米）", min_value=0.5, max_value=1.0,
                             value=float(cfg.get("milling_yield") or 0.9), step=0.01, format="%.2f",
                             help="玄米1kgから白米が何kgできるか。月の使える量を白米換算で出すのに使います。")
    hmonth = y2.number_input("次の新米が入る月", min_value=1, max_value=12,
                             value=int(cfg.get("harvest_month") or 9), step=1,
                             help="この月までの月数で在庫を割って「1か月に使える量」を出します。")
    if st.button("設定を保存", type="primary", key="stk_set_save"):
        if not names:
            st.error("保管場所を1つ以上登録してください。")
        else:
            caps = {r["名前"]: float(r["容量kg"] or 0) for _, r in led.iterrows() if isinstance(r["名前"], str)}
            cfg.update({
                "locations": [{"name": n, "capacity_kg": caps.get(n, 0.0)} for n in names],
                "default_location": dloc, "varieties": vlist, "default_variety": dvar,
                "risk_high_months": hi, "risk_mid_months": max(mid, hi), "stale_count_days": int(stale),
                "milling_yield": float(myield), "harvest_month": int(hmonth),
                "count_sent_invoices": bool(inv), "count_uninvoiced_orders": bool(uninv)})
            new_vl = {}
            for _, r in vloc_ed.iterrows():
                d = {f: r[f + "の出庫元"] for f in ("玄米", "精米") if r[f + "の出庫元"] in names}
                if d:
                    new_vl[r["品種"]] = d
            cfg["variety_location"] = {v: d for v, d in new_vl.items() if v in vlist}
            stock.save_config(cfg)
            st.success("設定を保存しました。")
            st.rerun()
