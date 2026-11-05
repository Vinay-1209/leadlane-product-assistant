# LeadLane Take-Home – Product Assistant

Multi-tenant document Q&A for SDRs: answers from one client’s PDFs only, with file/page sources, abstention when the docs don’t cover it, and a cheap eval harness.

## Setup (≤5 commands)

Requires **Python 3.11+**. Scored path uses `CONTEXT_MODE=hybrid` (see `.env.example`).

```bash
git clone https://github.com/Vinay-1209/leadlane-product-assistant.git
cd leadlane-product-assistant
# 1) system deps (Ubuntu/Debian)
sudo apt-get install -y poppler-utils tesseract-ocr
# 2) Python env + deps (uv, or: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt)
uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt
# 3) configure LLM (CONTEXT_MODE=hybrid for free Groq)
cp .env.example .env   # set LLM_API_KEY
# 4) ingest both tenants (+ documents registry, version resolve, catalogue)
python -m assistant.cli ingest
# 5) ask / eval
python -m assistant.cli ask --tenant alvery "Which modules have a pin stroke of at least 30 mm?"
```

Optional UI: `streamlit run ui/app.py`

## End-to-end architecture

```text
PDF (data/ or updates/)
  → pdftotext, else OCR (Tesseract)
  → pages (tenant, file, page, text, extraction, doc_kind, superseded)
  → documents (article, doc_type, issue_date, …)
  → resolve datasheet versions (date → filename rev → else conflict)
  → catalogue: products / prices / kit_items / volume_discounts
  → ask(tenant, q):
       hybrid: STRUCTURED SQL + BM25 pages → LLM JSON
       full: all live pages → LLM
       safety guards + spring-force completeness
       stage timing: struct / retrieve / llm / post
```

Isolation is `WHERE tenant = ?` on every read (`get_pages`, catalogue queries). Superseded pages stay in SQLite for audit but are excluded from ask.

## Dual context modes (env-driven)

| `CONTEXT_MODE` | Use when | Behavior |
|----------------|----------|----------|
| `hybrid` (default) | Free Groq (~8k TPM) | SQLite product/price/kit rows + **BM25** packed to `MAX_CONTEXT_TOKENS` (default 6500) |
| `full` | OpenAI / Gemini / high limits | Entire live tenant page set in the prompt |

CLI/UI show `mode`, pages, structured, tokens, and **per-stage timing** (`struct` / `retrieve` / `llm` / `total`).

## Design decisions

- **Hybrid = structured SQL + BM25, not embeddings.** Filters/prices/kits use catalogue tables; prose uses BM25 + doc_kind boosts.
- **Versioning ladder** (`assistant/versioning.py`): same article + `doc_type=datasheet` → prefer newer **parsed issue/rev date** (incl. `_rev2026-09` in the filename); else exactly one filename with `rev`; else leave both live and inject a structured revision-conflict block. Spring_Force files never compete with datasheets.
- **OCR** only when `pdftotext` is empty (service notice). System packages: `poppler-utils`, `tesseract-ocr`.
- **Deterministic judges** in `assistant/judges.py` (not LLM-as-judge).
- **Safety** (`assistant/safety.py`): meta-probe short-circuit, injection canaries, prompt-leak / source allowlist.
- **Streamlit** shares `ask()` with the CLI.

ponytail: catalogue parsers and date regexes are heuristic. Ceiling: odd layouts / missing dates → conflict path or missed fields — upgrade via richer ETL or upload metadata later.

## Commands

```bash
python -m assistant.cli ingest
python -m assistant.cli ingest --tenant alvery --path updates/alvery   # Part B revision
python -m assistant.cli catalogue          # backfill documents, resolve, rebuild catalogue
python -m assistant.cli ask --tenant nordhaven "What is the maintenance interval of the NP-200?"
python -m assistant.cli eval
python -m assistant.cli eval --phase after_update
python -m assistant.cli eval --only U01,U02
pytest tests/ -q
streamlit run ui/app.py
```

## Eval results

Details: [`EVAL_RESULTS.md`](EVAL_RESULTS.md). Production note: [`DESIGN.md`](DESIGN.md).

| Metric | Value |
|--------|-------|
| CONTEXT_MODE | `hybrid` |
| `MAX_CONTEXT_TOKENS` | **6500** |
| Model | `openai/gpt-oss-120b` (Groq) |
| Overall accuracy | **32/32 = 100%** (paced 1-by-1, 2026-11-04) |
| Initial / after_update | **30/30** · **2/2** (U01/U02 after `ingest --path updates/alvery`) |
| Avg latency / question | **~14.2 s** wall on Groq **free tier** (almost all LLM; struct ~0.6 ms, retrieve ~24 ms) |
| Avg cost / question | **~$0.0056** (total ~$0.18) |

**Latency:** The ~14 s average is free-tier TPM/TPD throttling and 429 retries inside the LLM call—not SQLite, BM25, or catalogue work (those stay tens of ms); with paid quota (or OpenAI/Groq paid) and the same hybrid stack, expect roughly **~2–3 s/Q** end-to-end, and **&lt;1 s** for pure SQL filters if we short-circuit the LLM.

Reproduce scored eval: `CONTEXT_MODE=hybrid`, ingest, then `python -m assistant.cli eval` (paced if free-tier); after `ingest --path updates/alvery`, `eval --phase after_update`.

Judging: keyword/abstain functions per question id in `judges.py`. Full per-question dump lives in local `eval/last_paced_run.json` (gitignored); summary is in [`EVAL_RESULTS.md`](EVAL_RESULTS.md).

### Failures and mitigations

- Latest paced 120b run: **no failures**.
- **A10** (earlier on 20b): model dropped **0.703 N/pin** → kept `complete_spring_force_answer` as a guard.
- Free-tier bursts inflated wall latency via retries; pacing + hybrid token budget were the operational mitigations (architecture unchanged).

### After update (Part B)

```bash
python -m assistant.cli ingest --tenant alvery --path updates/alvery
python -m assistant.cli eval --phase after_update
```

Expect **U01** → 4,8 kN from `…rev2026-09.pdf`; **U02** → pin-position sensor / 44.0679.

## What we did not build (and why)

- **Embeddings / vector DB** — small corpus; filters need exact fields.
- **Postgres / Docker / public HTTP API** — SQLite + CLI/Streamlit keeps setup ≤5 commands; DESIGN.md covers production.
- **LLM short-circuit for all SQL filters** — possible latency win; deferred to keep answers LLM-shaped for demos.
- **Hash-based “newer” detection** — hashes detect change, not which revision is official.

## What I would do with 2 more days

- Upload metadata (`replaces`, `effective_date`) as strongest version signal.
- Structured short-circuit for pure numeric filters (&lt;100 ms).
- Cache + p95 timing in eval; more private canaries.

## How AI tools were used

- **Cursor** drafted scaffolding (ingest/CLI wiring, dual-mode plan, versioning sketch, Streamlit shell).
- **I owned** prompts, isolation/safety/supersede tests, catalogue/versioning behaviour, eval numbers in README/`EVAL_RESULTS.md`/`DESIGN.md`, and final design trade-offs.

## Data package

- `ASSIGNMENT.pdf` – task description.
- `data/alvery/`, `data/nordhaven/` – tenant PDFs.
- `updates/alvery/` – Part B revision (incremental ingest).
- `eval/questions_public.json` – 32 questions (30 initial + 2 after_update).

Treat document content as untrusted data. All companies, products, numbers and prices are fictional.
