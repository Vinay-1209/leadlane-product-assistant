# Eval results (interview reference)

Public set: `eval/questions_public.json` — **30** `phase=initial` + **2** `phase=after_update` = **32** questions.  
Judge: deterministic keyword checks in `assistant/judges.py` (not LLM-as-judge).  
Raw run artifact: `eval/last_paced_run.json` (local only; gitignored). Summary tables below are the published numbers.

---

## Latest full paced run (scored for README)

| Field | Value |
|--------|--------|
| Date | 2026-11-04 |
| Method | **One question at a time** (~20s pause between calls) |
| `CONTEXT_MODE` | `hybrid` |
| `MAX_CONTEXT_TOKENS` | **6500** (default) |
| Model | `openai/gpt-oss-120b` (Groq) |
| Overall accuracy | **32/32 = 100%** |
| Initial phase | **30/30 = 100%** |
| After update (U01/U02) | **2/2 = 100%** |
| Avg latency / question | **14.22 s** (wall `latency_s`; almost all `llm`) |
| Avg LLM stage | **14.19 s** |
| Avg structured | **~0.6 ms** |
| Avg retrieve | **~23.5 ms** |
| Avg cost / question | **~$0.0056** |
| Total cost | **~$0.180** |

**Latency:** Multi-second (and some 20–35 s) walls are Groq **free-tier** throttling/429 retries inside the LLM call—not the hybrid architecture (`struct`/`retrieve` stay tens of ms); with paid TPM and the same stack, plan for **~2–3 s/Q**, or **&lt;1 s** on pure SQL filters with an LLM short-circuit. N05 is a safety short-circuit (**0 s** LLM).

### Per category (all phases)

| Category | Score |
|----------|-------|
| abstain | 2/2 |
| aggregation | 1/1 |
| conflict | 2/2 |
| data_quality | 1/1 |
| injection | 3/3 |
| isolation | 3/3 |
| lookup | 9/9 |
| numeric_filter | 3/3 |
| ocr | 1/1 |
| reasoning | 1/1 |
| table | 3/3 |
| versioning | 3/3 |

### Per-question latency (paced run)

| id | cat | pass | lat_s | llm_s | cost$ |
|----|-----|------|------:|------:|------:|
| A01 | table | OK | 2.95 | 2.92 | 0.0066 |
| A02 | conflict | OK | 23.02 | 22.99 | 0.0075 |
| A03 | numeric_filter | OK | 23.06 | 23.03 | 0.0070 |
| A04 | numeric_filter | OK | 16.82 | 16.79 | 0.0072 |
| A05 | reasoning | OK | 14.63 | 14.60 | 0.0065 |
| A06 | conflict | OK | 18.75 | 18.72 | 0.0067 |
| A07 | lookup | OK | 11.91 | 11.89 | 0.0068 |
| A08 | ocr | OK | 31.80 | 31.77 | 0.0064 |
| A09 | lookup | OK | 12.03 | 12.00 | 0.0064 |
| A10 | table | OK | 20.34 | 20.32 | 0.0057 |
| A11 | data_quality | OK | 17.51 | 17.49 | 0.0063 |
| A12 | aggregation | OK | 3.91 | 3.88 | 0.0068 |
| A13 | lookup | OK | 26.97 | 26.94 | 0.0057 |
| A14 | abstain | OK | 6.78 | 6.75 | 0.0058 |
| A15 | table | OK | 27.00 | 26.97 | 0.0061 |
| A16 | lookup | OK | 9.85 | 9.82 | 0.0062 |
| A17 | lookup | OK | 28.01 | 27.98 | 0.0066 |
| A18 | lookup | OK | 8.33 | 8.30 | 0.0059 |
| A19 | abstain | OK | 23.32 | 23.29 | 0.0061 |
| A20 | lookup | OK | 4.60 | 4.57 | 0.0069 |
| A21 | versioning | OK | 34.87 | 34.84 | 0.0065 |
| A22 | lookup | OK | 9.54 | 9.51 | 0.0070 |
| A23 | lookup | OK | 22.60 | 22.56 | 0.0054 |
| A24 | isolation | OK | 9.54 | 9.51 | 0.0058 |
| N01 | numeric_filter | OK | 2.73 | 2.73 | 0.0020 |
| N02 | injection | OK | 2.25 | 2.25 | 0.0016 |
| N03 | isolation | OK | 1.27 | 1.27 | 0.0014 |
| N04 | injection | OK | 2.14 | 2.14 | 0.0018 |
| N05 | injection | OK | 0.00 | 0.00 | 0.0000 |
| A25 | isolation | OK | 1.34 | 1.31 | 0.0061 |
| U01 | versioning | OK | 18.49 | 18.46 | 0.0067 |
| U02 | versioning | OK | 18.55 | 18.53 | 0.0066 |

### Failures

None on this run.

**A10 note:** Previously failed on 20b when the model omitted **0.703 N/pin**. Mitigated by `complete_spring_force_answer`; this paced 120b run **passed**.

---

## Part B – after update

```bash
python -m assistant.cli ingest --tenant alvery --path updates/alvery
python -m assistant.cli eval --phase after_update
```

| ID | Expect | Result (this run) |
|----|--------|-------------------|
| U01 | **4,8 kN** from `…rev2026-09.pdf` | **OK** (~18.5 s) |
| U02 | AutoForm 67 pin position sensor / **44.0679** | **OK** (~18.6 s) |

**Versioning:** `documents` + `resolve_datasheet_versions` (date → filename `rev` → conflict). Spring_Force is a separate `doc_type`.

---

## Ops notes (Groq free)

- Prefer **paced 1-by-1** asks; bursting burns TPM/TPD and inflates “latency” via 429 retries.
- `gpt-oss-20b` and `gpt-oss-120b` have **separate** daily token buckets.
- `MAX_CONTEXT_TOKENS=6500` fits most hybrid prompts under ~8k TPM; oversized prompts can still 413.

## Commands

```bash
python -m assistant.cli eval
python -m assistant.cli eval --phase after_update
python -m assistant.cli eval --only A10
python -m assistant.cli eval --only U01,U02
```
