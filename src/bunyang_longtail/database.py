from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

TABLES_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS topic_cluster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL DEFAULT 'cheongyak',
    semantic_key TEXT NOT NULL UNIQUE,
    family TEXT NOT NULL,
    primary_keyword TEXT NOT NULL,
    secondary_keyword TEXT NOT NULL,
    audience TEXT NOT NULL,
    search_intent TEXT NOT NULL,
    scenario TEXT NOT NULL,
    comparison_keyword TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    outline_json TEXT NOT NULL,
    policy_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic_variant (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL REFERENCES topic_cluster(id) ON DELETE CASCADE,
    variant_key TEXT NOT NULL UNIQUE,
    angle TEXT NOT NULL,
    title TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    seo_score INTEGER NOT NULL,
    prompt_json TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    route_policy TEXT NOT NULL DEFAULT 'gpt_web_first',
    status TEXT NOT NULL DEFAULT 'queued',
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS article_bundle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id INTEGER NOT NULL REFERENCES topic_variant(id) ON DELETE CASCADE,
    bundle_status TEXT NOT NULL DEFAULT 'queued',
    primary_draft_id INTEGER,
    primary_thumbnail_id INTEGER,
    selected_image_ids_json TEXT NOT NULL DEFAULT '[]',
    generation_strategy TEXT NOT NULL DEFAULT 'gpt_web_first',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS generation_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id INTEGER REFERENCES article_bundle(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES topic_variant(id) ON DELETE CASCADE,
    worker_type TEXT NOT NULL,
    route TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    model_label TEXT,
    prompt_version TEXT NOT NULL,
    request_payload_json TEXT NOT NULL,
    response_payload_json TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt_no INTEGER NOT NULL DEFAULT 1,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS article_draft (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id INTEGER REFERENCES article_bundle(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES topic_variant(id) ON DELETE CASCADE,
    source_job_id INTEGER REFERENCES generation_job(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    excerpt TEXT,
    article_markdown TEXT,
    structured_json TEXT,
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    content_hash TEXT,
    normalized_title_hash TEXT,
    similarity_score REAL,
    quality_score REAL,
    model_route TEXT,
    status TEXT NOT NULL DEFAULT 'drafted',
    review_note TEXT,
    naver_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS image_asset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id INTEGER REFERENCES article_bundle(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES topic_variant(id) ON DELETE CASCADE,
    source_job_id INTEGER REFERENCES generation_job(id) ON DELETE SET NULL,
    image_role TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    file_path TEXT,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    phash TEXT,
    status TEXT NOT NULL DEFAULT 'rendered',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS publish_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id INTEGER REFERENCES article_bundle(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES topic_variant(id) ON DELETE CASCADE,
    draft_id INTEGER NOT NULL REFERENCES article_draft(id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'naver_blog',
    target_account TEXT NOT NULL DEFAULT 'default',
    publish_mode TEXT NOT NULL DEFAULT 'draft',
    published_title TEXT NOT NULL,
    naver_url TEXT,
    published_at TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS similarity_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL REFERENCES article_draft(id) ON DELETE CASCADE,
    semantic_key TEXT NOT NULL,
    content_hash TEXT,
    normalized_title_hash TEXT,
    embedding_ref TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS performance_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_history_id INTEGER NOT NULL REFERENCES publish_history(id) ON DELETE CASCADE,
    metric_date TEXT NOT NULL,
    views INTEGER,
    comments INTEGER,
    likes INTEGER,
    manual_score REAL,
    dwell_proxy REAL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS curriculum_track (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    strategy_json TEXT NOT NULL DEFAULT '{}',
    target_ratio REAL NOT NULL DEFAULT 0.75,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS curriculum_node (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES curriculum_track(id) ON DELETE CASCADE,
    node_key TEXT NOT NULL UNIQUE,
    chapter_no INTEGER NOT NULL,
    part_no INTEGER NOT NULL,
    part_title TEXT NOT NULL,
    title TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'cheongyak',
    family TEXT NOT NULL,
    primary_keyword TEXT NOT NULL,
    secondary_keyword TEXT NOT NULL,
    audience TEXT NOT NULL,
    search_intent TEXT NOT NULL,
    scenario TEXT NOT NULL,
    comparison_keyword TEXT,
    angle TEXT NOT NULL DEFAULT '판단형',
    required INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    outline_json TEXT NOT NULL,
    policy_json TEXT NOT NULL DEFAULT '{}',
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(track_id, chapter_no)
);

CREATE TABLE IF NOT EXISTS curriculum_node_variant (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES curriculum_node(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES topic_variant(id) ON DELETE CASCADE,
    variant_role TEXT NOT NULL DEFAULT 'primary',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(node_id, variant_id),
    UNIQUE(node_id, variant_role)
);

CREATE TABLE IF NOT EXISTS curriculum_hub_post (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES curriculum_track(id) ON DELETE CASCADE,
    hub_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    naver_url TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    body_markdown TEXT NOT NULL DEFAULT '',
    body_hash TEXT,
    linked_node_count INTEGER NOT NULL DEFAULT 0,
    total_node_count INTEGER NOT NULL DEFAULT 0,
    needs_sync INTEGER NOT NULL DEFAULT 1,
    pinned INTEGER NOT NULL DEFAULT 1,
    last_rendered_at TEXT,
    last_synced_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(track_id)
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_cluster_family ON topic_cluster(family);
CREATE INDEX IF NOT EXISTS idx_cluster_domain_family ON topic_cluster(domain, family);
CREATE INDEX IF NOT EXISTS idx_cluster_primary ON topic_cluster(primary_keyword);
CREATE INDEX IF NOT EXISTS idx_cluster_domain_primary ON topic_cluster(domain, primary_keyword);
CREATE INDEX IF NOT EXISTS idx_variant_cluster_status ON topic_variant(cluster_id, status);
CREATE INDEX IF NOT EXISTS idx_variant_status ON topic_variant(status);
CREATE INDEX IF NOT EXISTS idx_bundle_variant ON article_bundle(variant_id);
CREATE INDEX IF NOT EXISTS idx_bundle_status ON article_bundle(bundle_status);
CREATE INDEX IF NOT EXISTS idx_job_bundle ON generation_job(bundle_id);
CREATE INDEX IF NOT EXISTS idx_job_variant ON generation_job(variant_id);
CREATE INDEX IF NOT EXISTS idx_job_status ON generation_job(status);
CREATE INDEX IF NOT EXISTS idx_article_bundle ON article_draft(bundle_id);
CREATE INDEX IF NOT EXISTS idx_article_variant ON article_draft(variant_id);
CREATE INDEX IF NOT EXISTS idx_article_status ON article_draft(status);
CREATE INDEX IF NOT EXISTS idx_image_bundle ON image_asset(bundle_id);
CREATE INDEX IF NOT EXISTS idx_image_variant ON image_asset(variant_id);
CREATE INDEX IF NOT EXISTS idx_publish_bundle ON publish_history(bundle_id);
CREATE INDEX IF NOT EXISTS idx_publish_variant ON publish_history(variant_id);
CREATE INDEX IF NOT EXISTS idx_similarity_semantic ON similarity_index(semantic_key);
CREATE INDEX IF NOT EXISTS idx_curriculum_track_status ON curriculum_track(status);
CREATE INDEX IF NOT EXISTS idx_curriculum_node_track_order ON curriculum_node(track_id, chapter_no);
CREATE INDEX IF NOT EXISTS idx_curriculum_node_domain_status ON curriculum_node(domain, status);
CREATE INDEX IF NOT EXISTS idx_curriculum_node_variant_variant ON curriculum_node_variant(variant_id);
CREATE INDEX IF NOT EXISTS idx_curriculum_hub_track ON curriculum_hub_post(track_id);
CREATE INDEX IF NOT EXISTS idx_curriculum_hub_status ON curriculum_hub_post(status, needs_sync);
"""


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
    *,
    backfill_sql: str | None = None,
) -> None:
    if column in _column_names(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    if backfill_sql:
        conn.execute(backfill_sql)


def migrate_db(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(TABLES_SQL)

        _ensure_column(
            conn,
            "topic_cluster",
            "domain",
            "TEXT DEFAULT 'cheongyak'",
            backfill_sql="UPDATE topic_cluster SET domain = 'cheongyak' WHERE domain IS NULL OR domain = ''",
        )

        _ensure_column(
            conn,
            "topic_cluster",
            "policy_json",
            "TEXT DEFAULT '{}'",
            backfill_sql="UPDATE topic_cluster SET policy_json = '{}' WHERE policy_json IS NULL",
        )
        _ensure_column(
            conn,
            "topic_cluster",
            "updated_at",
            "TEXT",
            backfill_sql="UPDATE topic_cluster SET updated_at = created_at WHERE updated_at IS NULL",
        )

        _ensure_column(
            conn,
            "topic_variant",
            "prompt_version",
            "TEXT DEFAULT 'v1'",
            backfill_sql="UPDATE topic_variant SET prompt_version = 'v1' WHERE prompt_version IS NULL",
        )
        _ensure_column(
            conn,
            "topic_variant",
            "route_policy",
            "TEXT DEFAULT 'gpt_web_first'",
            backfill_sql="UPDATE topic_variant SET route_policy = 'gpt_web_first' WHERE route_policy IS NULL",
        )
        _ensure_column(
            conn,
            "topic_variant",
            "updated_at",
            "TEXT",
            backfill_sql="UPDATE topic_variant SET updated_at = created_at WHERE updated_at IS NULL",
        )

        _ensure_column(
            conn,
            "generation_job",
            "bundle_id",
            "INTEGER REFERENCES article_bundle(id) ON DELETE CASCADE",
        )

        _ensure_column(
            conn,
            "article_draft",
            "bundle_id",
            "INTEGER REFERENCES article_bundle(id) ON DELETE CASCADE",
        )
        _ensure_column(conn, "article_draft", "source_job_id", "INTEGER REFERENCES generation_job(id) ON DELETE SET NULL")
        _ensure_column(conn, "article_draft", "excerpt", "TEXT")
        _ensure_column(conn, "article_draft", "structured_json", "TEXT")
        _ensure_column(conn, "article_draft", "normalized_title_hash", "TEXT")
        _ensure_column(conn, "article_draft", "similarity_score", "REAL")
        _ensure_column(conn, "article_draft", "quality_score", "REAL")
        _ensure_column(conn, "article_draft", "model_route", "TEXT")
        _ensure_column(conn, "article_draft", "review_note", "TEXT")
        _ensure_column(
            conn,
            "article_draft",
            "updated_at",
            "TEXT",
            backfill_sql="UPDATE article_draft SET updated_at = created_at WHERE updated_at IS NULL",
        )

        _ensure_column(
            conn,
            "image_asset",
            "bundle_id",
            "INTEGER REFERENCES article_bundle(id) ON DELETE CASCADE",
        )

        _ensure_column(
            conn,
            "publish_history",
            "bundle_id",
            "INTEGER REFERENCES article_bundle(id) ON DELETE CASCADE",
        )

        _ensure_column(
            conn,
            "curriculum_track",
            "target_ratio",
            "REAL DEFAULT 0.75",
            backfill_sql="UPDATE curriculum_track SET target_ratio = 0.75 WHERE target_ratio IS NULL",
        )
        _ensure_column(
            conn,
            "curriculum_track",
            "updated_at",
            "TEXT",
            backfill_sql="UPDATE curriculum_track SET updated_at = created_at WHERE updated_at IS NULL",
        )
        _ensure_column(conn, "curriculum_node", "angle", "TEXT DEFAULT '판단형'")
        _ensure_column(conn, "curriculum_node", "published_at", "TEXT")
        _ensure_column(
            conn,
            "curriculum_node",
            "updated_at",
            "TEXT",
            backfill_sql="UPDATE curriculum_node SET updated_at = created_at WHERE updated_at IS NULL",
        )
        _ensure_column(conn, "curriculum_hub_post", "body_hash", "TEXT")
        _ensure_column(
            conn,
            "curriculum_hub_post",
            "linked_node_count",
            "INTEGER DEFAULT 0",
            backfill_sql="UPDATE curriculum_hub_post SET linked_node_count = 0 WHERE linked_node_count IS NULL",
        )
        _ensure_column(
            conn,
            "curriculum_hub_post",
            "total_node_count",
            "INTEGER DEFAULT 0",
            backfill_sql="UPDATE curriculum_hub_post SET total_node_count = 0 WHERE total_node_count IS NULL",
        )
        _ensure_column(
            conn,
            "curriculum_hub_post",
            "needs_sync",
            "INTEGER DEFAULT 1",
            backfill_sql="UPDATE curriculum_hub_post SET needs_sync = 1 WHERE needs_sync IS NULL",
        )
        _ensure_column(
            conn,
            "curriculum_hub_post",
            "pinned",
            "INTEGER DEFAULT 1",
            backfill_sql="UPDATE curriculum_hub_post SET pinned = 1 WHERE pinned IS NULL",
        )
        _ensure_column(conn, "curriculum_hub_post", "last_rendered_at", "TEXT")
        _ensure_column(conn, "curriculum_hub_post", "last_synced_at", "TEXT")

        conn.executescript(INDEXES_SQL)


def init_db(db_path: str | Path) -> None:
    migrate_db(db_path)


def fetch_one(conn: sqlite3.Connection, query: str, params: tuple = ()) -> sqlite3.Row | None:
    return conn.execute(query, params).fetchone()


def fetch_all(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(query, params).fetchall()
