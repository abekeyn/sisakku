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
    st.caption("在庫は「棚卸（実数）＋入庫 − 出庫（注文・請求書）」で計算します。"
               "数字が実際と違うときは、棚卸を登録し直すだけで直せます。")

    cfg = stock.load_config()
    fy_now = ledger.fy_of(stock.today())
    fys = [fy_now - i for i in range(4)]

    c_fy, c_flt = st.columns([1, 2])
    fy = c_fy.selectbox("対象年度", fys, format_func=lambda y: f"{y}年度（{y}/4〜{y + 1}/3）", key="stk_fy")
    with c_flt.expander("絞り込み（品種・保管場所・形態）"):
        f2, f3, f4 = st.columns(3)
        varieties = ["すべて"] + sorted({*cfg["varieties"], cfg["default_variety"]})
        variety = f2.selectbox("品種", varieties, key="stk_var")
        locs = ["すべて"] + stock.location_names(cfg) + [stock.UNSET_LOC]
        location = f3.selectbox("保管場所", locs, key="stk_loc")
        form = f4.selectbox("形態", ["すべて", "玄米", "精米"], key="stk_form")

    m = stock.build(fy, variety=variety, location=location, form=form)
    unfiltered = (variety, location, form) == ("すべて", "すべて", "すべて")
    base = m if unfiltered else stock.build(fy)

    t_dash, t_edit, t_out, t_set = st.tabs(
        ["ダッシュボード", "在庫の登録・修正", "出庫の明細", "設定"])
    with t_dash:
        _dashboard(m)
    with t_edit:
        _editor(base, cfg)
    with t_out:
        _out_detail(base, cfg)
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

    # ---- 保管場所別 ＋ 出庫先の流れ ----
    st.write("")
    c1, c2 = st.columns([1, 1.25])
    with c1:
        ui.section("保管場所別の在庫")
        _locations(m)
    with c2:
        ui.section("在庫の流れ（保管場所 → 出庫先）", f'{m["fy"]}年度の出庫量')
        svg = _sankey_svg(m)
        if svg:
            st.markdown(svg, unsafe_allow_html=True)
        else:
            st.caption("この年度の出庫がまだありません。")

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
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={c: st.column_config.NumberColumn(c, format="%.1f")
                                for c in df.columns if c != "取引先"})
    if m.get("others"):
        with st.expander(f"「その他（{len(m['others'])}名）」の内訳を見る"):
            st.caption("上位8名以外のお客様です。1人あたりの出庫量が少ないため、表では1行にまとめています。")
            st.dataframe(pd.DataFrame(m["others"]), use_container_width=True, hide_index=True,
                         column_config={"合計(kg)": st.column_config.NumberColumn(format="%.1f")})
    st.download_button("↓ CSVで保存", df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f'取引先別月次出庫_{m["fy"]}年度.csv', mime="text/csv", key="stk_csv")


def _sankey_svg(m: dict) -> str:
    flow = {k: v for k, v in m["flow"].items() if v > 0}
    if not flow:
        return ""
    by_c = defaultdict(float)
    by_l = defaultdict(float)
    for (loc, ck), v in flow.items():
        by_c[ck] += v
        by_l[loc] += v
    top = [ck for ck, _ in sorted(by_c.items(), key=lambda kv: -kv[1])[:5]]
    right = top + (["__other__"] if len(by_c) > len(top) else [])
    rname = {ck: m["cust_name"].get(ck, "") for ck in top}
    rname["__other__"] = f"その他（{len(by_c) - len(top)}名）"
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

    ui.section("① 現在の在庫を数えて登録する（棚卸）",
               "保管場所・品種・形態ごとの実数（kg）を入れます。登録すると、その日以降はこの実数を基準に計算し直します。")
    on = st.date_input("棚卸日", value=stock.today(), key=f"stk_count_date_{ver}")
    cur = {(r["form"], r["variety"], r["location"]): r["kg"] for r in m["stock_rows"]}
    counted_keys = {stock._entry_key(e, cfg) for e in db.list_ledger_entries() if e.get("kind") == "stock"}
    rows = [{"保管場所": k[2], "品種": k[1], "形態": k[0], "帳簿上の在庫kg": v,
             "実数kg": round(v, 1) if k in counted_keys else None}
            for k, v in sorted(cur.items())]
    if not rows:
        rows = [{"保管場所": (cfg["default_location"].get(f) or (locs[0] if locs else "")),
                 "品種": cfg["default_variety"], "形態": f, "帳簿上の在庫kg": 0.0, "実数kg": None}
                for f in ("精米", "玄米")]
    df = pd.DataFrame(rows)
    loc_opts = locs + ([stock.UNSET_LOC] if any(r["保管場所"] == stock.UNSET_LOC for r in rows) else [])
    ed = st.data_editor(
        df, num_rows="dynamic", hide_index=True, use_container_width=True, key=f"stk_count_{ver}",
        column_config={
            "保管場所": st.column_config.SelectboxColumn("保管場所", options=loc_opts or [""], required=True),
            "品種": st.column_config.SelectboxColumn("品種", options=varieties, required=True),
            "形態": st.column_config.SelectboxColumn("形態", options=["玄米", "精米"], required=True),
            "帳簿上の在庫kg": st.column_config.NumberColumn("帳簿上の在庫(kg)", disabled=True, format="%.1f"),
            "実数kg": st.column_config.NumberColumn("実数(kg)", min_value=0.0, step=1.0, format="%.1f",
                                                    help="数えた量。空欄の行は登録されません。"),
        })
    if st.button("この実数で棚卸を登録", type="primary", key=f"stk_count_save_{ver}"):
        out = []
        for _, r in ed.iterrows():
            if pd.isna(r["実数kg"]) or not r["保管場所"] or not r["品種"] or not r["形態"]:
                continue
            k = (r["形態"], r["品種"], r["保管場所"])
            if k in cur and k in counted_keys and abs(cur[k] - float(r["実数kg"])) < 0.05:
                continue  # 変更なし
            out.append({"form": r["形態"], "variety": r["品種"], "location": r["保管場所"], "kg": float(r["実数kg"])})
        if not out:
            st.warning("登録する行がありません（実数を入れてください）。")
        else:
            n = stock.save_counts(out, on)
            st.session_state["stk_ver"] = ver + 1
            st.session_state["stk_flash"] = f"棚卸を{n}件登録しました。"
            st.rerun()
    if st.session_state.get("stk_flash"):
        st.success(st.session_state.pop("stk_flash"))

    st.divider()
    ui.section("② 入庫・移動・その他の増減を記録する", "収穫・仕入・とう精・保管場所の移動・ロス／サンプルなど")
    _entry_form(cfg, locs, varieties)

    st.divider()
    ui.section("③ 記録の修正・削除", "入力した棚卸・入庫の記録です。行を選んで削除、数字は直接書き換えられます。")
    _history_editor(cfg, locs, varieties)


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
    st.caption("在庫から出たものとして数えている一覧です（注文＋手入力の請求書）。"
               "同じ伝票番号の注文は自動で1件にまとめ、請求書は注文に含まれる分を差し引いています。"
               "違うと思う行は「除外」にチェックすると、在庫の計算から外せます。")
    months = set(m["months"])
    rows = [r for r in m["all_out"] if f'{r["date"].year}-{r["date"].month:02d}' in months]
    if not rows:
        st.info("この年度の出庫はまだありません。")
        return
    rows = sorted(rows, key=lambda r: (r["date"], r["id"]), reverse=True)
    df = pd.DataFrame([{
        "id": r["id"], "日付": r["date"], "取引先": r["customer"], "元": r["source"],
        "品種": r["variety"], "形態": r["form"], "kg": r["kg"], "保管場所": r["location"],
        "状態": ("重複（自動除外）" if r["dup"] else "計上" if not r["excluded"] else "手動で除外"),
        "メモ": r["note"], "除外": bool(r["excluded"])} for r in rows])
    flt = st.segmented_control("表示", ["すべて", "計上のみ", "除外・重複のみ"], default="すべて",
                               key="stk_out_flt", label_visibility="collapsed") or "すべて"
    if flt == "計上のみ":
        df = df[df["状態"] == "計上"]
    elif flt == "除外・重複のみ":
        df = df[df["状態"] != "計上"]
    ed = st.data_editor(
        df, hide_index=True, use_container_width=True, key=f"stk_out_{flt}",
        disabled=[c for c in df.columns if c != "除外"],
        column_config={"id": None, "日付": st.column_config.DateColumn("日付", format="YYYY/MM/DD"),
                       "kg": st.column_config.NumberColumn("kg", format="%.1f"),
                       "除外": st.column_config.CheckboxColumn("除外", help="チェックすると在庫の計算から外します")})
    if st.button("除外の設定を保存", key="stk_out_save"):
        shown = set(df["id"])
        picked = {r["id"] for _, r in ed.iterrows() if r["除外"] and not r["状態"].startswith("重複")}
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
    inc = st.checkbox("未確定（下書き）の手入力請求書も出庫に含める", value=bool(cfg["include_pending_invoices"]),
                      help="請求書を作った時点で納品済みなら、オンのままで構いません。")
    if st.button("設定を保存", type="primary", key="stk_set_save"):
        if not names:
            st.error("保管場所を1つ以上登録してください。")
        else:
            caps = {r["名前"]: float(r["容量kg"] or 0) for _, r in led.iterrows() if isinstance(r["名前"], str)}
            cfg.update({
                "locations": [{"name": n, "capacity_kg": caps.get(n, 0.0)} for n in names],
                "default_location": dloc, "varieties": vlist, "default_variety": dvar,
                "risk_high_months": hi, "risk_mid_months": max(mid, hi), "stale_count_days": int(stale),
                "include_pending_invoices": inc})
            stock.save_config(cfg)
            st.success("設定を保存しました。")
            st.rerun()
