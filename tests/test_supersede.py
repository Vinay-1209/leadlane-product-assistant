from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.catalogue import build_catalogue
from assistant.db import connect, get_pages, upsert_page
from assistant.ingest import ingest_tenant
from assistant.structured import complete_spring_force_answer, _revision_conflict_block
from assistant.versioning import (
    live_datasheet_conflicts,
    parse_issue_date,
    register_document,
    resolve_datasheet_versions,
)

OLD_DS = "ALVERY-Datasheet_44.0672_AutoForm 67.pdf"
NEW_DS = "ALVERY-Datasheet_44.0672_AutoForm 67_rev2026-09.pdf"
SPRING = "ALVERY-44.0672_AutoForm 67_Spring_Force.pdf"
UPDATE = ROOT / "updates" / "alvery" / NEW_DS


def _seed_page(conn, fname: str, text: str, *, kind: str = "datasheets") -> None:
    upsert_page(
        conn,
        tenant="alvery",
        source_file=fname,
        page=1,
        text=text,
        extraction="text",
        doc_kind=kind,
        superseded=0,
    )
    register_document(conn, "alvery", fname, text, kind, superseded=0)


def test_parse_issue_date_from_filename_rev():
    iso, _ = parse_issue_date("body", NEW_DS)
    assert iso == "2026-09-01"


def test_parse_issue_date_from_body():
    iso, _ = parse_issue_date("Issue date 15.11.2019\nOrder no. 44.0672", OLD_DS)
    assert iso == "2019-11-15"


def test_date_ladder_supersedes_without_rev_in_name(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = connect(db)
    try:
        old_f = "ALVERY-Datasheet_44.0672_AutoForm67_old.pdf"
        new_f = "ALVERY-Datasheet_44.0672_AutoForm67_new.pdf"
        _seed_page(
            conn,
            old_f,
            "Order no. 44.0672\nIssue date 15.11.2019\nMax. axial holding force, active 4,2 kN",
        )
        _seed_page(
            conn,
            new_f,
            "Order no. 44.0672\nIssue date 01.09.2026\nMax. axial holding force, active 4,8 kN",
        )
        conflicts = resolve_datasheet_versions(conn, "alvery")
        conn.commit()
        pages = {
            p["source_file"]: p["superseded"]
            for p in get_pages(conn, "alvery", include_superseded=True)
        }
    finally:
        conn.close()

    assert not conflicts
    assert pages[old_f] == 1
    assert pages[new_f] == 0


def test_filename_rev_fallback_spares_spring_force(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = connect(db)
    try:
        _seed_page(
            conn,
            OLD_DS,
            "Order no. 44.0672\nMax. axial holding force, active 4,2 kN",
        )
        _seed_page(
            conn,
            NEW_DS,
            "Order no. 44.0672\nMax. axial holding force, active 4,8 kN",
        )
        _seed_page(conn, SPRING, "10  50.60  0.703", kind="datasheets")
        # Force spring type via register (filename has Spring_Force)
        register_document(
            conn, "alvery", SPRING, "10  50.60  0.703", "datasheets", superseded=0
        )
        conflicts = resolve_datasheet_versions(conn, "alvery")
        conn.commit()
        pages = {
            p["source_file"]: p["superseded"]
            for p in get_pages(conn, "alvery", include_superseded=True)
        }
        docs = {
            r["source_file"]: r["doc_type"]
            for r in conn.execute(
                "SELECT source_file, doc_type FROM documents WHERE tenant='alvery'"
            )
        }
    finally:
        conn.close()

    assert docs[SPRING] == "spring_force"
    assert pages[OLD_DS] == 1
    assert pages[NEW_DS] == 0
    assert pages[SPRING] == 0
    assert not any(c["article_no"] == "44.0672" and NEW_DS in c.get("files", []) for c in conflicts)


def test_ambiguous_no_date_no_rev_leaves_both_live(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = connect(db)
    try:
        a = "ALVERY-Datasheet_44.0672_A.pdf"
        b = "ALVERY-Datasheet_44.0672_B.pdf"
        _seed_page(conn, a, "Order no. 44.0672\nMax. axial holding force, active 4,2 kN")
        _seed_page(conn, b, "Order no. 44.0672\nMax. axial holding force, active 4,8 kN")
        conflicts = resolve_datasheet_versions(conn, "alvery")
        conn.commit()
        pages = {
            p["source_file"]: p["superseded"]
            for p in get_pages(conn, "alvery", include_superseded=True)
        }
        live = live_datasheet_conflicts(conn, "alvery")
        block = _revision_conflict_block(
            conn, "alvery", "What is the active holding force of the AutoForm 67?"
        )
    finally:
        conn.close()

    assert pages[a] == 0 and pages[b] == 0
    assert any(c["article_no"] == "44.0672" for c in conflicts)
    assert any(c["article_no"] == "44.0672" for c in live)
    assert block and "Revision conflict" in block
    assert a in block and b in block


def test_complete_spring_force_appends_missing_pin():
    q = "What is the pin field spring force of the AutoForm 67 at 10 mm pin stroke?"
    block = (
        "### Spring force table row\n"
        "- pin field force: 50.60 N\n"
        "- single pin force: 0.703 N\n"
    )
    out = complete_spring_force_answer(
        q, "The pin field spring force at 10 mm is 50.60 N.", block
    )
    assert "0.703" in out
    assert "50.60" in out


@pytest.mark.skipif(not UPDATE.exists(), reason="updates/alvery PDF missing")
def test_ingest_update_path_supersedes_and_catalogue():
    """Requires prior full ingest of data/alvery (shared store.sqlite)."""
    from assistant.db import DB_PATH

    if not DB_PATH.exists():
        pytest.skip("Run full ingest first")

    ingest_tenant("alvery", path=ROOT / "updates" / "alvery")

    conn = connect()
    try:
        live = get_pages(conn, "alvery")
        files = {p["source_file"] for p in live}
        assert NEW_DS in files
        assert OLD_DS not in files
        assert any("Spring_Force" in f for f in files)

        build_catalogue("alvery")
        af = conn.execute(
            "SELECT * FROM products WHERE tenant = 'alvery' AND article_no = '44.0672'"
        ).fetchone()
    finally:
        conn.close()

    assert af is not None
    assert float(af["hold_active_n"]) == pytest.approx(4800.0)
    assert NEW_DS in (af["source_file"] or "")
