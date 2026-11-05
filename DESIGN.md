# Production design note (Part B)

Assume next quarter: **500 tenants**, up to **2,000 documents** each (~1M pages worst case), **40 SDRs × ~30 questions/day** (~1,200 Q/day), answers **&lt; 3 s** on a live call. No code — decisions only.

## Architecture at that scale

| Layer | Choice | Why |
|-------|--------|-----|
| Isolation | Tenant id on every row + `WHERE tenant = ?` only via `get_pages` / catalogue queries. App never loads “all tenants.” Postgres RLS later as defense in depth. | Leakage is a hard fail for multi-tenant SaaS; query-scoped reads are simpler and testable than trusting the LLM or app filters alone. |
| Storage | Take-home: SQLite (`pages`, `documents`, catalogue). Prod: **Postgres** + object store for PDFs; partition/shard by tenant when needed. | SQLite keeps the take-home ≤5 commands. Postgres + object store handles concurrent tenants, backups, and large binary PDFs without bloating the DB. |
| Structured catalogue | Product / price / kit tables. Numeric filters, prices, kits from SQL; prose from retrieval. | Specs and prices must be exact; stuffing whole PDFs for “≥ 30 mm” is brittle and burns tokens. |
| Document versioning | Registry per `source_file` (`article_no`, `doc_type`, `issue_date`). Competing **datasheets**: date → filename `rev` → conflict. Mark `superseded`; do not delete. | SDRs need the *current* figure (e.g. 4.8 kN vs 4.2). Hashes only detect change; identity + dates decide which wins. Keep old pages for audit. |
| Retrieval | Today: BM25 + doc_kind boosts under a token budget. At 2k docs/tenant: per-tenant FTS. Embeddings only when lexical+structured miss rate plateaus. | Corpus is product PDFs with shared jargon; BM25 + structured rows hit eval without embedding ops cost. FTS/embeddings scale when miss rate says so. |
| Ingestion | Async workers: extract/OCR → upsert pages + documents → resolve versions → rebuild catalogue. Idempotent on `(tenant, source_file, page)`. | Ask path stays &lt;3 s; OCR and version resolve are heavy and belong offline. Idempotency makes re-ingest / Part B updates safe. |
| Serving | Stateless API in front of `ask(tenant, q)`. Auth binds the SDR session to one tenant. | Horizontal scale and one enforcement point for tenant binding before any retrieval. |

Isolation is enforced in storage queries first; the LLM never sees another tenant’s pages.

## Versioning (identity, not hashes)

Official “newer” is **not** “different content hash.” Hashes only detect change.

Ladder used in this take-home (and recommended in prod with richer metadata):

1. Parsed issue/revision **date** from body or `_revYYYY-MM` filename  
2. Else exactly one competing datasheet whose filename contains `rev`  
3. Else **conflict**: both stay live; answer must report both (structured conflict block + prompt rules)  
4. Production add-on: upload CMS fields `replaces` / `effective_date` as strongest signal; admin override when parsers disagree  

Spring-force / price-list / flyers are separate `doc_type`s and do not auto-kill a datasheet.

## Hitting &lt; 3 seconds

Measured hybrid + Groq `gpt-oss-120b`, `MAX_CONTEXT_TOKENS=6500`, paced 1-by-1: **32/32**, avg **~14.2 s/Q** wall (almost all `llm`; `struct`/`retrieve` ≈ tens of ms).

That ~14 s is free-tier rate limits / 429 retries, not SQLite or retrieval; with paid quota (same hybrid + small context) plan **~2–3 s/Q**, and **&lt;1 s** for pure numeric filters if answered from SQL without an LLM round-trip.

Budget for production:

1. **Retrieve + SQL** &lt; 100–200 ms (indexed).  
2. **LLM** &lt; 2–2.5 s (paid TPM, no retry storms).  
3. **OCR** never on the ask path — only at ingest.

Levers: hybrid packing, optional structured short-circuit for pure filters, response cache, dedicated LLM quota. Do **not** OCR or re-parse PDFs per question.

## Monthly cost (order of magnitude)

Assumptions from our eval: **~$0.0056 / question** hybrid on `gpt-oss-120b`; ~1,200 Q/day → ~36k Q/month → **~$200/month LLM**. Stronger models / richer context: budget **~$500–2,000/month**.

Embeddings (if added): mostly ingest-time; usually less than chat spend. OCR is ingest CPU, not per-ask.

## Monitoring / quality regressions

- **Online:** latency p50/p95 (split retrieve vs LLM), error/429 rate, abstain rate, $/Q, tokens/Q, isolation denials, revision-conflict rate.  
- **Offline:** public + private eval after prompt/retriever/catalogue/versioning changes.  
- **Drift:** abstain spikes, missing sources, canary judge fails, SDR thumbs-down.  
- **Safety:** injection / meta-probe canaries in CI.

## What breaks first / what not to build in v1

**Breaks first:** LLM TPM and stuffing the whole tenant in the prompt; unscoped search bypassing `get_pages`; wrong supersede leaving stale 4.2 kN as “current.”

**Deliberately not in v1:** multi-tenant vector DB as the core; agent tool loops; fine-tuned model; real-time OCR on ask; hash-as-version; cross-tenant doc analytics.

**Build first:** tenant-scoped storage + tests, hybrid retrieve + structured filters, identity-based superseding ingest, cheap eval harness, honest abstain/conflict behaviour.
