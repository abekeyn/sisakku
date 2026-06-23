# -*- coding: utf-8 -*-
"""データベース層（SQLite / PostgreSQL 両対応）。

- ローカル（PC）：DATABASE_URL未設定なら data/app.db（SQLite）
- クラウド：DATABASE_URL（Supabase等のPostgreSQL）を設定すると自動でそちらを使う

SQLAlchemyで方言差を吸収。アプリ各所からはこのモジュール経由でアクセスする。
返却する行は辞書ライク（row["name"] でアクセス可）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import (Column, Float, Integer, MetaData, String, Table, Text,
                        create_engine, delete, insert, select, text, update)
from sqlalchemy.engine import Engine

from . import config

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "app.db"

_engine: Engine | None = None

metadata = MetaData()

settings = Table(
    "settings", metadata,
    Column("key", String(255), primary_key=True),
    Column("value", Text),
)

customers = Table(
    "customers", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False),
    Column("kana", String(255), default=""),
    Column("tel", String(64), default=""),
    Column("zip", String(32), default=""),
    Column("address", String(512), default=""),
    Column("address2", String(512), default=""),
    Column("company", String(255), default=""),
    Column("honorific", String(16), default="様"),
    Column("note", Text, default=""),
    Column("created_at", String(64)),
)

products = Table(
    "products", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(255), nullable=False, unique=True),
    Column("category", String(32), nullable=False),
    Column("weight_kg", Float, default=0),
    Column("needs_milling", Integer, default=0),
    Column("yamato_name", String(255), nullable=False),
    Column("sort_order", Integer, default=0),
    Column("active", Integer, default=1),
    Column("price", Float, default=0),   # 単価(円)。売上＝単価×個数
)

orders = Table(
    "orders", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("customer_id", Integer, nullable=False),
    Column("product_id", Integer, nullable=False),
    Column("qty", Integer, default=1),
    Column("channel", String(32), default="manual"),
    Column("order_date", String(32)),
    Column("ship_date", String(32), default=""),
    Column("delivery_date", String(32), default=""),
    Column("delivery_time", String(16), default=""),
    Column("milling_kg_override", Float),
    Column("note", Text, default=""),
    Column("status", String(16), default="pending"),
    Column("external_id", String(128), default=""),
    Column("dispatch_ref", Text, default=""),
    Column("tracking_no", String(64), default=""),   # ヤマト伝票番号
    Column("handover", Integer, default=0),           # 1=手渡し（送り状不要）
    Column("created_at", String(64)),
)

export_jobs = Table(
    "export_jobs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("filename", String(255), nullable=False),
    Column("content_b64", Text, nullable=False),
    Column("status", String(16), default="pending"),
    Column("created_at", String(64)),
    Column("written_at", String(64), default=""),
    Column("written_path", String(512), default=""),
)


def _normalize_url(url: str) -> str:
    """Supabase等の postgres:// / postgresql:// を psycopg3用に正規化。"""
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    url = config.database_url()
    if url:
        _engine = create_engine(
            _normalize_url(url),
            pool_pre_ping=True,   # 切れた接続を自動回復（安定性）
            pool_recycle=1800,    # 30分で接続を再生成
            pool_size=5, max_overflow=5,
        )
    else:
        DATA_DIR.mkdir(exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{DB_PATH.as_posix()}",
            connect_args={"check_same_thread": False},
        )
    return _engine


def init_db() -> None:
    engine = get_engine()
    metadata.create_all(engine)
    # マイグレーション：既存テーブルに不足カラムがあれば追加
    from sqlalchemy import inspect as _inspect
    cols = [c["name"] for c in _inspect(engine).get_columns("orders")]
    if "tracking_no" not in cols:
        with engine.begin() as c:
            c.execute(text("ALTER TABLE orders ADD COLUMN tracking_no VARCHAR(64) DEFAULT ''"))
    if "handover" not in cols:
        with engine.begin() as c:
            c.execute(text("ALTER TABLE orders ADD COLUMN handover INTEGER DEFAULT 0"))
    pcols = [c["name"] for c in _inspect(engine).get_columns("products")]
    if "price" not in pcols:
        with engine.begin() as c:
            c.execute(text("ALTER TABLE products ADD COLUMN price FLOAT DEFAULT 0"))


# ---------------------------------------------------------------------------
# 読み取りキャッシュ（速度対策）
# クラウドではアプリ(米国)→DB(ソウル)の往復が遅いため、読み取り結果を
# 短時間キャッシュする。書き込み時は clear_cache() で即座に消す。
# Streamlit実行中のみ有効（常駐エージェント等では素通し）。
# ---------------------------------------------------------------------------
def _cacheable(ttl: int):
    def deco(fn):
        try:
            import streamlit as st
            from streamlit import runtime
            if runtime.exists():
                return st.cache_data(ttl=ttl, show_spinner=False)(fn)
        except Exception:  # noqa: BLE001
            pass
        return fn
    return deco


def clear_cache() -> None:
    """書き込み後に読み取りキャッシュを破棄する（Streamlit実行中のみ）。"""
    try:
        import streamlit as st
        from streamlit import runtime
        if runtime.exists():
            st.cache_data.clear()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 設定 (settings)
# ---------------------------------------------------------------------------
@_cacheable(ttl=120)
def _get_setting_raw(key: str):
    with get_engine().connect() as c:
        row = c.execute(select(settings.c.value).where(settings.c.key == key)).first()
    return None if row is None else row[0]


def get_setting(key: str, default=None):
    raw = _get_setting_raw(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def get_setting_live(key: str, default=None):
    """キャッシュを使わず最新値を読む（進捗バーのポーリング用）。"""
    with get_engine().connect() as c:
        row = c.execute(select(settings.c.value).where(settings.c.key == key)).first()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return row[0]


def set_setting(key: str, value) -> None:
    payload = json.dumps(value, ensure_ascii=False)
    with get_engine().begin() as c:
        exists = c.execute(select(settings.c.key).where(settings.c.key == key)).first()
        if exists:
            c.execute(update(settings).where(settings.c.key == key).values(value=payload))
        else:
            c.execute(insert(settings).values(key=key, value=payload))
    clear_cache()


# ---------------------------------------------------------------------------
# 顧客 (customers)
# ---------------------------------------------------------------------------
_CUST_FIELDS = ("name", "kana", "tel", "zip", "address", "address2",
                "company", "honorific", "note")


@_cacheable(ttl=120)
def list_customers():
    with get_engine().connect() as c:
        rows = c.execute(select(customers).order_by(customers.c.name)).mappings().all()
    return [dict(r) for r in rows]


def get_customer(cid: int):
    with get_engine().connect() as c:
        row = c.execute(select(customers).where(customers.c.id == cid)).mappings().first()
    return dict(row) if row else None


def find_customer(name: str, address: str):
    with get_engine().connect() as c:
        row = c.execute(
            select(customers).where(customers.c.name == name,
                                    customers.c.address == address)
        ).mappings().first()
    return dict(row) if row else None


def upsert_customer(data: dict) -> int:
    existing = find_customer(data.get("name", ""), data.get("address", ""))
    with get_engine().begin() as c:
        if existing:
            vals = {f: data.get(f, existing[f]) for f in _CUST_FIELDS}
            c.execute(update(customers).where(customers.c.id == existing["id"]).values(**vals))
            clear_cache()
            return existing["id"]
        vals = {f: data.get(f, "") for f in _CUST_FIELDS}
        vals["created_at"] = datetime.now().isoformat()
        res = c.execute(insert(customers).values(**vals))
        clear_cache()
        return res.inserted_primary_key[0]


def update_customer(cid: int, data: dict) -> None:
    """idを指定して顧客情報を直接更新（住所変更でも別人扱いにならない）。"""
    vals = {f: data[f] for f in _CUST_FIELDS if f in data}
    if not vals:
        return
    with get_engine().begin() as c:
        c.execute(update(customers).where(customers.c.id == cid).values(**vals))
    clear_cache()


def delete_customer(cid: int) -> None:
    with get_engine().begin() as c:
        c.execute(delete(customers).where(customers.c.id == cid))
    clear_cache()


# ---------------------------------------------------------------------------
# 商品 (products)
# ---------------------------------------------------------------------------
_PROD_FIELDS = ("name", "category", "weight_kg", "needs_milling",
                "yamato_name", "sort_order", "active", "price")


@_cacheable(ttl=120)
def list_products(active_only: bool = True):
    q = select(products)
    if active_only:
        q = q.where(products.c.active == 1)
    q = q.order_by(products.c.sort_order, products.c.id)
    with get_engine().connect() as c:
        rows = c.execute(q).mappings().all()
    return [dict(r) for r in rows]


def get_product(pid: int):
    with get_engine().connect() as c:
        row = c.execute(select(products).where(products.c.id == pid)).mappings().first()
    return dict(row) if row else None


def upsert_product(data: dict) -> int:
    with get_engine().begin() as c:
        existing = c.execute(
            select(products.c.id).where(products.c.name == data["name"])
        ).first()
        vals = {f: data.get(f) for f in _PROD_FIELDS}
        if existing:
            c.execute(update(products).where(products.c.id == existing[0]).values(**vals))
            clear_cache()
            return existing[0]
        res = c.execute(insert(products).values(**vals))
        clear_cache()
        return res.inserted_primary_key[0]


# ---------------------------------------------------------------------------
# 注文 (orders)
# ---------------------------------------------------------------------------
_ORDER_FIELDS = ("customer_id", "product_id", "qty", "channel", "order_date",
                 "ship_date", "delivery_date", "delivery_time",
                 "milling_kg_override", "note", "status", "external_id",
                 "dispatch_ref", "tracking_no", "handover")


def add_order(data: dict) -> int:
    vals = {f: data.get(f) for f in _ORDER_FIELDS}
    vals["created_at"] = datetime.now().isoformat()
    with get_engine().begin() as c:
        res = c.execute(insert(orders).values(**vals))
        clear_cache()
        return res.inserted_primary_key[0]


def order_exists(external_id: str) -> bool:
    if not external_id:
        return False
    with get_engine().connect() as c:
        row = c.execute(
            select(orders.c.id).where(orders.c.external_id == external_id)
        ).first()
    return row is not None


def order_exists_prefix(prefix: str) -> bool:
    """external_id が prefix で始まる注文があるか（BASEの注文単位の取込済み判定）。"""
    if not prefix:
        return False
    with get_engine().connect() as c:
        row = c.execute(
            select(orders.c.id).where(orders.c.external_id.like(f"{prefix}%"))
        ).first()
    return row is not None


_ORDER_JOIN_SQL = """
    SELECT o.*, c.name AS customer_name, c.tel, c.zip, c.address,
           c.address2, c.company, c.honorific, c.kana,
           p.name AS product_name, p.category, p.weight_kg,
           p.needs_milling, p.yamato_name, p.price
    FROM orders o
    JOIN customers c ON c.id = o.customer_id
    JOIN products  p ON p.id = o.product_id
    {where}
    ORDER BY o.created_at DESC
"""


@_cacheable(ttl=45)
def list_orders(status: str | None = None):
    where = "WHERE o.status = :status" if status else ""
    sql = text(_ORDER_JOIN_SQL.format(where=where))
    params = {"status": status} if status else {}
    with get_engine().connect() as c:
        rows = c.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]


def update_order_status(order_ids: list[int], status: str) -> None:
    if not order_ids:
        return
    with get_engine().begin() as c:
        c.execute(update(orders).where(orders.c.id.in_(order_ids)).values(status=status))
    clear_cache()


def update_order(order_id: int, data: dict) -> None:
    allowed = ("qty", "ship_date", "delivery_date", "delivery_time",
               "milling_kg_override", "note", "status", "tracking_no", "handover")
    vals = {f: data[f] for f in data if f in allowed}
    if not vals:
        return
    with get_engine().begin() as c:
        c.execute(update(orders).where(orders.c.id == order_id).values(**vals))
    clear_cache()


def delete_order(order_id: int) -> None:
    with get_engine().begin() as c:
        c.execute(delete(orders).where(orders.c.id == order_id))
    clear_cache()


# ---------------------------------------------------------------------------
# CSV出力ジョブ (export_jobs)
# ---------------------------------------------------------------------------
def enqueue_export(filename: str, content_b64: str) -> int:
    with get_engine().begin() as c:
        res = c.execute(insert(export_jobs).values(
            filename=filename, content_b64=content_b64,
            status="pending", created_at=datetime.now().isoformat(),
        ))
        return res.inserted_primary_key[0]


def list_export_jobs(status: str | None = None):
    q = select(export_jobs)
    if status:
        q = q.where(export_jobs.c.status == status)
    q = q.order_by(export_jobs.c.id.desc())
    with get_engine().connect() as c:
        return c.execute(q).mappings().all()


def mark_export_done(job_id: int, path: str) -> None:
    with get_engine().begin() as c:
        c.execute(update(export_jobs).where(export_jobs.c.id == job_id).values(
            status="done", written_at=datetime.now().isoformat(), written_path=path,
        ))


# ---------------------------------------------------------------------------
# 全データ初期化
# ---------------------------------------------------------------------------
def reset_all() -> None:
    with get_engine().begin() as c:
        for t in (orders, customers, products, settings, export_jobs):
            c.execute(delete(t))
    clear_cache()
