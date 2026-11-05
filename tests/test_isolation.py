from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.ask import ask
from assistant.db import DB_PATH, connect, get_pages


@pytest.fixture(scope="module")
def require_ingest():
    if not DB_PATH.exists():
        pytest.skip("Run ingest first: python -m assistant.cli ingest")
    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()["n"]
    finally:
        conn.close()
    if n == 0:
        pytest.skip("SQLite has no pages; run ingest first")


def test_get_pages_sql_binds_tenant(require_ingest):
    conn = connect()
    try:
        alvery = get_pages(conn, "alvery")
        nord = get_pages(conn, "nordhaven")
    finally:
        conn.close()

    assert alvery, "alvery should have pages after ingest"
    assert nord, "nordhaven should have pages after ingest"
    assert all(p["tenant"] == "alvery" for p in alvery)
    assert all(p["tenant"] == "nordhaven" for p in nord)


def test_alvery_pages_exclude_nordhaven_files(require_ingest):
    conn = connect()
    try:
        pages = get_pages(conn, "alvery")
    finally:
        conn.close()

    files = {p["source_file"] for p in pages}
    assert not any(name.startswith("NP-") for name in files)
    assert "NP-450_Datasheet.pdf" not in files
    assert "NP-200_Service_Manual.pdf" not in files
    assert all("nordhaven" not in p["source_file"].lower() for p in pages)


def test_nordhaven_pages_exclude_alvery_products(require_ingest):
    conn = connect()
    try:
        pages = get_pages(conn, "nordhaven")
    finally:
        conn.close()
    files = {p["source_file"] for p in pages}
    assert not any("FormVise" in f or "ALVERY" in f or "Alvery" in f for f in files)


def test_ask_loads_only_requested_tenant(require_ingest):
    """Prove ask never receives the other tenant's pages (mock LLM)."""
    captured: dict = {}

    def fake_chat_json(messages, **kwargs):
        captured["user"] = messages[1]["content"]

        class R:
            content = '{"answer":"NOT_IN_DOCUMENTS","sources":[],"abstained":true}'
            parsed = {"answer": "NOT_IN_DOCUMENTS", "sources": [], "abstained": True}
            prompt_tokens = 0
            completion_tokens = 0
            cost_usd = 0.0
            model = "test"

        return R()

    with patch("assistant.ask.chat_json", side_effect=fake_chat_json):
        ask("alvery", "What is the maximum head of the NP-450?")

    user = captured["user"]
    assert "NP-450_Datasheet.pdf" not in user
    assert "NP-200_Service_Manual.pdf" not in user
    assert "alvery" in user.lower() or "FormVise" in user or "file=" in user


def test_cross_tenant_questions_abstain_with_mock(require_ingest):
    def fake_chat_json(messages, **kwargs):
        class R:
            content = '{"answer":"NOT_IN_DOCUMENTS","sources":[],"abstained":true}'
            parsed = {"answer": "NOT_IN_DOCUMENTS", "sources": [], "abstained": True}
            prompt_tokens = 1
            completion_tokens = 1
            cost_usd = 0.0
            model = "test"

        return R()

    with patch("assistant.ask.chat_json", side_effect=fake_chat_json):
        a = ask("alvery", "What is the maximum head of the NP-450?")
        b = ask("nordhaven", "What is the list price of the FormVise 100?")
    assert a.abstained
    assert b.abstained
    assert "NOT_IN_DOCUMENTS" in a.answer.upper().replace(" ", "_")
