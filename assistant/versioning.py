"""Document identity + datasheet revision resolve (date ladder, filename fallback, conflict)."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date
from typing import Any

from assistant.db import (
    list_documents,
    mark_document_current,
    mark_superseded,
    upsert_document,
)

log = logging.getLogger(__name__)

_ARTICLE = re.compile(r"\b(\d{2}\.\d{4})\b")
_ORDER = re.compile(r"Order no\.\s+(\d{2}\.\d{4}|NP-\d+)", re.I)
# 15.11.2019 or 15/11/2019
_DMY = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
# ISO-ish or filename rev2026-09
_YM = re.compile(r"(?:rev)?(\d{4})-(\d{2})(?:-(\d{2}))?", re.I)
_REV_LABEL = re.compile(r"\brev(?:ision)?[\s._-]*([0-9]{4}[-/.]?[0-9]{0,2}[-/.]?[0-9]{0,2})", re.I)


def infer_doc_type(source_file: str, doc_kind: str | None = None) -> str:
    name = (source_file or "").lower()
    kind = (doc_kind or "").lower()
    if "spring_force" in name or "spring force" in name:
        return "spring_force"
    if "price_list" in name or "price" in kind:
        return "price_list"
    if "datasheet" in name or kind == "datasheets":
        return "datasheet"
    return "other"


def parse_article(text: str, source_file: str) -> str | None:
    m = _ORDER.search(text or "")
    if m:
        return m.group(1)
    m = _ARTICLE.search(source_file or "")
    if m:
        return m.group(1)
    m = _ARTICLE.search(text or "")
    return m.group(1) if m else None


def _to_iso(y: int, month: int, day: int = 1) -> str | None:
    try:
        return date(y, month, day).isoformat()
    except ValueError:
        return None


def parse_issue_date(text: str, source_file: str) -> tuple[str | None, str | None]:
    """Return (iso_date, revision_label). Prefer explicit body dates, then filename rev."""
    label: str | None = None
    lm = _REV_LABEL.search(source_file or "") or _REV_LABEL.search(text or "")
    if lm:
        label = lm.group(0).strip()

    # Filename _rev2026-09
    fm = re.search(r"_rev(\d{4})-(\d{2})(?:-(\d{2}))?", source_file or "", re.I)
    if fm:
        iso = _to_iso(int(fm.group(1)), int(fm.group(2)), int(fm.group(3) or 1))
        if iso:
            return iso, label or fm.group(0)

    body = text or ""
    # Prefer "Issue date" / "Revision" lines if present
    for line in body.splitlines():
        low = line.lower()
        if not any(k in low for k in ("issue", "revision", "rev.", "dated", "version")):
            # still allow bare DMY later
            pass
        dm = _DMY.search(line)
        if dm and any(k in low for k in ("issue", "revision", "rev", "date", "version")):
            d, m, y = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
            iso = _to_iso(y, m, d)
            if iso:
                return iso, label or line.strip()[:80]

    # Any DMY in first 2k chars (datasheets often put date near header)
    head = body[:2000]
    dm = _DMY.search(head)
    if dm:
        d, m, y = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
        # Heuristic: day-month-year European
        iso = _to_iso(y, m, d)
        if iso:
            return iso, label

    ym = _YM.search(source_file or "")
    if ym:
        iso = _to_iso(int(ym.group(1)), int(ym.group(2)), int(ym.group(3) or 1))
        if iso:
            return iso, label

    return None, label


def register_document(
    conn,
    tenant: str,
    source_file: str,
    text: str,
    doc_kind: str | None = None,
    *,
    superseded: int = 0,
) -> dict[str, Any]:
    doc_type = infer_doc_type(source_file, doc_kind)
    article = parse_article(text, source_file)
    issue_date, revision_label = parse_issue_date(text, source_file)
    upsert_document(
        conn,
        tenant=tenant,
        source_file=source_file,
        article_no=article,
        doc_type=doc_type,
        issue_date=issue_date,
        revision_label=revision_label,
        superseded=superseded,
    )
    return {
        "tenant": tenant,
        "source_file": source_file,
        "article_no": article,
        "doc_type": doc_type,
        "issue_date": issue_date,
        "revision_label": revision_label,
        "superseded": superseded,
    }


def _has_rev_token(source_file: str) -> bool:
    return bool(re.search(r"[\W_]rev", (source_file or "").lower()))


def resolve_datasheet_versions(conn, tenant: str) -> list[dict[str, Any]]:
    """
    For each article with multiple datasheets, pick a winner by date, else filename rev,
    else leave all live and return conflict records.
    """
    docs = list_documents(conn, tenant, doc_type="datasheet", include_superseded=True)
    by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in docs:
        art = d.get("article_no")
        if art:
            by_article[art].append(d)

    conflicts: list[dict[str, Any]] = []
    for article, group in by_article.items():
        if len(group) < 2:
            # Single datasheet: ensure live if it was wrongly flagged (no-op usually)
            continue

        dated = [(d, d.get("issue_date")) for d in group if d.get("issue_date")]
        if len(dated) >= 2 or (len(dated) == 1 and len(group) >= 2):
            # Rank: entries with dates first by date desc; undated lose to any dated
            def sort_key(d: dict[str, Any]) -> tuple:
                iso = d.get("issue_date") or ""
                return (1 if iso else 0, iso)

            ranked = sorted(group, key=sort_key, reverse=True)
            best = ranked[0]
            best_date = best.get("issue_date")
            # Only auto-supersede if best has a date and every other is strictly older or undated
            if best_date:
                winners = [d for d in group if d.get("issue_date") == best_date]
                if len(winners) == 1:
                    winner = winners[0]["source_file"]
                    mark_document_current(conn, tenant, winner)
                    for d in group:
                        if d["source_file"] != winner:
                            n = mark_superseded(conn, tenant, d["source_file"])
                            if n:
                                log.info(
                                    "Superseded %s → current %s (date %s > %s)",
                                    d["source_file"],
                                    winner,
                                    best_date,
                                    d.get("issue_date"),
                                )
                    continue
                # Same date on multiple files → conflict
                conflicts.append(
                    {
                        "article_no": article,
                        "reason": "same_or_unclear_dates",
                        "files": [d["source_file"] for d in winners],
                    }
                )
                continue

        # Filename fallback: exactly one file with 'rev' token
        rev_files = [d for d in group if _has_rev_token(d["source_file"])]
        non_rev = [d for d in group if not _has_rev_token(d["source_file"])]
        if len(rev_files) == 1 and non_rev:
            winner = rev_files[0]["source_file"]
            mark_document_current(conn, tenant, winner)
            for d in non_rev:
                n = mark_superseded(conn, tenant, d["source_file"])
                if n:
                    log.info(
                        "Superseded %s → current %s (filename rev fallback)",
                        d["source_file"],
                        winner,
                    )
            continue

        # Ambiguous: leave all as-is (typically live); report conflict
        conflicts.append(
            {
                "article_no": article,
                "reason": "ambiguous_no_date_or_single_rev",
                "files": [d["source_file"] for d in group],
            }
        )
        log.warning(
            "Datasheet revision conflict tenant=%s article=%s files=%s",
            tenant,
            article,
            [d["source_file"] for d in group],
        )

    return conflicts


def live_datasheet_conflicts(conn, tenant: str) -> list[dict[str, Any]]:
    """Articles with ≥2 non-superseded datasheet documents."""
    docs = list_documents(conn, tenant, doc_type="datasheet", include_superseded=False)
    by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in docs:
        if d.get("article_no"):
            by_article[d["article_no"]].append(d)
    out = []
    for art, group in by_article.items():
        if len(group) >= 2:
            out.append({"article_no": art, "files": [d["source_file"] for d in group]})
    return out


def backfill_documents_from_pages(conn, tenant: str) -> int:
    """Register documents from existing pages (e.g. DB created before documents table)."""
    rows = conn.execute(
        "SELECT source_file, doc_kind, superseded, "
        "GROUP_CONCAT(text, char(10)) AS text "
        "FROM pages WHERE tenant = ? GROUP BY source_file, doc_kind, superseded",
        (tenant,),
    ).fetchall()
    n = 0
    for r in rows:
        register_document(
            conn,
            tenant,
            r["source_file"],
            r["text"] or "",
            r["doc_kind"],
            superseded=int(r["superseded"] or 0),
        )
        n += 1
    return n
