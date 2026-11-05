from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.ask import ask, format_timing  # noqa: E402
from assistant.config import context_mode, max_context_tokens  # noqa: E402
from assistant.ingest import TENANTS  # noqa: E402
from assistant.llm import model_name  # noqa: E402

st.set_page_config(page_title="LeadLane Assistant", layout="centered")
st.title("LeadLane product assistant")
st.caption(
    f"mode={context_mode()} · model={model_name()} · "
    f"max_tokens={max_context_tokens()} · tenant-isolated answers with sources"
)

tenant = st.selectbox("Tenant", options=list(TENANTS), index=0)
question = st.text_area(
    "Question",
    height=100,
    placeholder="Which modules have a pin stroke of at least 30 mm?",
)

if st.button("Ask", type="primary") and question.strip():
    with st.spinner("Asking…"):
        ans = ask(tenant, question.strip())
    st.subheader("Answer")
    st.write(ans.answer)
    if ans.abstained:
        st.info("Abstained: not in this tenant's documents.")
    st.subheader("Sources")
    if ans.sources:
        st.table([{"file": s.file, "page": s.page} for s in ans.sources])
    else:
        st.write("(none)")
    st.caption(
        f"mode={ans.mode} · pages={ans.pages_used} · structured={ans.structured} · "
        f"model={ans.model} · tokens={ans.prompt_tokens}+{ans.completion_tokens} · "
        f"≈${ans.cost_usd:.4f}"
    )
    st.caption(format_timing(ans))
    if ans.t_llm_s > 5 and ans.t_llm_s > 0.9 * ans.latency_s:
        st.warning(
            "Almost all time is in the LLM call. If logs show HTTP 429, "
            "latency is mostly provider retry wait — not retrieval."
        )
