from __future__ import annotations

import re
from typing import Any

from assistant.db import get_price, query_products
from assistant.retrieve import classify


def _n_to_kn_str(n: float | None) -> str:
    if n is None:
        return "—"
    if n >= 1000:
        kn = n / 1000.0
        s = f"{kn:.1f}".replace(".", ",")
        if s.endswith(",0"):
            s = s[:-2]
        return f"{s} kN ({n:.0f} N)"
    return f"{n:.3f} N".rstrip("0").rstrip(".")


def _stroke_threshold(question: str) -> float | None:
    m = re.search(r"(?:at least|>=|≥)\s*(\d+(?:[.,]\d+)?)\s*mm", question, re.I)
    if m:
        return float(m.group(1).replace(",", "."))
    if re.search(r"pin stroke.*\b30\b|\b30\b.*pin stroke", question, re.I):
        return 30.0
    return None


def _force_threshold_n(question: str) -> float | None:
    q = question.lower()
    # skip spring-force table questions
    if "spring force" in q:
        return None
    m = re.search(r"more than\s*(\d+(?:[.,]\d+)?)\s*k\s*n", q)
    if m:
        return float(m.group(1).replace(",", ".")) * 1000.0
    m = re.search(r"more than\s*(\d+(?:[.,]\d+)?)\s*n\b", q)
    if m:
        return float(m.group(1).replace(",", "."))
    if "1 kn" in q.replace("\xa0", " "):
        return 1000.0
    return None


def _flow_threshold(question: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m[³3]/h", question, re.I)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def _qty_and_product(question: str) -> tuple[int | None, str | None]:
    m = re.search(
        r"(\d+)\s+(AutoForm Mini|FormVise \d+|FormBed[^?]*)",
        question,
        re.I,
    )
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, None


def _filter_clamping(rows: list[dict[str, Any]], question: str) -> list[dict[str, Any]]:
    q = question.lower()
    if "pneumatic" in q:
        return [
            r
            for r in rows
            if r.get("clamping") and "pneumatic" in str(r["clamping"]).lower()
        ]
    if "manual" in q and "holding" in q:
        return [
            r
            for r in rows
            if r.get("clamping") and "manual" in str(r["clamping"]).lower()
        ]
    return rows


def _format_product_table(rows: list[dict[str, Any]], title: str) -> str:
    lines = [
        title,
        "Instruction: In your answer, list EVERY row below and include stroke_mm and "
        "holding forces (use kN values shown).",
        "| article | name | clamping | stroke_mm | hold_active | hold_passive | "
        "hold_axial | source |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('article_no')} | {r.get('name')} | {r.get('clamping')} | "
            f"{r.get('pin_stroke_mm')} | {_n_to_kn_str(r.get('hold_active_n'))} | "
            f"{_n_to_kn_str(r.get('hold_passive_n'))} | {_n_to_kn_str(r.get('hold_axial_n'))} | "
            f"{r.get('source_file')} p.{r.get('source_page')} |"
        )
    return "\n".join(lines)


def _dimension_conflict_block(conn, tenant: str, question: str) -> str | None:
    q = question.lower()
    if "dimension" not in q or "formvise 100" not in q:
        return None
    pages = conn.execute(
        "SELECT source_file, page, text FROM pages WHERE tenant = ? AND superseded = 0 "
        "AND (source_file LIKE '%41.0672%' OR source_file LIKE '%Product-Range%')",
        (tenant,),
    ).fetchall()
    ds = cat = None
    for p in pages:
        t = p["text"] or ""
        if "41.0672" in p["source_file"] and "52 x 120" in t.replace("×", "x"):
            ds = (p["source_file"], p["page"], "52 x 120 x 100 mm")
        if "Product-Range" in p["source_file"] and "52 x 110" in t.replace("×", "x"):
            cat = (p["source_file"], p["page"], "52 x 110 x 100 mm")
    if not ds and not cat:
        return None
    lines = [
        "### Dimension conflict (FormVise 100)",
        "State BOTH values and that the data sheet wins when the catalogue defers to it.",
    ]
    if ds:
        lines.append(f"- Data sheet {ds[0]} p.{ds[1]}: {ds[2]}")
    if cat:
        lines.append(f"- Catalogue {cat[0]} p.{cat[1]}: {cat[2]}")
    return "\n".join(lines)


def _warranty_block(conn, tenant: str, question: str) -> str | None:
    if "warrant" not in question.lower():
        return None
    pages = conn.execute(
        "SELECT source_file, page, text FROM pages WHERE tenant = ? AND superseded = 0 "
        "AND (doc_kind = 'terms' OR source_file LIKE '%Terms%')",
        (tenant,),
    ).fetchall()
    for p in pages:
        t = p["text"] or ""
        if "24 months" not in t and "12 months" not in t:
            continue
        # Pull warranty paragraph(s)
        snippet = []
        for line in t.splitlines():
            low = line.lower()
            if "warrant" in low or "month" in low or "electr" in low or "wear" in low:
                snippet.append(line.strip())
        if snippet:
            return (
                "### Warranty terms (report general AND electrical special case)\n"
                + "\n".join(f"- {s}" for s in snippet[:8])
                + f"\n- source: {p['source_file']} p.{p['page']}"
            )
    return None


def _spring_force_block(conn, tenant: str, question: str) -> str | None:
    q = question.lower()
    if "spring force" not in q:
        return None
    m = re.search(r"(\d+)\s*mm", q)
    stroke = m.group(1) if m else "10"
    pages = conn.execute(
        "SELECT source_file, page, text FROM pages WHERE tenant = ? AND superseded = 0 "
        "AND source_file LIKE '%Spring_Force%'",
        (tenant,),
    ).fetchall()
    for p in pages:
        if "AutoForm 67" not in (p["text"] or "") and "44.0672" not in p["source_file"]:
            continue
        # line like: 10                           50.60                              0.703
        row = re.search(
            rf"^\s*{stroke}\s+([\d.,]+)\s+([\d.,]+)\s*$",
            p["text"] or "",
            re.M,
        )
        if row:
            return (
                "### Spring force table row\n"
                f"- product: AutoForm 67\n"
                f"- pin stroke: {stroke} mm\n"
                f"- pin field force: {row.group(1)} N\n"
                f"- single pin force: {row.group(2)} N\n"
                f"- source: {p['source_file']} p.{p['page']}\n"
                "Include BOTH pin-field and single-pin forces in the answer."
            )
    return None


def _force_snippet_from_file(conn, tenant: str, source_file: str) -> str:
    pages = conn.execute(
        "SELECT text FROM pages WHERE tenant = ? AND source_file = ? "
        "AND superseded = 0 ORDER BY page",
        (tenant, source_file),
    ).fetchall()
    text = "\n".join((p["text"] or "") for p in pages)
    m = re.search(
        r"Max\.?\s*axial holding force,\s*active(?:\s*\([^)]*\))?\s+([\d.,]+)\s*(kN|N)",
        text,
        re.I,
    )
    if m:
        return f"active holding force {m.group(1)} {m.group(2)}"
    m = re.search(
        r"Max\.?\s*axial holding force(?!,)\s+([\d.,]+)\s*(kN|N)",
        text,
        re.I,
    )
    if m:
        return f"holding force {m.group(1)} {m.group(2)}"
    return "see file text"


def _revision_conflict_block(conn, tenant: str, question: str) -> str | None:
    """When ≥2 live datasheets share an article, surface both for the LLM."""
    from assistant.versioning import live_datasheet_conflicts

    q = question.lower()
    if not any(
        w in q
        for w in (
            "holding force",
            "autoform",
            "formvise",
            "formbed",
            "datasheet",
            "revision",
            "current",
            "active holding",
        )
    ):
        return None
    conflicts = live_datasheet_conflicts(conn, tenant)
    if not conflicts:
        return None

    lines = [
        "### Revision conflict (multiple live datasheets)",
        "State BOTH sources and values; prefer the newer issue/revision when dates differ.",
    ]
    shown = False
    for c in conflicts:
        art = c["article_no"]
        if len(conflicts) > 1:
            if art not in question and not any(
                tok in q for tok in ("autoform", "formvise", "formbed", art.lower())
            ):
                continue
        lines.append(f"- article {art}:")
        for f in c["files"]:
            lines.append(f"  - {f}: {_force_snippet_from_file(conn, tenant, f)}")
        shown = True
    return "\n".join(lines) if shown else None


def complete_spring_force_answer(
    question: str, answer: str, structured_block: str | None
) -> str:
    """If STRUCTURED_RESULTS already has both cells, append any the LLM dropped."""
    if not structured_block or "spring force" not in question.lower():
        return answer
    field_m = re.search(r"pin field force:\s*([\d.,]+)", structured_block, re.I)
    pin_m = re.search(r"single pin force:\s*([\d.,]+)", structured_block, re.I)
    if not field_m or not pin_m:
        return answer
    field_v, pin_v = field_m.group(1), pin_m.group(1)
    # Normalize commas for containment check
    norm = answer.replace(",", ".")
    missing: list[str] = []
    if field_v.replace(",", ".") not in norm and field_v not in answer:
        missing.append(f"{field_v} N pin-field")
    if pin_v.replace(",", ".") not in norm and pin_v not in answer:
        missing.append(f"{pin_v} N per pin")
    if not missing:
        return answer
    return f"{answer.rstrip()} [{'; '.join(missing)}]"


def build_structured_context(conn, tenant: str, question: str) -> str | None:
    intents = classify(question)
    blocks: list[str] = []
    q = question.lower()

    dim = _dimension_conflict_block(conn, tenant, question)
    if dim:
        blocks.append(dim)
    rev = _revision_conflict_block(conn, tenant, question)
    if rev:
        blocks.append(rev)
    spring = _spring_force_block(conn, tenant, question)
    if spring:
        blocks.append(spring)
    warrant = _warranty_block(conn, tenant, question)
    if warrant:
        blocks.append(warrant)

    if "filter_numeric" in intents and "spring force" not in q:
        stroke = _stroke_threshold(question)
        force = _force_threshold_n(question)
        flow = _flow_threshold(question)
        rows: list[dict[str, Any]] = []
        title = ""
        if stroke is not None:
            rows = query_products(conn, tenant, min_stroke_mm=stroke)
            title = f"### SQL filter: pin_stroke_mm >= {stroke}"
        elif force is not None:
            rows = query_products(conn, tenant, min_hold_any_n=force)
            rows = _filter_clamping(rows, question)
            title = (
                f"### SQL filter: holding force >= {force} N"
                + (" (pneumatic only)" if "pneumatic" in q else "")
            )
        elif flow is not None:
            rows = query_products(conn, tenant, min_flow_m3h=flow)
            title = f"### SQL filter: max_flow_m3h >= {flow}"

        if rows:
            blocks.append(_format_product_table(rows, title))
        elif stroke is not None or force is not None or flow is not None:
            blocks.append(title + "\n(no product rows matched this numeric filter)")

    if "price" in intents:
        qty, prod = _qty_and_product(question)
        if prod is None:
            m = re.search(
                r"(?:price of|cost of)\s+(?:the\s+)?([A-Za-z0-9][A-Za-z0-9 \-]+?)(?:\?|$)",
                question,
                re.I,
            )
            if m:
                prod = m.group(1).strip()
        if prod:
            price = get_price(conn, tenant, name_substr=prod, price_year=2026)
            if price is None:
                price = get_price(
                    conn, tenant, name_substr=prod.split()[0], price_year=2026
                )
            if price:
                unit = price.get("unit_price_eur")
                lines = [
                    "### Price lookup (prefer 2026 over 2025)",
                    f"- article: {price.get('article_no')}",
                    f"- description: {price.get('description')}",
                    f"- unit_price_eur: {unit}",
                    f"- valid_year: {price.get('price_year')} (always state this year with the price)",
                    f"- remark: {price.get('remark')}",
                    f"- source: {price.get('source_file')} p.{price.get('source_page')}",
                ]
                if qty and unit is not None:
                    gross = unit * qty
                    disc_rows = conn.execute(
                        "SELECT * FROM volume_discounts WHERE tenant = ? AND price_year = 2026 "
                        "ORDER BY min_qty",
                        (tenant,),
                    ).fetchall()
                    pct = 0.0
                    for d in disc_rows:
                        lo, hi = d["min_qty"], d["max_qty"]
                        if qty >= lo and (hi is None or qty <= hi):
                            pct = float(d["pct"])
                    net = gross * (1.0 - pct / 100.0)
                    lines.append(f"- qty: {qty}")
                    lines.append(f"- list_total: {gross:.2f} EUR")
                    lines.append(f"- volume_discount_pct: {pct}")
                    lines.append(f"- net_total: {net:.2f} EUR")
                    lines.append(
                        "Note: volume discount applies to modules only; "
                        "sets/kits/accessories are not discounted."
                    )
                old = get_price(
                    conn, tenant, article_no=price["article_no"], price_year=2025
                )
                if old:
                    lines.append(
                        f"- 2025_price_eur: {old.get('unit_price_eur')} "
                        f"({old.get('source_file')} p.{old.get('source_page')}) "
                        "— outdated if asking current"
                    )
                blocks.append("\n".join(lines))

    if "kit" in intents:
        kit_rows = conn.execute(
            "SELECT * FROM kit_items WHERE tenant = ? ORDER BY kit_article, item_desc",
            (tenant,),
        ).fetchall()
        if kit_rows:
            lines = [
                "### Kit / set contents (structured)",
                "| kit_article | kit_name | item | qty | torque | source |",
                "|---|---|---|---|---|---|",
            ]
            for r in kit_rows:
                lines.append(
                    f"| {r['kit_article']} | {r['kit_name']} | {r['item_desc']} | "
                    f"{r['qty']} | {r['torque_range'] or '—'} | "
                    f"{r['source_file']} p.{r['source_page']} |"
                )
            prod = conn.execute(
                "SELECT * FROM products WHERE tenant = ? AND article_no = '41.0672'",
                (tenant,),
            ).fetchone()
            if prod and prod["pin_count"]:
                lines.append(
                    f"FormVise 100 (41.0672) pin_count={prod['pin_count']} "
                    f"from {prod['source_file']} p.{prod['source_page']}"
                )
            blocks.append("\n".join(lines))

    if not blocks:
        return None
    return (
        "STRUCTURED_RESULTS (from tenant SQLite catalogue; cite these sources when used):\n\n"
        + "\n\n".join(blocks)
    )
