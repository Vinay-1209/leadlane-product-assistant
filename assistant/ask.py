from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assistant.config import context_mode, max_context_tokens
from assistant.db import connect, get_pages
from assistant.ingest import TENANTS
from assistant.llm import LLMResult, chat_json
from assistant.prompts import SYSTEM_PROMPT, user_prompt
from assistant.retrieve import select_pages
from assistant.safety import filter_sources, is_meta_probe, sanitize_answer
from assistant.structured import build_structured_context, complete_spring_force_answer


@dataclass
class Source:
    file: str
    page: int


@dataclass
class Answer:
    answer: str
    sources: list[Source] = field(default_factory=list)
    abstained: bool = False
    latency_s: float = 0.0  # wall total (struct + retrieve + llm + post)
    t_structured_s: float = 0.0
    t_retrieve_s: float = 0.0
    t_llm_s: float = 0.0
    t_post_s: float = 0.0
    cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    mode: str = ""
    pages_used: int = 0
    structured: bool = False
    raw: dict[str, Any] | None = None


def _normalize_sources(raw_sources: Any) -> list[Source]:
    out: list[Source] = []
    if not isinstance(raw_sources, list):
        return out
    for s in raw_sources:
        if not isinstance(s, dict):
            continue
        file = s.get("file") or s.get("source_file") or s.get("filename")
        page = s.get("page")
        if file is None or page is None:
            continue
        try:
            page_i = int(page)
        except (TypeError, ValueError):
            continue
        out.append(Source(file=str(file), page=page_i))
    return out


def format_timing(ans: Answer) -> str:
    """Human-readable stage breakdown (local stages in ms, LLM in s)."""
    return (
        f"struct={ans.t_structured_s * 1000:.0f}ms "
        f"retrieve={ans.t_retrieve_s * 1000:.0f}ms "
        f"llm={ans.t_llm_s:.2f}s "
        f"post={ans.t_post_s * 1000:.0f}ms "
        f"total={ans.latency_s:.2f}s"
    )


def ask(
    tenant: str,
    question: str,
    *,
    db_path: Path | None = None,
) -> Answer:
    if tenant not in TENANTS:
        raise ValueError(f"Unknown tenant {tenant!r}; expected one of {TENANTS}")

    mode = context_mode()

    # Policy / recon probes: do not call the LLM or dump corpus metadata.
    if is_meta_probe(question):
        return Answer(
            answer="NOT_IN_DOCUMENTS",
            abstained=True,
            sources=[],
            mode=mode,
            pages_used=0,
            structured=False,
        )

    t_structured = t_retrieve = 0.0
    structured_block = None
    pages: list[dict[str, Any]] = []

    conn = connect(db_path)
    try:
        if mode == "full":
            t0 = time.perf_counter()
            pages = get_pages(conn, tenant)
            t_retrieve = time.perf_counter() - t0
        else:
            t0 = time.perf_counter()
            structured_block = build_structured_context(conn, tenant, question)
            t_structured = time.perf_counter() - t0

            t0 = time.perf_counter()
            pages = select_pages(
                conn, tenant, question, budget_tokens=max_context_tokens()
            )
            t_retrieve = time.perf_counter() - t0
    finally:
        conn.close()

    if not pages and not structured_block:
        total = t_structured + t_retrieve
        return Answer(
            answer="NOT_IN_DOCUMENTS",
            abstained=True,
            sources=[],
            mode=mode,
            pages_used=0,
            structured=False,
            t_structured_s=t_structured,
            t_retrieve_s=t_retrieve,
            latency_s=total,
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": user_prompt(question, pages, structured_block),
        },
    ]

    t0 = time.perf_counter()
    result: LLMResult = chat_json(messages)
    t_llm = time.perf_counter() - t0

    t0 = time.perf_counter()
    parsed = result.parsed or {}
    answer_text = str(parsed.get("answer") or result.content or "").strip()
    answer_text = complete_spring_force_answer(
        question, answer_text, structured_block
    )
    abstained = bool(parsed.get("abstained"))
    if "NOT_IN_DOCUMENTS" in answer_text.upper().replace(" ", "_"):
        abstained = True
    sources = filter_sources(
        _normalize_sources(parsed.get("sources")),
        pages,
        structured_block,
    )
    answer_text, forced = sanitize_answer(answer_text)
    if forced:
        abstained = True
        sources = []
    t_post = time.perf_counter() - t0

    total = t_structured + t_retrieve + t_llm + t_post
    return Answer(
        answer=answer_text,
        sources=sources,
        abstained=abstained,
        latency_s=total,
        t_structured_s=t_structured,
        t_retrieve_s=t_retrieve,
        t_llm_s=t_llm,
        t_post_s=t_post,
        cost_usd=result.cost_usd,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        model=result.model,
        mode=mode,
        pages_used=len(pages),
        structured=bool(structured_block),
        raw=parsed or None,
    )


def format_answer(ans: Answer) -> str:
    lines = [ans.answer, ""]
    if ans.sources:
        lines.append("Sources:")
        for s in ans.sources:
            lines.append(f"  - {s.file} (page {s.page})")
    else:
        lines.append("Sources: (none)")
    return "\n".join(lines)
