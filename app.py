"""Onboarding Buddy: chat with any GitHub repo's docs and issues, powered by Snowflake Cortex."""
import json
import re

import streamlit as st

import github_fetch as gh
import snow

st.set_page_config(page_title="Onboarding Buddy", page_icon="🧭", layout="wide")


@st.cache_resource(show_spinner="Connecting to Snowflake…")
def get_conn():
    return snow.connect()


def retrieve(conn, repo, query, limit=8):
    try:
        hits = snow.search(conn, repo, query, limit)
        if hits:
            return hits, "Cortex Search"
    except Exception:  # index still building or service missing
        pass
    return snow.keyword_search(conn, repo, query, limit), "keyword fallback"


def build_checklist(conn, repo, info):
    hits, _ = retrieve(conn, repo, "install setup development environment run tests contributing pull request", 6)
    context = "\n\n".join(h["CONTENT"] for h in hits)
    prompt = (
        f"From the documentation of the GitHub repo {repo} below, write a getting-started checklist for a new "
        "contributor: 5 to 7 short imperative steps (max 12 words each), in order, from cloning to opening a "
        "first pull request. Output ONLY a JSON array of strings.\n\n" + context
    )
    raw = snow.complete(conn, prompt)
    m = re.search(r"\[.*\]", raw, re.S)
    try:
        steps = json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        steps = []
    if not steps:
        steps = [re.sub(r"^\s*[-*\d.)]+\s*", "", l) for l in raw.splitlines() if l.strip()]
    return [str(s) for s in steps][:7]


def build_prompt(repo, info, question, hits, first_issues, history):
    sources = "\n\n".join(f"[{i}] ({h['SOURCE_PATH']})\n{h['CONTENT']}" for i, h in enumerate(hits, 1))
    issues = "\n".join(
        f"- #{g['number']} {g['title']} (label: {g['label']}{', already assigned' if g['assigned'] else ''}) {g['url']}"
        for g in first_issues
    ) or "None found."
    convo = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in history[-4:])
    return f"""You are Onboarding Buddy, a friendly guide helping a new contributor get started with the GitHub repository {repo} ({info['description']}).

Rules:
- Answer using ONLY the numbered sources and the good-first-issues list below.
- Cite sources inline like [1] or [2][3] right after the facts they support.
- If the sources don't contain the answer, say so plainly and name the most relevant file to read.
- Be concise: short numbered steps, and put shell commands in code blocks.
- When asked what to work on, recommend 1-3 unassigned issues from the list, with links, and say why each suits a beginner.

Open good first issues:
{issues}

Sources:
{sources}

Recent conversation:
{convo}

Question: {question}
Answer:"""


def load(repo_input):
    conn = get_conn()
    repo = gh.parse_repo(repo_input)
    with st.status(f"Loading {repo}…", expanded=True) as status:
        st.write("Fetching README, docs, contributing guide and closed issues from GitHub…")
        info, rows = gh.fetch_repo(repo)
        docs = sum(r["source_type"] == "doc" for r in rows)
        st.write(f"Found {docs} doc chunks and {len(rows) - docs} issue chunks.")
        st.write("Storing chunks in Snowflake…")
        snow.load_repo(conn, repo, rows)
        st.write("Indexing with Cortex Search (up to ~2 min)…")
        indexed = snow.refresh_search(conn, repo)
        if not indexed:
            st.write("Index still building; using keyword search until it's ready.")
        st.write("Finding good first issues…")
        first_issues = gh.fetch_good_first_issues(repo)
        st.write("Writing your getting-started checklist with Cortex AI…")
        checklist = build_checklist(conn, repo, info)
        try:
            health = snow.repo_health(conn, repo)
        except Exception as e:
            health = {"error": str(e)}
        status.update(label=f"{repo} is ready", state="complete", expanded=False)
    st.session_state.update(repo=repo, info=info, first_issues=first_issues, checklist=checklist,
                            health=health, messages=[])


# ---------------- Sidebar ----------------
with st.sidebar:
    st.title("🧭 Onboarding Buddy")
    st.caption("Paste a GitHub repo and ask anything about getting started.")
    repo_input = st.text_input("GitHub repo URL", placeholder="https://github.com/pallets/flask")
    if st.button("Load repo", type="primary", use_container_width=True) and repo_input:
        try:
            load(repo_input)
        except Exception as e:
            st.error(f"Couldn't load repo: {e}")

    if "repo" in st.session_state:
        info = st.session_state.info
        st.markdown(f"**[{info['full_name']}]({info['html_url']})**  \n{info['description']}")
        st.subheader("Repo health")
        st.caption("GitHub Archive · Snowflake Marketplace")
        health = st.session_state.health
        if not health:
            st.info("Repo health query not set up yet (sql/repo_health.sql).")
        elif "error" in health:
            st.warning(f"Repo health unavailable: {health['error'][:200]}")
        else:
            cols = st.columns(2)
            for i, (k, v) in enumerate(health.items()):
                cols[i % 2].metric(k.replace("_", " ").title(), "—" if v is None else str(v))

    st.divider()
    st.caption(f"Powered by Snowflake Cortex Search + Cortex AI ({snow.MODEL}, open-weight)")

# ---------------- Main ----------------
if "repo" not in st.session_state:
    st.header("New to a codebase? Start here.")
    st.write(
        "Onboarding Buddy reads a repo's README, docs, contributing guide and most-discussed closed issues, "
        "indexes them in **Snowflake Cortex Search**, and answers your questions with citations using an "
        "**open-weight LLM on Snowflake Cortex**. Paste a repo URL in the sidebar to begin."
    )
    st.stop()

repo, info = st.session_state.repo, st.session_state.info
chat_col, side_col = st.columns([2, 1], gap="large")

with side_col:
    st.subheader("✅ Getting started")
    for i, step in enumerate(st.session_state.checklist):
        st.checkbox(step, key=f"chk_{repo}_{i}")
    st.subheader("🌱 Good first issues")
    if not st.session_state.first_issues:
        st.caption("No beginner-labelled open issues found.")
    for g in st.session_state.first_issues:
        tag = " · assigned" if g["assigned"] else ""
        st.markdown(f"[#{g['number']} {g['title']}]({g['url']})  \n`{g['label']}` · {g['comments']} comments{tag}")

with chat_col:
    st.subheader(f"Ask about {repo}")
    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("sources"):
                with st.expander("Sources"):
                    for line in m["sources"]:
                        st.markdown(line)

    examples = ["How do I set up a dev environment?", "How do I run the tests?",
                "Which good first issue should I pick?"]
    picked = None
    if not st.session_state.messages:
        ex_cols = st.columns(len(examples))
        for c, ex in zip(ex_cols, examples):
            if c.button(ex, use_container_width=True):
                picked = ex
    question = st.chat_input("Ask a question about this repo…") or picked

    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Searching docs with Cortex Search and thinking…"):
                conn = get_conn()
                hits, method = retrieve(conn, repo, question)
                prompt = build_prompt(repo, info, question, hits, st.session_state.first_issues,
                                      st.session_state.messages[:-1])
                answer = snow.complete(conn, prompt).strip()
            st.markdown(answer)
            sources = [f"[{i}] [{h['SOURCE_PATH']}]({h['URL']})" for i, h in enumerate(hits, 1)]
            with st.expander(f"Sources ({method})"):
                for line in sources:
                    st.markdown(line)
        st.session_state.messages.append({"role": "assistant", "content": answer, "sources": sources})
