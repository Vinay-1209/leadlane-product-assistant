from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(__file__).resolve().parent / "store.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY,
  tenant TEXT NOT NULL,
  source_file TEXT NOT NULL,
  page INTEGER NOT NULL,
  text TEXT NOT NULL,
  extraction TEXT NOT NULL,
  doc_kind TEXT,
  superseded INTEGER NOT NULL DEFAULT 0,
  UNIQUE(tenant, source_file, page)
);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY,
  tenant TEXT NOT NULL,
  article_no TEXT NOT NULL,
  name TEXT,
  line TEXT,
  pin_stroke_mm REAL,
  pin_count INTEGER,
  hold_active_n REAL,
  hold_passive_n REAL,
  hold_axial_n REAL,
  max_flow_m3h REAL,
  max_head_m REAL,
  weight_raw TEXT,
  clamping TEXT,
  discontinued INTEGER NOT NULL DEFAULT 0,
  source_file TEXT,
  source_page INTEGER,
  UNIQUE(tenant, article_no)
);

CREATE TABLE IF NOT EXISTS prices (
  id INTEGER PRIMARY KEY,
  tenant TEXT NOT NULL,
  article_no TEXT NOT NULL,
  description TEXT,
  unit_price_eur REAL,
  price_year INTEGER NOT NULL,
  remark TEXT,
  source_file TEXT,
  source_page INTEGER,
  UNIQUE(tenant, article_no, price_year)
);

CREATE TABLE IF NOT EXISTS kit_items (
  id INTEGER PRIMARY KEY,
  tenant TEXT NOT NULL,
  kit_article TEXT,
  kit_name TEXT NOT NULL,
  item_desc TEXT NOT NULL,
  qty REAL,
  torque_range TEXT,
  source_file TEXT,
  source_page INTEGER
);

CREATE TABLE IF NOT EXISTS volume_discounts (
  id INTEGER PRIMARY KEY,
  tenant TEXT NOT NULL,
  min_qty INTEGER NOT NULL,
  max_qty INTEGER,
  pct REAL NOT NULL,
  price_year INTEGER NOT NULL,
  source_file TEXT,
  source_page INTEGER
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  tenant TEXT NOT NULL,
  source_file TEXT NOT NULL,
  article_no TEXT,
  doc_type TEXT NOT NULL DEFAULT 'other',
  issue_date TEXT,
  revision_label TEXT,
  superseded INTEGER NOT NULL DEFAULT 0,
  UNIQUE(tenant, source_file)
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_page(
    conn: sqlite3.Connection,
    *,
    tenant: str,
    source_file: str,
    page: int,
    text: str,
    extraction: str,
    doc_kind: str | None,
    superseded: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO pages (tenant, source_file, page, text, extraction, doc_kind, superseded)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant, source_file, page) DO UPDATE SET
          text = excluded.text,
          extraction = excluded.extraction,
          doc_kind = excluded.doc_kind,
          superseded = excluded.superseded
        """,
        (tenant, source_file, page, text, extraction, doc_kind, superseded),
    )


def get_pages(
    conn: sqlite3.Connection,
    tenant: str,
    *,
    include_superseded: bool = False,
) -> list[dict[str, Any]]:
    """Return pages for one tenant only. Isolation is enforced here."""
    if include_superseded:
        rows = conn.execute(
            "SELECT tenant, source_file, page, text, extraction, doc_kind, superseded "
            "FROM pages WHERE tenant = ? ORDER BY source_file, page",
            (tenant,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT tenant, source_file, page, text, extraction, doc_kind, superseded "
            "FROM pages WHERE tenant = ? AND superseded = 0 "
            "ORDER BY source_file, page",
            (tenant,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_superseded(conn: sqlite3.Connection, tenant: str, source_file: str) -> int:
    cur = conn.execute(
        "UPDATE pages SET superseded = 1 WHERE tenant = ? AND source_file = ?",
        (tenant, source_file),
    )
    conn.execute(
        "UPDATE documents SET superseded = 1 WHERE tenant = ? AND source_file = ?",
        (tenant, source_file),
    )
    return cur.rowcount


def upsert_document(conn: sqlite3.Connection, **kwargs: Any) -> None:
    conn.execute(
        """
        INSERT INTO documents (
          tenant, source_file, article_no, doc_type, issue_date,
          revision_label, superseded
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant, source_file) DO UPDATE SET
          article_no = excluded.article_no,
          doc_type = excluded.doc_type,
          issue_date = COALESCE(excluded.issue_date, documents.issue_date),
          revision_label = COALESCE(excluded.revision_label, documents.revision_label),
          superseded = excluded.superseded
        """,
        (
            kwargs["tenant"],
            kwargs["source_file"],
            kwargs.get("article_no"),
            kwargs.get("doc_type") or "other",
            kwargs.get("issue_date"),
            kwargs.get("revision_label"),
            int(kwargs.get("superseded") or 0),
        ),
    )


def list_documents(
    conn: sqlite3.Connection,
    tenant: str,
    *,
    doc_type: str | None = None,
    article_no: str | None = None,
    include_superseded: bool = False,
) -> list[dict[str, Any]]:
    clauses = ["tenant = ?"]
    args: list[Any] = [tenant]
    if doc_type:
        clauses.append("doc_type = ?")
        args.append(doc_type)
    if article_no:
        clauses.append("article_no = ?")
        args.append(article_no)
    if not include_superseded:
        clauses.append("superseded = 0")
    sql = (
        f"SELECT * FROM documents WHERE {' AND '.join(clauses)} "
        "ORDER BY article_no, source_file"
    )
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def mark_document_current(conn: sqlite3.Connection, tenant: str, source_file: str) -> None:
    """Ensure a file is live on both documents and pages."""
    conn.execute(
        "UPDATE documents SET superseded = 0 WHERE tenant = ? AND source_file = ?",
        (tenant, source_file),
    )
    conn.execute(
        "UPDATE pages SET superseded = 0 WHERE tenant = ? AND source_file = ?",
        (tenant, source_file),
    )


def page_count(conn: sqlite3.Connection, tenant: str | None = None) -> int:
    if tenant is None:
        row = conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant = ?", (tenant,)
        ).fetchone()
    return int(row["n"])


def clear_structured(conn: sqlite3.Connection, tenant: str) -> None:
    for table in ("products", "prices", "kit_items", "volume_discounts"):
        conn.execute(f"DELETE FROM {table} WHERE tenant = ?", (tenant,))


def upsert_product(conn: sqlite3.Connection, **kwargs: Any) -> None:
    cols = [
        "tenant",
        "article_no",
        "name",
        "line",
        "pin_stroke_mm",
        "pin_count",
        "hold_active_n",
        "hold_passive_n",
        "hold_axial_n",
        "max_flow_m3h",
        "max_head_m",
        "weight_raw",
        "clamping",
        "discontinued",
        "source_file",
        "source_page",
    ]
    vals = [kwargs.get(c) for c in cols]
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)
    updates = ", ".join(
        f"{c} = COALESCE(excluded.{c}, products.{c})"
        for c in cols
        if c not in ("tenant", "article_no")
    )
    conn.execute(
        f"""
        INSERT INTO products ({col_sql}) VALUES ({placeholders})
        ON CONFLICT(tenant, article_no) DO UPDATE SET {updates}
        """,
        vals,
    )


def upsert_price(conn: sqlite3.Connection, **kwargs: Any) -> None:
    conn.execute(
        """
        INSERT INTO prices (
          tenant, article_no, description, unit_price_eur, price_year,
          remark, source_file, source_page
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant, article_no, price_year) DO UPDATE SET
          description = excluded.description,
          unit_price_eur = excluded.unit_price_eur,
          remark = excluded.remark,
          source_file = excluded.source_file,
          source_page = excluded.source_page
        """,
        (
            kwargs["tenant"],
            kwargs["article_no"],
            kwargs.get("description"),
            kwargs.get("unit_price_eur"),
            kwargs["price_year"],
            kwargs.get("remark"),
            kwargs.get("source_file"),
            kwargs.get("source_page"),
        ),
    )


def insert_kit_item(conn: sqlite3.Connection, **kwargs: Any) -> None:
    conn.execute(
        """
        INSERT INTO kit_items (
          tenant, kit_article, kit_name, item_desc, qty, torque_range,
          source_file, source_page
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            kwargs["tenant"],
            kwargs.get("kit_article"),
            kwargs["kit_name"],
            kwargs["item_desc"],
            kwargs.get("qty"),
            kwargs.get("torque_range"),
            kwargs.get("source_file"),
            kwargs.get("source_page"),
        ),
    )


def insert_volume_discount(conn: sqlite3.Connection, **kwargs: Any) -> None:
    conn.execute(
        """
        INSERT INTO volume_discounts (
          tenant, min_qty, max_qty, pct, price_year, source_file, source_page
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            kwargs["tenant"],
            kwargs["min_qty"],
            kwargs.get("max_qty"),
            kwargs["pct"],
            kwargs["price_year"],
            kwargs.get("source_file"),
            kwargs.get("source_page"),
        ),
    )


def query_products(
    conn: sqlite3.Connection,
    tenant: str,
    *,
    min_stroke_mm: float | None = None,
    min_hold_active_n: float | None = None,
    min_hold_any_n: float | None = None,
    min_flow_m3h: float | None = None,
) -> list[dict[str, Any]]:
    clauses = ["tenant = ?"]
    args: list[Any] = [tenant]
    if min_stroke_mm is not None:
        clauses.append("pin_stroke_mm >= ?")
        args.append(min_stroke_mm)
    if min_hold_active_n is not None:
        clauses.append("hold_active_n >= ?")
        args.append(min_hold_active_n)
    if min_hold_any_n is not None:
        clauses.append(
            "(COALESCE(hold_active_n, 0) >= ? OR COALESCE(hold_passive_n, 0) >= ? "
            "OR COALESCE(hold_axial_n, 0) >= ?)"
        )
        args.extend([min_hold_any_n, min_hold_any_n, min_hold_any_n])
    if min_flow_m3h is not None:
        clauses.append("max_flow_m3h >= ?")
        args.append(min_flow_m3h)
    sql = f"SELECT * FROM products WHERE {' AND '.join(clauses)} ORDER BY article_no"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_price(
    conn: sqlite3.Connection,
    tenant: str,
    *,
    article_no: str | None = None,
    name_substr: str | None = None,
    price_year: int = 2026,
) -> dict[str, Any] | None:
    if article_no:
        row = conn.execute(
            "SELECT * FROM prices WHERE tenant = ? AND article_no = ? AND price_year = ?",
            (tenant, article_no, price_year),
        ).fetchone()
        return dict(row) if row else None
    if name_substr:
        row = conn.execute(
            "SELECT * FROM prices WHERE tenant = ? AND price_year = ? "
            "AND description LIKE ? ORDER BY article_no LIMIT 1",
            (tenant, price_year, f"%{name_substr}%"),
        ).fetchone()
        return dict(row) if row else None
    return None
