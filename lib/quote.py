# -*- coding: utf-8 -*-
"""見積書 — 明細の作成・PDF生成・メール送信・発行履歴。

請求書／領収書／給与明細と同じく reportlab でアプリ内即時生成（A4・1枚）。

見積書は請求書と違い「まだ請求先マスタに無い相手」へ出すことが多いので、
請求先マスタから選んでも、宛名とメールアドレスを直接入力しても作れる。

見積書は適格請求書（インボイス）ではないので税率ごとの記載義務は無いが、
お米(8%)と送料など(10%)が混ざる見積を出すことがあるため、明細行ごとに
税率を持たせ、税率別の小計を印字する。

保存・履歴は請求まわり（lib/billing.py）と同じく settings テーブルへJSONで
持つ（スキーマ変更が不要で、ローカルSQLiteでもクラウドPostgreSQLでも動く）。
"""
from __future__ import annotations

import base64
import io
from datetime import date, datetime, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics

from . import billing, config, db
from . import pdf_common as pc

QUOTES_KEY = "quotes"             # {quote_id: {...}}
DOC_NO_KEY = "quote_last_doc_no"  # 最後に発番した見積番号
MAIL_KEY = "quote_mail_tmpl"      # メール文面の既定（件名・本文）
FOLDER_KEY = "quote_folder"       # 保存先（発行書類/m_見積書/）

DOC_NO_BASE = 260000              # 発番の起点（26年・0001から）
DEFAULT_FOLDER = str(config.DOCS_ROOT / "m_見積書")
DEFAULT_VALID_DAYS = 30           # 見積の有効期限（発行日から）
TAX_RATES = (8, 10)               # 軽減税率(お米) / 標準税率(送料など)

DEFAULT_ITEM = {"name": "令和7年度　福島県産 コシヒカリ (精米) 5㎏", "cond": "",
                "qty": 1.0, "unit": "個", "price": 4000, "rate": 8}

# 件名の下に並べる記載項目の初期値（画面で自由に足したり消したりできる）
DEFAULT_FIELDS = [{"label": "納期", "value": "ご注文後3営業日以内"},
                  {"label": "お支払条件", "value": "月末締め　翌月末日お振込み"}]

DEFAULT_SUBJECT_TMPL = "阿部農園　お見積書のご送付（{title}）"
DEFAULT_BODY_TMPL = (
    "{to_name}\nご担当者様\n\n"
    "いつもお世話になっております。\n阿部農園の阿部と申します。\n\n"
    "ご依頼いただきましたお見積書を添付にてお送りいたします。\n\n"
    "　件名：{title}\n"
    "　お見積金額：{price}\n"
    "　有効期限：{valid_until}\n\n"
    "内容をご確認いただき、ご不明な点等ございましたらご返信ください。\n"
    "ご検討のほど、よろしくお願いいたします。\n\n"
    "―――――――――――――――――\n"
    "阿部農園　阿部 喜臣\n"
    "〒963-0211　福島県郡山市片平町字西大町一\n"
    "TEL：080-6030-3705\n")


# ===== メール文面テンプレート =====================================================
def get_mail_tmpl() -> dict:
    t = db.get_setting(MAIL_KEY) or {}
    return {"subject": t.get("subject") or DEFAULT_SUBJECT_TMPL,
            "body": t.get("body") or DEFAULT_BODY_TMPL}


def save_mail_tmpl(subject: str, body: str) -> None:
    db.set_setting(MAIL_KEY, {"subject": subject, "body": body})


def render_mail(quote: dict) -> tuple[str, str]:
    """テンプレートに見積の内容を差し込んで（件名, 本文）を返す。

    書きかけの文面に未知の差し込み記号が混ざっていても操作が止まらないよう、
    差し込みに失敗したときは元の文字列をそのまま返す。
    """
    t = get_mail_tmpl()
    vals = {
        "to_name": f"{quote['to_name']}　御中",
        "title": quote.get("title") or "お米代",
        "amount": quote["amount"],
        # {price} は「¥84,406（税込）」や「¥14,000〜¥15,000（税込・30kgあたり）」。
        # 数量が決まっていない（単価だけの）見積書でも文面が成り立つようにする。
        "price": quote.get("price_text") or f"¥{quote['amount']:,}（税込）",
        "valid_until": (_jp_date(date.fromisoformat(quote["valid_until"]))
                        if quote.get("valid_until") else "—"),
        "issue_date": _jp_date(date.fromisoformat(quote["issue_date"])),
        "doc_no": quote["doc_no"],
    }

    def _fmt(s: str) -> str:
        try:
            return s.format(**vals)
        except (KeyError, IndexError, ValueError):
            return s

    return _fmt(t["subject"]), _fmt(t["body"])


# ===== 発番 =====================================================================
def next_doc_no() -> int:
    return int(db.get_setting(DOC_NO_KEY) or DOC_NO_BASE) + 1


def commit_doc_no(no: int) -> None:
    if int(no) > int(db.get_setting(DOC_NO_KEY) or 0):
        db.set_setting(DOC_NO_KEY, int(no))


# ===== 集計 =====================================================================
def clean_items(items: list[dict]) -> list[dict]:
    """明細を正規化する（品名が空の行だけ捨てる）。

    数量が空・0の行は「単価だけのご提示」として残す。合計には入らないが
    見積書には印字する（例：一部切替なら30kgあたり15,000円、全量なら14,000円、
    のように条件別の単価を並べて示したいことがあるため）。
    """
    out = []
    for it in items:
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        try:
            qty = float(it.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        rate = int(it.get("rate") or 8)
        out.append({"name": name, "cond": str(it.get("cond") or "").strip(),
                    "qty": max(qty, 0.0), "unit": str(it.get("unit") or "").strip(),
                    "price": float(it.get("price") or 0),
                    "rate": rate if rate in TAX_RATES else 8})
    return out


def line_amounts(item: dict, tax_included: bool) -> tuple[int, int, int, int]:
    """1行の（税抜単価, 税込単価, 税抜金額, 税込金額）。

    丸め方は請求書（lib/invoice_pdf.py）と揃える：税込単価×数量を税込金額とし、
    税抜金額は「税抜へ直した単価×数量」。同じ内容なら見積と請求で1円もずれない。
    数量が無い（単価のご提示だけの）行は金額を0で返す。
    """
    rate = item["rate"] / 100
    price = item["price"]
    if tax_included:
        unit_incl, unit_excl = round(price), round(price / (1 + rate))
    else:
        unit_excl, unit_incl = round(price), round(price * (1 + rate))
    qty = item.get("qty") or 0
    if qty <= 0:
        return unit_excl, unit_incl, 0, 0
    if tax_included:
        return unit_excl, unit_incl, round(unit_excl * qty), round(price * qty)
    line_excl = round(price * qty)
    return unit_excl, unit_incl, line_excl, round(line_excl * (1 + rate))


def totals(items: list[dict], tax_included: bool) -> dict:
    """税抜合計・税率別の（税抜小計・消費税）・税込合計をまとめて返す。

    数量のある行だけを合計する。has_total が False のときは「単価のご提示だけの
    見積書」なので、合計欄は印字しない。
    """
    excl_by: dict[int, int] = {}
    incl_by: dict[int, int] = {}
    for it in clean_items(items):
        if (it.get("qty") or 0) <= 0:
            continue
        _, _, line_excl, line_incl = line_amounts(it, tax_included)
        excl_by[it["rate"]] = excl_by.get(it["rate"], 0) + line_excl
        incl_by[it["rate"]] = incl_by.get(it["rate"], 0) + line_incl
    excl, incl = sum(excl_by.values()), sum(incl_by.values())
    return {"excl": excl, "tax": incl - excl, "total": incl,
            "by_rate": excl_by, "taxes": {r: incl_by[r] - v for r, v in excl_by.items()},
            "has_total": incl > 0}


def price_range(items: list[dict], tax_included: bool) -> str:
    """単価のご提示だけのときに、見積金額欄へ出す文言（例：¥14,000〜¥15,000（税込・30kgあたり））。"""
    rows = clean_items(items)
    if not rows:
        return "—"
    basis = "税込" if tax_included else "税抜"
    prices = sorted({line_amounts(r, tax_included)[1 if tax_included else 0] for r in rows})
    units = {r["unit"] for r in rows if r["unit"]}
    per = f"・{units.pop()}あたり" if len(units) == 1 else ""
    if len(prices) == 1:
        return f"¥{prices[0]:,}（{basis}{per}）"
    return f"¥{prices[0]:,}〜¥{prices[-1]:,}（{basis}{per}）"


def price_summary(items: list[dict], tax_included: bool, headline: str = "") -> str:
    """見積金額欄・メール本文に出す金額の文言。headlineを入れればそれを優先する。"""
    if headline.strip():
        return headline.strip()
    t = totals(items, tax_included)
    if t["has_total"]:
        return f"¥{t['total']:,}（税込）"
    return price_range(items, tax_included)


# ===== 履歴 =====================================================================
def get_quotes() -> dict:
    return db.get_setting(QUOTES_KEY) or {}


def _save_quotes(q: dict) -> None:
    db.set_setting(QUOTES_KEY, q)


def get_quote(qid: str) -> dict | None:
    return get_quotes().get(qid)


def save_quote(quote: dict, pdf_bytes: bytes, filename: str) -> str:
    """発行した見積書を記録する（発行ボタンを押した時点で呼ぶ）。

    同じ見積番号での作り直しは置き換える（入力を直して発行し直すたびに
    履歴とフォルダのPDFが増えていかないようにするため）。
    """
    quotes = get_quotes()
    for old in [k for k, v in quotes.items() if v.get("doc_no") == quote["doc_no"]]:
        quotes.pop(old)
    qid = f"q{quote['doc_no']}"
    quotes[qid] = {**quote, "id": qid, "status": "draft",
                   "pdf_b64": base64.b64encode(pdf_bytes).decode("ascii"),
                   "filename": filename,
                   "created_at": datetime.now().isoformat(timespec="seconds"),
                   "synced_to_folder": False}
    _save_quotes(quotes)
    commit_doc_no(quote["doc_no"])
    return qid


def delete_quote(qid: str) -> None:
    quotes = get_quotes()
    if quotes.pop(qid, None) is not None:
        _save_quotes(quotes)


# ===== PC側：ローカルフォルダへ保存 ==============================================
def quote_folder(client_id: str = "") -> Path | None:
    """保存先フォルダ。請求先マスタの取引先フォルダがあればそちらへ入れる。

    見積 → 請求 → 領収 が同じ取引先フォルダに揃うようにするため。請求先が
    紐づかない（新規のお客様向けの）見積は「発行書類/m_見積書/」へ入れる。
    """
    if client_id:
        c = billing.get_client(client_id)
        if c:
            f = billing.receipts_folder(c)
            if f:
                return f
    p = Path(db.get_setting(FOLDER_KEY) or DEFAULT_FOLDER)
    if p.exists():
        return p
    if p.parent.exists():
        p.mkdir(parents=True, exist_ok=True)
        return p
    return None


def sync_quotes() -> list[dict]:
    """【PC側】未保存の見積書PDFを発行書類フォルダへ書き出す。

    領収書（billing.sync_receipts）と同じく synced_to_folder フラグで冪等。
    クラウド運用時はPC常駐エージェントが定期的に呼ぶ。
    """
    out = []
    quotes = get_quotes()
    changed = False
    for qid, q in quotes.items():
        if q.get("synced_to_folder"):
            continue
        folder = quote_folder(q.get("client_id", ""))
        if not folder:
            continue
        path = folder / q["filename"]
        path.write_bytes(base64.b64decode(q["pdf_b64"]))
        q["synced_to_folder"] = True
        q["synced_at"] = datetime.now().isoformat(timespec="seconds")
        changed = True
        out.append({"id": qid, "to": q["to_name"], "path": str(path)})
    if changed:
        _save_quotes(quotes)
    return out


def quote_filename(issue_date: date, to_name: str = "") -> str:
    d = issue_date.strftime("%Y%m%d")
    return f"{d}_{to_name}_見積書.pdf" if to_name else f"{d}_見積書.pdf"


# ===== 送信 =====================================================================
def send_quote(qid: str, subject: str, body: str, to_addr: str = "") -> dict:
    """見積書PDFを添付してメール送信する（請求書と同じGmail/SMTP設定を使う）。"""
    from lib.granada_cloud import _ntfy, _smtp_send

    quotes = get_quotes()
    q = quotes.get(qid)
    if not q:
        return {"ok": False, "msg": "対象の見積書が見つかりません"}
    to = (to_addr or q.get("email") or "").strip()
    if not to:
        return {"ok": False, "msg": "送付先メールアドレスが未入力です"}
    ok, msg = _smtp_send(subject, body, base64.b64decode(q["pdf_b64"]),
                         q["filename"], to_addr=to)
    if not ok:
        return {"ok": False, "msg": msg}
    q["status"] = "sent"
    q["email"] = to
    q["sent_at"] = datetime.now().isoformat(timespec="seconds")
    q["sent_subject"] = subject
    quotes[qid] = q
    _save_quotes(quotes)
    _ntfy("Quote: SENT",
          f"✅ {q['to_name']} へ見積書を送信しました。\n"
          f"金額 ¥{q['amount']:,}（税込） / 宛先 {to} / 見積番号 {q['doc_no']}",
          tags="white_check_mark")
    return {"ok": True, "msg": msg}


# ===== PDF ======================================================================
def _jp_date(d: date) -> str:
    return d.strftime("%Y年%m月%d日")


def default_valid_until(issue_date: date) -> date:
    return issue_date + timedelta(days=DEFAULT_VALID_DAYS)


def _fit_size(s: str, max_w: float, size: float, min_size: float = 7.0) -> float:
    """max_wに収まるフォントサイズを返す（収まらなければ少しずつ縮める）。"""
    while size > min_size and pdfmetrics.stringWidth(s, pc.FONT_NAME, size) > max_w:
        size -= 0.5
    return size


def _wrap(s: str, max_w: float, size: float) -> list[str]:
    """max_w幅で折り返した行のリスト（日本語なので1文字ずつ詰めて測る）。"""
    lines: list[str] = []
    for para in (s or "").split("\n"):
        cur = ""
        for ch in para:
            if cur and pdfmetrics.stringWidth(cur + ch, pc.FONT_NAME, size) > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines


def build_quote_pdf(to_name: str, items: list[dict], tax_included: bool,
                    issue_date: date, doc_no, valid_until: date | None = None,
                    title: str = "", fields: list[dict] | None = None,
                    headline: str = "", note: str = "") -> bytes:
    """見積書PDF(bytes)を作る（A4。品目が多ければ続きのページへ送る）。

    tax_included=True なら単価・金額を税込で印字する（お客様に伝えている金額を
    そのまま載せるため。既定）。どちらでも合計欄には税抜・消費税・税込を並べる。
    fields は「納期」「お支払条件」など、件名の下に並べる自由な記載項目
    （[{"label": ..., "value": ...}, ...]）。有効期限はその先頭に自動で入る。
    headline は見積金額欄に出す文言（空なら合計または単価レンジを自動で出す）。
    """
    from reportlab.pdfgen import canvas as _canvas

    pc.ensure_font()
    rows = clean_items(items)
    t = totals(rows, tax_included)
    basis = "税込" if tax_included else "税抜"
    has_cond = any(r["cond"] for r in rows)
    quoted_only = [r for r in rows if (r.get("qty") or 0) <= 0]

    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    text = pc.make_text_fn(c)

    text(w / 2, h - 70, "御 見 積 書", size=24, align="center")

    text(w - 60, h - 120, f"発行日：{_jp_date(issue_date)}", size=10, align="right")
    text(w - 60, h - 136, f"見積番号：{doc_no}", size=10, align="right")

    atesaki = f"{to_name}　御中"
    text(60, h - 150, atesaki, size=15)
    c.line(60, h - 156,
           60 + max(240, pdfmetrics.stringWidth(atesaki, pc.FONT_NAME, 15) + 10), h - 156)

    ly = h - 186
    text(60, ly, f"件名：{title or 'お米代'}", size=10)
    ly -= 15
    text(60, ly, "下記の通りお見積り申し上げます。", size=10)
    lines = [("有効期限", _jp_date(valid_until))] if valid_until else []
    lines += [(str(f.get("label") or "").strip(), str(f.get("value") or "").strip())
              for f in (fields or [])]
    for label, val in lines:
        if not (label and val):
            continue
        ly -= 15
        text(60, ly, f"{label}：{val}", size=10)

    # 見積金額欄（合計が出せないときは単価のご提示として文言を入れる）
    box_y = ly - 56
    c.rect(60, box_y - 10, w - 120, 46, stroke=1, fill=0)
    if t["has_total"] and not headline.strip():
        text(80, box_y + 8, "お見積金額（税込）", size=12)
        text(w - 80, box_y + 8, f"¥ {t['total']:,} －", size=20, align="right")
    else:
        label = "お見積金額（税込）" if t["has_total"] else "お見積単価"
        value = price_summary(rows, tax_included, headline)
        text(80, box_y + 8, label, size=12)
        text(w - 80, box_y + 8, value,
             size=_fit_size(value, w - 260, 20, 10), align="right")

    # 明細の桁位置。使わない列は出さずに幅を回す（適用条件を使うときは内容欄を
    # 狭め、数量が1行も無い＝単価のご提示だけなら数量・金額の列ごと省く）
    has_qty = any((r.get("qty") or 0) > 0 for r in rows)
    if has_qty:
        if has_cond:
            name_w, x_cond, cond_w = 165, 232, 122
            x_qty, x_unit, x_price, x_rate = 360, 366, 452, 484
        else:
            name_w, x_cond, cond_w = 265, 0, 0
            x_qty, x_unit, x_price, x_rate = 350, 356, 445, 480
    else:
        x_qty = 0
        x_unit, x_price, x_rate = 400, 480, 535
        if has_cond:
            name_w, x_cond, cond_w = 205, 280, 110
        else:
            name_w, x_cond, cond_w = 330, 0, 0

    def table_header(y: float) -> None:
        text(60, y, "内容", size=9)
        if has_cond:
            text(x_cond, y, "適用条件", size=9)
        if has_qty:
            text(x_qty, y, "数量", size=9, align="right")
        text(x_unit, y, "単位", size=9)
        text(x_price, y, f"単価({basis})", size=9, align="right")
        text(x_rate, y, "税率", size=9, align="right")
        if has_qty:
            text(w - 60, y, f"金額({basis})", size=9, align="right")
        c.line(60, y - 6, w - 60, y - 6)

    # 明細。1枚に入りきらないときは続きのページへ送る（合計欄と右下の発行者欄に
    # 食い込ませないため。品目が多いほど行間を詰めて、なるべく1枚に収める）
    n = len(rows)
    row_h = 20 if n <= 8 else (16 if n <= 14 else 14)
    fs = 10 if row_h == 20 else (8.5 if row_h == 16 else 8)
    floor = 252 + (30 + 16 * (len(t["by_rate"]) + 1) if t["has_total"] else 0)

    pages, rest, top = [], list(rows), box_y - 52
    while rest:
        with_totals = int((top - floor) // row_h)     # 合計欄まで置ける行数
        if len(rest) <= with_totals:
            pages.append(rest)
            break
        full = int((top - 100) // row_h)              # 続きがある回は下まで使える
        take = min(full, max(1, (len(rest) + 1) // 2)) if len(rest) <= full else full
        pages.append(rest[:take])
        rest = rest[take:]
        top = h - 70
    if not pages:            # 明細0件でも見出しだけは出す
        pages = [[]]

    ty = box_y - 52
    for i, page_rows in enumerate(pages):
        if i:
            c.showPage()
            ty = h - 70
            text(60, ty + 22, f"{to_name}　御中　／　見積番号：{doc_no}（続き）", size=9)
        table_header(ty)
        for it in page_rows:
            ty -= row_h
            unit_excl, unit_incl, line_excl, line_incl = line_amounts(it, tax_included)
            text(60, ty, it["name"], size=_fit_size(it["name"], name_w, fs))
            if has_cond and it["cond"]:
                text(x_cond, ty, it["cond"], size=_fit_size(it["cond"], cond_w, fs))
            text(x_unit, ty, it["unit"], size=fs)
            text(x_price, ty, f"¥{(unit_incl if tax_included else unit_excl):,}",
                 size=fs, align="right")
            text(x_rate, ty, f"{it['rate']}%", size=fs, align="right")
            if (it.get("qty") or 0) > 0:
                text(x_qty, ty, f"{it['qty']:g}", size=fs, align="right")
                text(w - 60, ty, f"¥{(line_incl if tax_included else line_excl):,}",
                     size=fs, align="right")
    c.line(60, ty - 10, w - 60, ty - 10)

    if t["has_total"]:
        ty -= 30
        lx = 460      # 見出しの右端（金額欄と重ならない位置で右揃えにする）
        text(lx, ty, "税抜金額合計", size=9, align="right")
        text(w - 60, ty, f"¥{t['excl']:,}", size=10, align="right")
        for rate in sorted(t["by_rate"]):
            ty -= 16
            label = f"消費税等（{rate}%）"
            if len(t["by_rate"]) > 1:
                label += f"　対象 ¥{t['by_rate'][rate]:,}"
            text(lx, ty, label, size=9, align="right")
            text(w - 60, ty, f"¥{t['taxes'][rate]:,}", size=10, align="right")
        ty -= 16
        text(lx, ty, "税込合計", size=9, align="right")
        text(w - 60, ty, f"¥{t['total']:,}", size=10, align="right")

    ny = ty - 34
    if quoted_only and t["has_total"]:
        # 数量欄が空の行があると、合計との関係が読み取りにくいので必ず断る
        text(60, ny, "※数量欄が空の行は単価のご提示です（上記合計には含みません）。", size=8)
        ny -= 16
    if note:
        text(60, ny, "【備考】", size=9)
        for line in _wrap(note, w - 220, 9):
            ny -= 13
            if ny < 232:      # 右下の発行者欄に食い込ませない
                text(60, ny, "（以下省略）", size=9)
                break
            text(60, ny, line, size=9)

    pc.draw_issuer_block(c, w, 160)

    c.showPage()
    c.save()
    return buf.getvalue()
