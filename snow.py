"""Everything that talks to Snowflake: storage, Cortex Search, Cortex COMPLETE, GitHub Archive."""
import json
import os
import re
import time
from pathlib import Path

import snowflake.connector
from cryptography.hazmat.primitives import serialization
from dotenv import load_dotenv

load_dotenv()

DB = os.getenv("OB_DATABASE", "ONBOARDING_BUDDY")
SCHEMA = os.getenv("OB_SCHEMA", "APP")
TABLE = f"{DB}.{SCHEMA}.REPO_DOCS"
SEARCH_SERVICE = f"{DB}.{SCHEMA}.REPO_DOCS_SEARCH"
MODEL = os.getenv("OB_MODEL", "llama3.3-70b")
SQL_DIR = Path(__file__).parent / "sql"


def connect():
    key_path = os.path.expanduser(os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE", "~/.snowflake/ob_app_key.p8"))
    with open(key_path, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.getenv("SNOWFLAKE_USER", "OB_APP"),
        private_key=der,
        role=os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "ONBOARDING_WH"),
        database=DB,
        schema=SCHEMA,
        client_session_keep_alive=True,
    )


def _one(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                REPO STRING, SOURCE_TYPE STRING, SOURCE_PATH STRING, URL STRING,
                CHUNK_ID INT, CONTENT STRING, LOADED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
            ) CHANGE_TRACKING = TRUE""")


def repo_loaded(conn, repo: str) -> int:
    return _one(conn, f"SELECT COUNT(*) FROM {TABLE} WHERE REPO = %s", (repo,)) or 0


def load_repo(conn, repo: str, rows: list[dict]) -> int:
    ensure_table(conn)
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {TABLE} WHERE REPO = %s", (repo,))
        cur.executemany(
            f"INSERT INTO {TABLE} (REPO, SOURCE_TYPE, SOURCE_PATH, URL, CHUNK_ID, CONTENT) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [(repo, r["source_type"], r["source_path"], r["url"], r["chunk_id"], r["content"]) for r in rows],
        )
    return len(rows)


def refresh_search(conn, repo: str, timeout_s: int = 120) -> bool:
    """Ask Cortex Search to re-index now, then wait until the repo shows up in results."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER CORTEX SEARCH SERVICE {SEARCH_SERVICE} REFRESH")
    except snowflake.connector.Error:
        pass  # older accounts: fall back to waiting for TARGET_LAG
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if search(conn, repo, "getting started setup install", limit=1):
                return True
        except snowflake.connector.Error:
            pass
        time.sleep(5)
    return False


def search(conn, repo: str, query: str, limit: int = 8) -> list[dict]:
    payload = json.dumps({
        "query": query,
        "columns": ["CONTENT", "SOURCE_PATH", "URL", "SOURCE_TYPE"],
        "filter": {"@eq": {"REPO": repo}},
        "limit": limit,
    })
    raw = _one(conn, "SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(%s, %s)", (SEARCH_SERVICE, payload))
    results = json.loads(raw).get("results", []) if raw else []
    return [{k.upper(): v for k, v in r.items()} for r in results]


def keyword_search(conn, repo: str, query: str, limit: int = 8) -> list[dict]:
    """Fallback while the Cortex Search index is still building."""
    with conn.cursor(snowflake.connector.DictCursor) as cur:
        cur.execute(f"SELECT CONTENT, SOURCE_PATH, URL, SOURCE_TYPE FROM {TABLE} WHERE REPO = %s", (repo,))
        rows = cur.fetchall()
    terms = {t for t in re.findall(r"\w{3,}", query.lower())}
    rows.sort(key=lambda r: -sum(r["CONTENT"].lower().count(t) for t in terms))
    return rows[:limit]


def complete(conn, prompt: str, model: str = MODEL) -> str:
    try:
        out = _one(conn, "SELECT AI_COMPLETE(%s, %s)", (model, prompt)) or ""
    except snowflake.connector.Error:
        # Older accounts without AI_COMPLETE
        out = _one(conn, "SELECT SNOWFLAKE.CORTEX.COMPLETE(%s, %s)", (model, prompt)) or ""
    # AI_COMPLETE returns a JSON-encoded string ("...")
    if out.startswith('"'):
        try:
            out = json.loads(out)
        except json.JSONDecodeError:
            pass
    return out


def _health_sql() -> str:
    """Prefer the CoCo-written query; fall back to the hand-written draft."""
    for name in ("repo_health.sql", "repo_health.fallback.sql"):
        path = SQL_DIR / name
        sql = path.read_text().strip().rstrip(";") if path.exists() else ""
        if sql and not sql.startswith("-- TODO"):
            return sql
    return ""


def repo_health(conn, repo: str) -> dict | None:
    """One row of activity stats for `repo` from the GitHub Archive (Snowflake Marketplace)."""
    sql = _health_sql()
    if not sql:
        return None
    with conn.cursor(snowflake.connector.DictCursor) as cur:
        cur.execute("SET REPO_NAME = %s", (repo,))
        cur.execute(sql)
        return cur.fetchone()
