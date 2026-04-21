PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS topic_cluster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
