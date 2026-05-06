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

CREATE INDEX IF NOT EXISTS idx_curriculum_track_status ON curriculum_track(status);
CREATE INDEX IF NOT EXISTS idx_curriculum_node_track_order ON curriculum_node(track_id, chapter_no);
CREATE INDEX IF NOT EXISTS idx_curriculum_node_domain_status ON curriculum_node(domain, status);
CREATE INDEX IF NOT EXISTS idx_curriculum_node_variant_variant ON curriculum_node_variant(variant_id);
CREATE INDEX IF NOT EXISTS idx_curriculum_hub_track ON curriculum_hub_post(track_id);
CREATE INDEX IF NOT EXISTS idx_curriculum_hub_status ON curriculum_hub_post(status, needs_sync);
