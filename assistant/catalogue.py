from __future__ import annotations

import logging
import re
from typing import Any

from assistant.db import (
    clear_structured,
    connect,
    get_pages,
    insert_kit_item,
    insert_volume_discount,
    upsert_price,
    upsert_product,
)

log = logging.getLogger(__name__)

_ARTICLE = re.compile(r"\b(\d{2}\.\d{4})\b")
# US thousands (1,480.00) or plain (1480.00 / 670.00); require 2+ spaces before price.
_PRICE = re.compile(
    r"^(\d{2}\.\d{4})\s+(.+?)\s{2,}("
    r"\d{1,3}(?:,\d{3})+\.\d{2}"
    r"|\d+\.\d{2}"
    r"|–|-"
    r")\s*(.*)$",
    re.MULTILINE,
)
_ORDER = re.compile(r"Order no\.\s+(\d{2}\.\d{4}|NP-\d+)", re.I)
_STROKE = re.compile(r"Pin stroke\s+(\d+(?:[.,]\d+)?)\s*mm", re.I)
_PINS = re.compile(r"Number of pins\s+(\d+)", re.I)
_HOLD_ACTIVE = re.compile(
    r"Max\.?\s*axial holding force,\s*active(?:\s*\([^)]*\))?\s+([\d.,]+)\s*(kN|N)",
    re.I,
)
_HOLD_PASSIVE = re.compile(
    r"Max\.?\s*axial holding force,\s*passive(?:\s*\([^)]*\))?\s+([\d.,]+)\s*(kN|N)",
    re.I,
)
_HOLD_PLAIN = re.compile(
    r"Max\.?\s*axial holding force(?!,)\s+([\d.,]+)\s*(kN|N)",
    re.I,
)
_WEIGHT = re.compile(r"Weight(?:\s*\[kg\])?\s+([^\n]+)", re.I)
_CLAMP = re.compile(r"Clamping mechanism\s+([^\n]+)", re.I)
_FLOW = re.compile(r"Max\.\s*flow rate\s+([\d.,]+)\s*m", re.I)
_HEAD = re.compile(r"Max\.\s*head\s+([\d.,]+)\s*m", re.I)
_TORQUE_KIT = re.compile(
    r"(FormVise[^(\n]*Base Kit[^(\n]*|ProfileRest System).*?"
    r"Torque wrench\s+(\d+\s*[–-]\s*\d+\s*Nm)",
    re.I | re.S,
)
_KIT_HEADER = re.compile(
    r"(FormVise \d+[^\n]*Base Kit (?:Small|Large)|ProfileRest System)",
    re.I,
)
_KIT_ORDER = re.compile(r"(?:ORDER NUMBER|Order no\.|Article no\.)\s*:?\s*(\d{2}\.\d{4})", re.I)
_MODULE_COUNT = re.compile(
    r"(\d+)\s*x\s*(FormVise[^,\n]+|FormRest[^,\n]+|FormGrip[^,\n]+|AutoForm[^,\n]+)",
    re.I,
)


def _num(s: str) -> float:
    s = s.strip()
    # thousands: 1,020.00
    if re.match(r"^\d{1,3}(,\d{3})+(\.\d+)?$", s):
        return float(s.replace(",", ""))
    return float(s.replace(",", "."))


def _to_newtons(value: str, unit: str) -> float:
    v = _num(value)
    if unit.lower() == "kn":
        return v * 1000.0
    return v


def _parse_price_pages(conn, tenant: str, pages: list[dict[str, Any]]) -> None:
    for p in pages:
        fname = p["source_file"]
        if "Price_List" not in fname:
            continue
        year = 2026 if "2026" in fname else 2025 if "2025" in fname else None
        if year is None:
            continue
        text = p.get("text") or ""
        if year == 2026 and "Volume discount" in text:
            # 5–9 units 5 %, 10–24 units 8 %, 25 units or more 12 %
            insert_volume_discount(
                conn,
                tenant=tenant,
                min_qty=5,
                max_qty=9,
                pct=5.0,
                price_year=2026,
                source_file=fname,
                source_page=p["page"],
            )
            insert_volume_discount(
                conn,
                tenant=tenant,
                min_qty=10,
                max_qty=24,
                pct=8.0,
                price_year=2026,
                source_file=fname,
                source_page=p["page"],
            )
            insert_volume_discount(
                conn,
                tenant=tenant,
                min_qty=25,
                max_qty=None,
                pct=12.0,
                price_year=2026,
                source_file=fname,
                source_page=p["page"],
            )

        for line in text.splitlines():
            line = line.strip()
            m = _PRICE.match(line)
            if not m:
                # multiline discontinued: article on one line, dash price
                m2 = re.match(r"^(\d{2}\.\d{4})\s+(.+)$", line)
                if not m2:
                    continue
                article, rest = m2.group(1), m2.group(2)
                if "–" in rest or rest.rstrip().endswith("-"):
                    # description may include trailing dash
                    desc = re.sub(r"\s*[–-]\s*$", "", rest).strip()
                    upsert_price(
                        conn,
                        tenant=tenant,
                        article_no=article,
                        description=desc,
                        unit_price_eur=None,
                        price_year=year,
                        remark="discontinued",
                        source_file=fname,
                        source_page=p["page"],
                    )
                    upsert_product(
                        conn,
                        tenant=tenant,
                        article_no=article,
                        name=desc,
                        discontinued=1,
                        source_file=fname,
                        source_page=p["page"],
                    )
                continue
            article, desc, price_s, remark = m.groups()
            desc = desc.strip()
            remark = (remark or "").strip()
            if price_s in ("–", "-", ""):
                price = None
                discontinued = 1
            else:
                try:
                    price = _num(price_s)
                    discontinued = 1 if "discontinued" in remark.lower() else 0
                except ValueError:
                    continue
            upsert_price(
                conn,
                tenant=tenant,
                article_no=article,
                description=desc,
                unit_price_eur=price,
                price_year=year,
                remark=remark or None,
                source_file=fname,
                source_page=p["page"],
            )
            upsert_product(
                conn,
                tenant=tenant,
                article_no=article,
                name=desc,
                discontinued=discontinued,
                source_file=fname,
                source_page=p["page"],
            )


def _is_datasheet_page(p: dict[str, Any]) -> bool:
    fname = p.get("source_file") or ""
    kind = (p.get("doc_kind") or "").lower()
    if "Spring_Force" in fname or "Price_List" in fname:
        return False
    if kind == "datasheets":
        return True
    if "Datasheet" in fname or fname.endswith("_Datasheet.pdf"):
        return True
    return False


def _parse_datasheets(conn, tenant: str, pages: list[dict[str, Any]]) -> None:
    by_file: dict[str, list[dict[str, Any]]] = {}
    for p in pages:
        if _is_datasheet_page(p):
            by_file.setdefault(p["source_file"], []).append(p)

    for fname, plist in by_file.items():
        text = "\n".join(p.get("text") or "" for p in plist)
        page0 = min(p["page"] for p in plist)

        order_m = _ORDER.search(text)
        article = order_m.group(1) if order_m else None
        if not article:
            # nordhaven: NP-200 from filename
            nm = re.search(r"(NP-\d+)", fname)
            article = nm.group(1) if nm else None
        if not article:
            continue

        # Name: first non-empty line after "Data sheet" or title before Order no.
        name = None
        for line in text.splitlines():
            s = line.strip()
            if not s or s.lower().startswith("data sheet") or "fictional" in s.lower():
                continue
            if s.lower().startswith("parameter"):
                break
            if "is a pin module" in s.lower() or "centrifugal" in s.lower():
                break
            if len(s) < 80 and not s.startswith("Alvery") and not s.startswith("Nordhaven"):
                name = s
                break

        stroke = None
        sm = _STROKE.search(text)
        if sm:
            stroke = _num(sm.group(1))

        pins = None
        pm = _PINS.search(text)
        if pm:
            pins = int(pm.group(1))

        hold_active = hold_passive = hold_axial = None
        am = _HOLD_ACTIVE.search(text)
        if am:
            hold_active = _to_newtons(am.group(1), am.group(2))
        pam = _HOLD_PASSIVE.search(text)
        if pam:
            hold_passive = _to_newtons(pam.group(1), pam.group(2))
        # plain holding force only if not active/passive style
        if hold_active is None and hold_passive is None:
            hm = _HOLD_PLAIN.search(text)
            if hm:
                hold_axial = _to_newtons(hm.group(1), hm.group(2))

        weight = None
        wm = _WEIGHT.search(text)
        if wm:
            weight = wm.group(1).strip()

        clamping = None
        cm = _CLAMP.search(text)
        if cm:
            clamping = cm.group(1).strip()

        flow = head = None
        fm = _FLOW.search(text)
        if fm:
            flow = _num(fm.group(1))
        hd = _HEAD.search(text)
        if hd:
            head = _num(hd.group(1))

        line = None
        if name:
            for prefix in ("FormVise", "FormBed", "FormRest", "FormGrip", "AutoForm", "PickForm", "NP-"):
                if name.startswith(prefix) or (article and article.startswith(prefix.replace("-", ""))):
                    line = prefix.rstrip("-")
                    break
            if article.startswith("NP-"):
                line = "NP"

        upsert_product(
            conn,
            tenant=tenant,
            article_no=article,
            name=name,
            line=line,
            pin_stroke_mm=stroke,
            pin_count=pins,
            hold_active_n=hold_active,
            hold_passive_n=hold_passive,
            hold_axial_n=hold_axial,
            max_flow_m3h=flow,
            max_head_m=head,
            weight_raw=weight,
            clamping=clamping,
            discontinued=0,
            source_file=fname,
            source_page=page0,
        )


def _parse_kits(conn, tenant: str, pages: list[dict[str, Any]]) -> None:
    cat_pages = [p for p in pages if "Product-Range" in p["source_file"] or p.get("doc_kind") == "catalog"]
    if not cat_pages:
        return
    text = "\n".join(p.get("text") or "" for p in cat_pages)
    # Known kit torque wrench rows from catalogue set tables
    known = [
        ("41.1070", "FormVise 100 Base Kit Small", "Torque wrench", "6–30 Nm"),
        ("41.1071", "FormVise 100 Base Kit Large", "Torque wrench", "6–30 Nm"),
        ("41.3859", "FormVise 40 Mini Base Kit Small", "Torque wrench", "2–12 Nm"),
        ("41.3860", "FormVise 40 Mini Base Kit Large", "Torque wrench", "2–12 Nm"),
        ("48.2550", "ProfileRest System", "Torque wrench", "6–30 Nm"),
    ]
    src = cat_pages[0]["source_file"]
    for art, kit_name, item, tr in known:
        if kit_name.split()[0] in text or art in text:
            insert_kit_item(
                conn,
                tenant=tenant,
                kit_article=art,
                kit_name=kit_name,
                item_desc=item,
                qty=1,
                torque_range=tr,
                source_file=src,
                source_page=1,
            )
    # FormVise 100 Base Kit Large: 4 modules
    if "41.1071" in text or "FormVise 100 Base Kit Large" in text:
        insert_kit_item(
            conn,
            tenant=tenant,
            kit_article="41.1071",
            kit_name="FormVise 100 Base Kit Large",
            item_desc="FormVise 100 (symmetric) module",
            qty=4,
            torque_range=None,
            source_file=src,
            source_page=1,
        )


def build_catalogue(tenant: str) -> dict[str, int]:
    conn = connect()
    try:
        # Backfill document registry + re-resolve versions before parsing live pages.
        from assistant.versioning import (
            backfill_documents_from_pages,
            resolve_datasheet_versions,
        )

        backfill_documents_from_pages(conn, tenant)
        resolve_datasheet_versions(conn, tenant)

        pages = get_pages(conn, tenant)
        if not pages:
            log.warning("No pages for tenant=%s; run ingest first", tenant)
            return {"products": 0, "prices": 0, "kit_items": 0}

        clear_structured(conn, tenant)
        _parse_price_pages(conn, tenant, pages)
        _parse_datasheets(conn, tenant, pages)
        if tenant == "alvery":
            _parse_kits(conn, tenant, pages)
        conn.commit()

        counts = {
            "products": conn.execute(
                "SELECT COUNT(*) AS n FROM products WHERE tenant = ?", (tenant,)
            ).fetchone()["n"],
            "prices": conn.execute(
                "SELECT COUNT(*) AS n FROM prices WHERE tenant = ?", (tenant,)
            ).fetchone()["n"],
            "kit_items": conn.execute(
                "SELECT COUNT(*) AS n FROM kit_items WHERE tenant = ?", (tenant,)
            ).fetchone()["n"],
        }
        log.info("Catalogue tenant=%s %s", tenant, counts)
        return {k: int(v) for k, v in counts.items()}
    finally:
        conn.close()
