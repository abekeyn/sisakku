# -*- coding: utf-8 -*-
"""給与支払明細書（給与明細）— 従業員マスタ・発行履歴・PDF生成。

請求書／領収書と同じく reportlab でアプリ内即時生成（A5・1枚）。
従業員マスタと発行済み明細は、請求まわり（lib/billing.py）と同じ方式で
settings テーブルにJSONとして持つ（スキーマ変更が不要で、ローカルSQLiteでも
クラウドPostgreSQLでもそのまま動く）。

給与明細の交付は所得税法231条で事業主に義務づけられている（支払の都度）。
控除額（源泉所得税・社会保険料等）は手入力方式：金額の根拠は事業主側の
計算に委ねる。個人経営の農業は社会保険の強制適用事業所ではないため、
既定の控除項目は源泉所得税を先頭に置いている。
"""
from __future__ import annotations

import base64
import io
from datetime import date, datetime
from pathlib import Path

from reportlab.lib.pagesizes import A5
from reportlab.pdfbase import pdfmetrics

from . import db
from . import pdf_common as pc

EMPLOYEES_KEY = "payroll_employees"   # 従業員マスタ
SLIPS_KEY = "payroll_slips"           # 発行済み明細 {f"{emp_id}:{ym}": slip}
FOLDER_KEY = "payroll_folder"         # ローカル保存先（発行書類/給与明細/）
EMPLOYER_KEY = "payroll_employer"     # 支払者（事業主）情報

DEFAULT_FOLDER = r"C:\Users\wolhp\OneDrive\デスクトップ\発行書類\z_給与明細"
# 事業主（給与の支払者）は阿部　喜之。請求書の担当者（阿部　喜臣）とは別人なので、
# pdf_common の ISSUER_CONTACT は流用しない。
DEFAULT_EMPLOYER = {"name": pc.ISSUER_NAME, "rep": "阿部　喜之",
                    "address": pc.ISSUER_ZIP_ADDRESS, "tel": pc.ISSUER_TEL}

# 明細の既定項目（金額0の行はPDFに印字しない）
DEFAULT_EARNINGS = [
    {"name": "基本給", "amount": 0, "taxfree": False},
    {"name": "諸手当", "amount": 0, "taxfree": False},
    {"name": "通勤手当（非課税）", "amount": 0, "taxfree": True},
]
DEFAULT_DEDUCTIONS = [
    {"name": "源泉所得税", "amount": 0},
    {"name": "社会保険料", "amount": 0},
    {"name": "その他控除", "amount": 0},
]

# 源泉徴収税額表（月額表・甲欄）で税額が0円になる下限。扶養控除等申告書の
# 提出が前提。UIの目安表示に使うだけで、税額そのものは手入力。
KOU_ZERO_LIMIT = 88_000


# ===== マスタ・履歴 ==============================================================
def get_employer() -> dict:
    return {**DEFAULT_EMPLOYER, **(db.get_setting(EMPLOYER_KEY) or {})}


def save_employer(emp: dict) -> None:
    db.set_setting(EMPLOYER_KEY, emp)


def get_employees() -> list[dict]:
    return db.get_setting(EMPLOYEES_KEY) or []


def get_employee(emp_id: str) -> dict | None:
    return next((e for e in get_employees() if e["id"] == emp_id), None)


def upsert_employee(emp: dict) -> None:
    es = get_employees()
    for i, e in enumerate(es):
        if e["id"] == emp["id"]:
            es[i] = emp
            break
    else:
        es.append(emp)
    db.set_setting(EMPLOYEES_KEY, es)


def delete_employee(emp_id: str) -> None:
    db.set_setting(EMPLOYEES_KEY, [e for e in get_employees() if e["id"] != emp_id])


def get_slips() -> dict:
    return db.get_setting(SLIPS_KEY) or {}


def save_slip(emp_id: str, emp_name: str, ym: str, pay_date: date, sums: dict,
              earnings: list[dict], deductions: list[dict],
              pdf_bytes: bytes, filename: str) -> str:
    """発行した明細を保存する。同じ従業員・同じ対象月は上書き（＝再発行）。"""
    slips = get_slips()
    key = f"{emp_id}:{ym}"
    slips[key] = {
        "employee_id": emp_id, "employee_name": emp_name, "ym": ym,
        "pay_date": pay_date.isoformat(),
        "gross": sums["gross"], "deduction": sums["deduction"], "net": sums["net"],
        "earnings": earnings, "deductions": deductions,
        "pdf_b64": base64.b64encode(pdf_bytes).decode("ascii"), "filename": filename,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "synced_to_folder": False,
    }
    db.set_setting(SLIPS_KEY, slips)
    return key


def delete_slip(key: str) -> None:
    slips = get_slips()
    if slips.pop(key, None) is not None:
        db.set_setting(SLIPS_KEY, slips)


def latest_slip(emp_id: str) -> dict | None:
    """その従業員の直近（対象月が最新）の明細。翌月の初期値に使う。"""
    mine = [s for s in get_slips().values() if s["employee_id"] == emp_id]
    return max(mine, key=lambda s: s["ym"]) if mine else None


def prefill(emp: dict) -> tuple[list[dict], list[dict]]:
    """明細入力欄の初期値（支給・控除）。直近の明細 → 従業員マスタ → 既定 の順。

    毎月ほぼ同額のため、前回の内容をそのまま出したほうが入力が早い。
    """
    last = latest_slip(emp["id"])
    if last:
        return ([dict(x) for x in last["earnings"]], [dict(x) for x in last["deductions"]])
    return ([dict(x) for x in (emp.get("earnings") or DEFAULT_EARNINGS)],
            [dict(x) for x in (emp.get("deductions") or DEFAULT_DEDUCTIONS)])


def year_total(emp_id: str, year: int) -> dict:
    """その年（支給日ベース）の累計。年末調整・源泉徴収票の下敷きに使う。"""
    g = d = n = 0
    for s in get_slips().values():
        if s["employee_id"] == emp_id and s["pay_date"][:4] == str(year):
            g += s["gross"]
            d += s["deduction"]
            n += s["net"]
    return {"gross": g, "deduction": d, "net": n}


# ===== PC側：ローカルフォルダへ保存 ==============================================
def payslip_folder() -> Path | None:
    """保存先フォルダ。親（発行書類）があれば給与明細フォルダを自動作成する。"""
    p = Path(db.get_setting(FOLDER_KEY) or DEFAULT_FOLDER)
    if p.exists():
        return p
    if p.parent.exists():
        p.mkdir(parents=True, exist_ok=True)
        return p
    return None


def sync_payslips() -> list[dict]:
    """【PC側】未保存の給与明細PDFを「発行書類/給与明細/」へ書き出す。

    領収書（billing.sync_receipts）と同じく synced_to_folder フラグで冪等。
    クラウド運用時はPC常駐エージェントが定期的に呼ぶ。
    """
    out = []
    folder = payslip_folder()
    if not folder:
        return out
    slips = get_slips()
    changed = False
    for s in slips.values():
        if s.get("synced_to_folder"):
            continue
        path = folder / s["filename"]
        path.write_bytes(base64.b64decode(s["pdf_b64"]))
        s["synced_to_folder"] = True
        s["synced_at"] = datetime.now().isoformat(timespec="seconds")
        changed = True
        out.append({"employee": s["employee_name"], "path": str(path)})
    if changed:
        db.set_setting(SLIPS_KEY, slips)
    return out


def payslip_filename(pay_date: date, employee_name: str) -> str:
    d = pay_date.strftime("%Y%m%d")
    return f"{d}_{employee_name}_給与明細.pdf" if employee_name else f"{d}_給与明細.pdf"


# ===== 集計 =====================================================================
def totals(earnings: list[dict], deductions: list[dict]) -> dict:
    """支給合計・課税対象支給額・控除合計・差引支給額を求める。"""
    gross = sum(int(e["amount"]) for e in earnings)
    taxable = sum(int(e["amount"]) for e in earnings if not e.get("taxfree"))
    ded = sum(int(d["amount"]) for d in deductions)
    return {"gross": gross, "taxable": taxable, "deduction": ded, "net": gross - ded}


# ===== PDF ======================================================================
def build_payslip_pdf(employee_name: str, pay_date: date,
                      period_from: date, period_to: date,
                      earnings: list[dict], deductions: list[dict],
                      work_days: float | None = None, work_hours: float | None = None,
                      note: str = "", employer: dict | None = None) -> bytes:
    """給与支払明細書PDF(bytes)を作る（A5・支給欄と控除欄を左右に並べる様式）。

    金額0の行は印字しない（使っていない項目名だけが並んで、実際にいくら
    支給・控除されたのかが読み取りにくくなるのを防ぐ）。
    """
    from reportlab.pdfgen import canvas as _canvas

    pc.ensure_font()
    employer = employer or get_employer()
    earn = [e for e in earnings if int(e["amount"]) != 0]
    ded = [d for d in deductions if int(d["amount"]) != 0]
    t = totals(earn, ded)

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    text = pc.make_text_fn(c)
    m = 30

    text(w / 2, h - 42, "給 与 支 払 明 細 書", size=17, align="center")

    # 右上：支給日・計算期間
    text(w - m, h - 68, f"支給日：{pay_date.strftime('%Y年%m月%d日')}", size=8.5, align="right")
    period = f"{period_from.strftime('%Y/%m/%d')}〜{period_to.strftime('%Y/%m/%d')}"
    text(w - m, h - 81, f"計算期間：{period}", size=8.5, align="right")

    # 左上：氏名
    nm = f"{employee_name}　殿"
    text(m, h - 70, nm, size=12)
    c.line(m, h - 76, m + max(150, pdfmetrics.stringWidth(nm, pc.FONT_NAME, 12) + 10), h - 76)

    ty = h - 106
    if work_days is not None or work_hours is not None:
        parts = []
        if work_days is not None:
            parts.append(f"就業日数　{work_days:g}日")
        if work_hours is not None:
            parts.append(f"労働時間　{work_hours:g}時間")
        text(m, ty, "【勤怠】　" + "　／　".join(parts), size=8.5)
        ty -= 22

    # 支給欄・控除欄を左右に並べる
    gap = 12
    col_w = (w - 2 * m - gap) / 2
    rows = max(len(earn), len(ded), 3)
    row_h, head_h = 15, 17
    box_h = head_h + rows * row_h + row_h  # 見出し＋明細行＋合計行

    def draw_column(x, title, items, total_label, total_val):
        c.rect(x, ty - box_h, col_w, box_h, stroke=1, fill=0)
        c.setFillGray(0.92)
        c.rect(x, ty - head_h, col_w, head_h, stroke=1, fill=1)
        c.setFillGray(0)
        text(x + col_w / 2, ty - head_h + 5, title, size=9.5, align="center")
        y = ty - head_h
        for it in items:
            y -= row_h
            text(x + 7, y + 4, it["name"], size=8.5)
            text(x + col_w - 7, y + 4, f"{int(it['amount']):,}", size=9, align="right")
        y = ty - head_h - rows * row_h
        c.line(x, y, x + col_w, y)
        text(x + 7, y - row_h + 4.5, total_label, size=9)
        text(x + col_w - 7, y - row_h + 4.5, f"{total_val:,}", size=10, align="right")

    draw_column(m, "支　給", earn, "支給合計", t["gross"])
    draw_column(m + col_w + gap, "控　除", ded, "控除合計", t["deduction"])

    # 差引支給額
    by = ty - box_h - 16
    c.rect(m, by - 34, w - 2 * m, 34, stroke=1, fill=0)
    text(m + 12, by - 22, "差引支給額", size=11)
    text(w - m - 12, by - 23, f"¥ {t['net']:,} －", size=16, align="right")

    ny = by - 52
    if t["gross"] != t["taxable"]:
        text(m, ny, f"※課税対象支給額　¥{t['taxable']:,}"
             f"（非課税分 ¥{t['gross'] - t['taxable']:,} を除く）", size=7.5)
        ny -= 12
    if note:
        text(m, ny, f"※{note}", size=7.5)
        ny -= 12
    text(m, ny, "※本明細は所得税法第231条に基づき交付するものです。", size=7)

    # 支払者（事業主）。備考のすぐ下に寄せて、明細全体を上半分に収める
    # （A5用紙の下半分が空白のまま間延びして見えるのを防ぐ）。
    sy = ny - 36
    text(w - m, sy, "支払者", size=8, align="right")
    text(w - m, sy - 17, employer.get("name", ""), size=12, align="right")
    sy -= 17
    if employer.get("rep"):
        sy -= 15
        text(w - m, sy, f"代表　{employer['rep']}", size=9, align="right")
    text(w - m, sy - 14, employer.get("address", ""), size=8, align="right")
    text(w - m, sy - 26, employer.get("tel", ""), size=8, align="right")

    c.showPage()
    c.save()
    return buf.getvalue()
