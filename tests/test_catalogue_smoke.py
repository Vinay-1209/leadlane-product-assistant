from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.catalogue import build_catalogue
from assistant.db import DB_PATH, connect


@pytest.fixture(scope="module")
def catalogue_ready():
    if not DB_PATH.exists():
        pytest.skip("Run ingest first")
    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()["n"]
    finally:
        conn.close()
    if n == 0:
        pytest.skip("Empty DB")
    build_catalogue("alvery")
    build_catalogue("nordhaven")


def test_alvery_2026_prices(catalogue_ready):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM prices WHERE tenant = 'alvery' AND price_year = 2026 "
            "AND description LIKE '%AutoForm Mini%' AND description NOT LIKE '%Duo%'"
        ).fetchall()
        fv = conn.execute(
            "SELECT * FROM prices WHERE tenant = 'alvery' AND article_no = '41.0852' "
            "AND price_year = 2026"
        ).fetchone()
    finally:
        conn.close()
    assert rows, "AutoForm Mini 2026 price missing"
    assert float(rows[0]["unit_price_eur"]) == 1290.0
    assert fv is not None
    assert float(fv["unit_price_eur"]) == 1480.0


def test_alvery_stroke_and_force(catalogue_ready):
    conn = connect()
    try:
        stroke = conn.execute(
            "SELECT * FROM products WHERE tenant = 'alvery' AND pin_stroke_mm >= 30"
        ).fetchall()
        af = conn.execute(
            "SELECT * FROM products WHERE tenant = 'alvery' AND article_no = '44.0672'"
        ).fetchone()
    finally:
        conn.close()
    assert stroke, "expected products with stroke >= 30"
    assert af is not None
    assert af["hold_active_n"] is not None
    # 4.2 kN initial datasheet; 4.8 kN after updates/alvery ingest
    assert float(af["hold_active_n"]) in (4200.0, 4800.0)


def test_volume_discounts(catalogue_ready):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM volume_discounts WHERE tenant = 'alvery' AND price_year = 2026"
        ).fetchall()
    finally:
        conn.close()
    pcts = {int(r["min_qty"]): float(r["pct"]) for r in rows}
    assert pcts.get(10) == 8.0


def test_nordhaven_flow(catalogue_ready):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM products WHERE tenant = 'nordhaven' AND max_flow_m3h >= 300"
        ).fetchall()
    finally:
        conn.close()
    assert any(r["article_no"] == "NP-450" for r in rows)
