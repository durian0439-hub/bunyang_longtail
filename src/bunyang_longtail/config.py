from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("BUNYANG_LONGTAIL_ROOT", Path(__file__).resolve().parents[2])).resolve()
DEV_ROOT = PROJECT_ROOT  # backward-compatible name; not necessarily the dev checkout.
DATA_DIR = Path(os.getenv("BUNYANG_LONGTAIL_DATA_DIR", PROJECT_ROOT / "data")).resolve()
DEFAULT_DB_PATH = DATA_DIR / "longtail.sqlite3"
DEFAULT_EXPORT_PATH = DATA_DIR / "queued_prompts.jsonl"
GPT_PROFILE_DIR = DATA_DIR / "gpt_profiles"
GPT_WEB_ARTIFACT_DIR = DATA_DIR / "gpt_web_artifacts"
OPENAI_COMPAT_ARTIFACT_DIR = DATA_DIR / "openai_compat_artifacts"
CODEX_CLI_ARTIFACT_DIR = DATA_DIR / "codex_cli_artifacts"
SIMULATED_ASSET_DIR = DATA_DIR / "simulated_assets"
OPENAI_COMPAT_BASE_URL = os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.openai.com/v1")
OPENAI_COMPAT_TEXT_MODEL = os.getenv("OPENAI_COMPAT_TEXT_MODEL", "gpt-4.1-mini")
OPENAI_COMPAT_IMAGE_MODEL = os.getenv("OPENAI_COMPAT_IMAGE_MODEL", "gpt-image-1")


def ensure_data_dir() -> None:
    for path in (DATA_DIR, GPT_PROFILE_DIR, GPT_WEB_ARTIFACT_DIR, OPENAI_COMPAT_ARTIFACT_DIR, CODEX_CLI_ARTIFACT_DIR, SIMULATED_ASSET_DIR):
        path.mkdir(parents=True, exist_ok=True)
