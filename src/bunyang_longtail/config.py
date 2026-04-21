from __future__ import annotations

import os
from pathlib import Path

DEV_ROOT = Path("/home/kj/app/bunyang_longtail/dev")
DATA_DIR = DEV_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "longtail.sqlite3"
DEFAULT_EXPORT_PATH = DATA_DIR / "queued_prompts.jsonl"
GPT_PROFILE_DIR = DATA_DIR / "gpt_profiles"
GPT_WEB_ARTIFACT_DIR = DATA_DIR / "gpt_web_artifacts"
OPENAI_COMPAT_ARTIFACT_DIR = DATA_DIR / "openai_compat_artifacts"
SIMULATED_ASSET_DIR = DATA_DIR / "simulated_assets"
OPENAI_COMPAT_BASE_URL = os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.openai.com/v1")
OPENAI_COMPAT_TEXT_MODEL = os.getenv("OPENAI_COMPAT_TEXT_MODEL", "gpt-4.1-mini")
OPENAI_COMPAT_IMAGE_MODEL = os.getenv("OPENAI_COMPAT_IMAGE_MODEL", "gpt-image-1")


def ensure_data_dir() -> None:
    for path in (DATA_DIR, GPT_PROFILE_DIR, GPT_WEB_ARTIFACT_DIR, OPENAI_COMPAT_ARTIFACT_DIR, SIMULATED_ASSET_DIR):
        path.mkdir(parents=True, exist_ok=True)
