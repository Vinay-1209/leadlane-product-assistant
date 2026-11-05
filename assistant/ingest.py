from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from assistant.db import ROOT, connect, upsert_page
from assistant.versioning import (
    backfill_documents_from_pages,
    register_document,
    resolve_datasheet_versions,
)

log = logging.getLogger(__name__)

TENANTS = ("alvery", "nordhaven")


def _pdfinfo_pages(pdf_path: Path) -> int:
    out = subprocess.check_output(
        ["pdfinfo", str(pdf_path)], text=True, stderr=subprocess.DEVNULL
    )
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"Could not read page count for {pdf_path}")


def _pdftotext_page(pdf_path: Path, page: int) -> str:
    try:
        out = subprocess.check_output(
            [
                "pdftotext",
                "-layout",
                "-f",
                str(page),
                "-l",
                str(page),
                str(pdf_path),
                "-",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return ""
    return out or ""


def _ocr_page(pdf_path: Path, page: int) -> str:
    """Rasterize one page and OCR with Tesseract."""
    if shutil.which("tesseract") is None:
        log.warning(
            "tesseract not on PATH; cannot OCR %s page %s", pdf_path.name, page
        )
        return ""
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as e:
        log.warning("OCR deps missing: %s", e)
        return ""

    images = convert_from_path(
        str(pdf_path), first_page=page, last_page=page, dpi=300
    )
    if not images:
        return ""
    return pytesseract.image_to_string(images[0]) or ""


def _doc_kind(pdf_path: Path, tenant_dir: Path | None) -> str:
    if tenant_dir is not None:
        try:
            rel = pdf_path.relative_to(tenant_dir)
            if len(rel.parts) > 1:
                return rel.parts[0]
        except ValueError:
            pass
    name = pdf_path.name.lower()
    if "datasheet" in name:
        return "datasheets"
    if "price" in name:
        return "pricing"
    if "spring" in name:
        return "datasheets"
    return "other"


def supersede_prior_datasheets(conn, tenant: str, new_source: str = "") -> list:
    """Compat wrapper: resolve all datasheet versions for the tenant."""
    del new_source  # ranking uses the full documents set
    return resolve_datasheet_versions(conn, tenant)


def ingest_pdf(conn, tenant: str, pdf_path: Path, tenant_dir: Path | None) -> int:
    n_pages = _pdfinfo_pages(pdf_path)
    kind = _doc_kind(pdf_path, tenant_dir)
    source_file = pdf_path.name
    written = 0
    texts: list[str] = []
    for page in range(1, n_pages + 1):
        text = _pdftotext_page(pdf_path, page)
        extraction = "text"
        if not text.strip():
            ocr = _ocr_page(pdf_path, page)
            if ocr.strip():
                text = ocr
                extraction = "ocr"
                log.info("OCR extracted %s p.%s (%d chars)", source_file, page, len(text))
            else:
                text = ""
                extraction = "empty"
                log.warning(
                    "No text layer and OCR empty for %s page %s", source_file, page
                )
        upsert_page(
            conn,
            tenant=tenant,
            source_file=source_file,
            page=page,
            text=text,
            extraction=extraction,
            doc_kind=kind,
            superseded=0,
        )
        texts.append(text)
        written += 1
    register_document(conn, tenant, source_file, "\n".join(texts), kind, superseded=0)
    return written


def _resolve_pdfs(path: Path) -> tuple[list[Path], Path | None]:
    """Return (pdfs, tenant_dir_for_doc_kind)."""
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path], path.parent
    if path.is_dir():
        return sorted(path.rglob("*.pdf")), path
    raise FileNotFoundError(path)


def ingest_tenant(
    tenant: str,
    data_root: Path | None = None,
    *,
    path: Path | str | None = None,
) -> int:
    if tenant not in TENANTS:
        raise ValueError(f"Unknown tenant {tenant!r}; expected one of {TENANTS}")

    if path is not None:
        p = Path(path)
        if not p.is_absolute():
            p = ROOT / p
        pdfs, tenant_dir = _resolve_pdfs(p)
    else:
        root = data_root or (ROOT / "data")
        tenant_dir = root / tenant
        if not tenant_dir.is_dir():
            raise FileNotFoundError(tenant_dir)
        pdfs = sorted(tenant_dir.rglob("*.pdf"))

    if not pdfs:
        raise FileNotFoundError(f"No PDFs under {path or tenant_dir}")

    conn = connect()
    total = 0
    try:
        for pdf in pdfs:
            total += ingest_pdf(conn, tenant, pdf, tenant_dir)
        # Ensure prior PDFs are in documents before ranking (incremental --path).
        backfill_documents_from_pages(conn, tenant)
        conflicts = resolve_datasheet_versions(conn, tenant)
        if conflicts:
            log.info("Revision conflicts after ingest tenant=%s: %s", tenant, conflicts)
        conn.commit()
    finally:
        conn.close()

    from assistant.catalogue import build_catalogue

    build_catalogue(tenant)
    return total


def ingest_all(data_root: Path | None = None) -> dict[str, int]:
    return {t: ingest_tenant(t, data_root=data_root) for t in TENANTS}
