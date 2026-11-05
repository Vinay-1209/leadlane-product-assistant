from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from assistant.ask import ask
from assistant.db import ROOT
from assistant.judges import judge
from assistant.llm import model_name


@dataclass
class EvalRow:
    id: str
    category: str
    passed: bool
    reason: str
    latency_s: float
    cost_usd: float
    answer: str


def load_questions(
    path: Path | None = None, *, phase: str | None = "initial"
) -> list[dict]:
    qpath = path or (ROOT / "eval" / "questions_public.json")
    data = json.loads(qpath.read_text(encoding="utf-8"))
    if phase is None:
        return data
    return [q for q in data if q.get("phase") == phase]


def run_eval(
    *,
    phase: str | None = "initial",
    question_ids: list[str] | None = None,
) -> list[EvalRow]:
    # --only by id should see all phases unless the user also pinned a phase.
    if question_ids and phase == "initial":
        questions = load_questions(phase=None)
    else:
        questions = load_questions(phase=phase)
    if question_ids:
        want = set(question_ids)
        questions = [q for q in questions if q["id"] in want]

    rows: list[EvalRow] = []
    for q in questions:
        ans = ask(q["tenant"], q["question"])
        ok, reason = judge(q["id"], ans)
        rows.append(
            EvalRow(
                id=q["id"],
                category=q.get("category", ""),
                passed=ok,
                reason=reason,
                latency_s=ans.latency_s,
                cost_usd=ans.cost_usd,
                answer=ans.answer,
            )
        )
    return rows


def print_report(rows: list[EvalRow]) -> None:
    if not rows:
        print("No questions evaluated.")
        return

    print(f"Model: {model_name()}")
    print()
    print(f"{'id':<6} {'cat':<16} {'pass':<6} {'lat_s':>7} {'cost$':>8}  reason")
    print("-" * 88)
    for r in rows:
        mark = "OK" if r.passed else "FAIL"
        print(
            f"{r.id:<6} {r.category:<16} {mark:<6} {r.latency_s:7.2f} "
            f"{r.cost_usd:8.4f}  {r.reason}"
        )
        if not r.passed:
            preview = r.answer.replace("\n", " ")[:120]
            print(f"       answer: {preview}")

    n = len(rows)
    n_ok = sum(1 for r in rows if r.passed)
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        by_cat[r.category].append(r.passed)

    print()
    print(f"Accuracy overall: {n_ok}/{n} = {100.0 * n_ok / n:.1f}%")
    print("Accuracy by category:")
    for cat in sorted(by_cat):
        vals = by_cat[cat]
        c_ok = sum(vals)
        print(f"  {cat:<16} {c_ok}/{len(vals)} = {100.0 * c_ok / len(vals):.1f}%")

    avg_lat = sum(r.latency_s for r in rows) / n
    avg_cost = sum(r.cost_usd for r in rows) / n
    print(f"Avg latency/question: {avg_lat:.2f}s")
    print(f"Avg cost/question:    ${avg_cost:.4f}")
    print(f"Total cost:           ${sum(r.cost_usd for r in rows):.4f}")
