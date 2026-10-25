"""Pull onboarding material (docs, closed issues, good first issues) from a GitHub repo."""
import os
import re
import shutil
import subprocess

import requests

API = "https://api.github.com"
DOC_NAME = re.compile(
    r"(^|/)(readme|contributing|install|setup|development|developing|hacking|getting[-_]?started|building|faq)[^/]*\.(md|rst|txt)$",
    re.I,
)
DOC_DIR = re.compile(r"^(docs?|documentation|\.github)/.*\.(md|rst|mdx)$", re.I)
FIRST_ISSUE_LABEL = re.compile(r"good.?first|first.?timer|beginner|easy|starter|help.?wanted", re.I)

MAX_DOC_FILES = 40
MAX_FILE_CHARS = 60_000
MAX_CLOSED_ISSUES = 60
CHUNK_CHARS = 1800


def _token():
    if os.getenv("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
            return out.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


_session = requests.Session()
_session.headers["Accept"] = "application/vnd.github+json"
_tok = _token()
if _tok:
    _session.headers["Authorization"] = f"Bearer {_tok}"


def parse_repo(url_or_name: str) -> str:
    """'https://github.com/pallets/flask/tree/main' -> 'pallets/flask'."""
    s = url_or_name.strip().removesuffix(".git")
    m = re.search(r"github\.com[/:]([^/\s]+)/([^/\s#?]+)", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    if re.fullmatch(r"[\w.-]+/[\w.-]+", s):
        return s
    raise ValueError(f"Not a GitHub repo: {url_or_name!r}")


def _get(path, **params):
    r = _session.get(f"{API}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def repo_info(repo: str) -> dict:
    d = _get(f"/repos/{repo}")
    return {
        "full_name": d["full_name"],
        "description": d.get("description") or "",
        "default_branch": d["default_branch"],
        "stars": d.get("stargazers_count", 0),
        "open_issues": d.get("open_issues_count", 0),
        "language": d.get("language") or "",
        "html_url": d["html_url"],
    }


def _pick_doc_paths(repo: str, branch: str) -> list[str]:
    tree = _get(f"/repos/{repo}/git/trees/{branch}", recursive=1).get("tree", [])
    paths = [t["path"] for t in tree if t["type"] == "blob" and t.get("size", 0) < 400_000]
    def priority(p):
        in_docs, named = bool(DOC_DIR.match(p)), bool(DOC_NAME.search(p))
        if "/" not in p and named:
            return 0  # top-level README / CONTRIBUTING
        if in_docs and named:
            return 1  # docs/**/contributing.rst, docs/**/installation.md
        if in_docs:
            return 2
        return 3  # nested READMEs in examples/, packages/, …

    picked = [p for p in paths if DOC_DIR.match(p) or (DOC_NAME.search(p) and p.count("/") <= 2)]
    picked.sort(key=lambda p: (priority(p), p.count("/"), len(p)))
    return picked[:MAX_DOC_FILES]


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split on blank lines / headings, packing paragraphs up to `size` chars."""
    parts = re.split(r"\n(?=#{1,4} )|\n\s*\n", text)
    chunks, cur = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        while len(p) > size:  # very long paragraph / code block
            chunks.append(p[:size])
            p = p[size:]
        if len(cur) + len(p) + 2 > size and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def fetch_docs(repo: str, branch: str) -> list[dict]:
    rows = []
    for path in _pick_doc_paths(repo, branch):
        raw = _session.get(f"https://raw.githubusercontent.com/{repo}/{branch}/{path}", timeout=20)
        if raw.status_code != 200:
            continue
        url = f"https://github.com/{repo}/blob/{branch}/{path}"
        for i, c in enumerate(chunk_text(raw.text[:MAX_FILE_CHARS])):
            rows.append({"source_type": "doc", "source_path": path, "url": url, "chunk_id": i,
                         "content": f"[{path}]\n{c}"})
    return rows


def fetch_closed_issues(repo: str) -> list[dict]:
    # Most-commented closed issues tend to be the recurring setup questions
    issues = _get(f"/repos/{repo}/issues", state="closed", sort="comments", direction="desc",
                  per_page=MAX_CLOSED_ISSUES)
    rows = []
    for it in issues:
        if "pull_request" in it:
            continue
        body = (it.get("body") or "")[:4000]
        text = f"[Issue #{it['number']}] {it['title']}\n{body}"
        for i, c in enumerate(chunk_text(text)):
            rows.append({"source_type": "issue", "source_path": f"issue #{it['number']}",
                         "url": it["html_url"], "chunk_id": i, "content": c})
    return rows


BEGINNER_LABELS = '"good first issue","good-first-issue","good first bug","beginner","beginner friendly","easy","first-timers-only","starter"'


def fetch_good_first_issues(repo: str, limit: int = 8) -> list[dict]:
    out = []
    # Comma-separated labels are OR'd by GitHub search; "help wanted" only if nothing beginner-labelled exists
    for labels in (BEGINNER_LABELS, '"help wanted"'):
        res = _get("/search/issues", q=f"repo:{repo} is:issue is:open label:{labels}", per_page=30)
        for it in res.get("items", []):
            label = next((l["name"] for l in it.get("labels", []) if FIRST_ISSUE_LABEL.search(l["name"])),
                         labels.strip('"'))
            out.append({"number": it["number"], "title": it["title"], "url": it["html_url"],
                        "label": label, "comments": it.get("comments", 0),
                        "assigned": bool(it.get("assignees"))})
        if out:
            break
    # Unassigned, less-crowded issues are better picks for a newcomer
    out.sort(key=lambda i: (i["assigned"], i["comments"]))
    return out[:limit]


def fetch_repo(repo: str) -> tuple[dict, list[dict]]:
    info = repo_info(repo)
    rows = fetch_docs(repo, info["default_branch"]) + fetch_closed_issues(repo)
    return info, rows
