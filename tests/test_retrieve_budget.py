from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.db import DB_PATH, connect, get_pages
from assistant.retrieve import estimate_tokens, select_pages


@pytest.fixture(scope="module")
def require_ingest():
    if not DB_PATH.exists():
        pytest.skip("Run ingest first")
    conn = connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE tenant = 'alvery'"
        ).fetchone()["n"]
    finally:
        conn.close()
    if n == 0:
        pytest.skip("No alvery pages")


def test_hybrid_budget_under_cap(require_ingest):
    conn = connect()
    try:
        budget = 6500
        pages = select_pages(
            conn,
            "alvery",
            "How long is the warranty on a FormBed e?",
            budget_tokens=budget,
        )
        all_pages = get_pages(conn, "alvery")
    finally:
        conn.close()

    assert pages
    used = sum(estimate_tokens(p.get("text") or "") for p in pages)
    # select_pages reserves ~800 for prompt overhead
    assert used <= budget
    assert len(pages) < len(all_pages) or used <= budget - 800


def test_bm25_warranty_prefers_terms(require_ingest):
    conn = connect()
    try:
        pages = select_pages(
            conn,
            "alvery",
            "How long is the warranty on a FormBed e?",
            budget_tokens=6500,
        )
    finally:
        conn.close()
    names = " ".join(p["source_file"].lower() for p in pages[:8])
    assert "terms" in names


def test_alvery_retrieve_excludes_nordhaven(require_ingest):
    conn = connect()
    try:
        pages = select_pages(
            conn, "alvery", "What is the maximum head of the NP-450?", budget_tokens=6500
        )
    finally:
        conn.close()
    for p in pages:
        assert not p["source_file"].startswith("NP-")
        assert p["tenant"] == "alvery"
