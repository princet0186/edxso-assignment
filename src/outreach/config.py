"""Configuration: secrets from config/.env, campaign rules from config/settings.yaml.

Secrets and rules are kept apart on purpose: secrets differ per machine and must never be
committed, while rules are part of the reviewed, versioned behaviour of the system.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
ENV_FILE = CONFIG_DIR / ".env"
SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
DEFAULT_DATABASE_URL = "sqlite:///data/outreach.db"
MAX_SCORE = 100


class MissingConfigError(RuntimeError):
    """A value needed by the requested operation is not configured."""


class SendMode(StrEnum):
    DRY_RUN = "DRY_RUN"
    REDIRECT = "REDIRECT"
    LIVE = "LIVE"


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    youtube_data_api_key: str = ""

    llm_providers: str = "gemini,groq"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_model: str = "gemini-3.5-flash"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_app_password: str = ""
    sender_name: str = ""

    send_mode: SendMode = SendMode.DRY_RUN
    test_inbox: str = ""
    live_allowlist: str = ""
    auto_approve: bool = False

    api_key: str = ""
    database_url: str = DEFAULT_DATABASE_URL

    @property
    def provider_order(self) -> list[str]:
        return [name.strip().lower() for name in self.llm_providers.split(",") if name.strip()]

    @property
    def live_allowlist_addresses(self) -> set[str]:
        return {addr.strip().lower() for addr in self.live_allowlist.split(",") if addr.strip()}


SECRET_NAMES_BY_PURPOSE: dict[str, tuple[str, ...]] = {
    "Discovery (YouTube)": ("youtube_data_api_key",),
    "LLM": ("gemini_api_key", "groq_api_key"),
    "Email sending": ("smtp_user", "smtp_app_password", "sender_name", "test_inbox"),
    "n8n → API": ("api_key",),
}


def require(value: str, env_name: str) -> str:
    if not value:
        raise MissingConfigError(f"{env_name} is not set in config/.env (see docs/SETUP.md)")
    return value


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DiscoverySettings(FrozenModel):
    queries: list[str] = Field(min_length=1)
    published_within_days: int = Field(gt=0)
    results_per_query: int = Field(gt=0, le=50)
    search_order: str
    relevance_language: str
    region_code: str | None = None
    recent_videos_per_channel: int = Field(gt=0, le=50)


class YouTubeSettings(FrozenModel):
    quota_ceiling_units: int = Field(gt=0)


class MetricsSettings(FrozenModel):
    min_video_age_hours: int = Field(ge=0)
    long_form_min_seconds: int = Field(gt=0)
    min_sample_videos: int = Field(gt=0)
    activity_window_days: int = Field(gt=0)


class FilterSettings(FrozenModel):
    subscribers_min: int = Field(gt=0)
    subscribers_max: int = Field(gt=0)
    min_engagement_rate: float = Field(gt=0, lt=1)
    min_relevance: float = Field(ge=0, le=1)
    accepted_niches: list[str] = Field(min_length=1)
    allowed_languages: list[str] = Field(min_length=1)
    geography_allowlist: list[str] = Field(default_factory=list)

    def subscriber_range_contains(self, subscriber_count: int) -> bool:
        return self.subscribers_min <= subscriber_count <= self.subscribers_max


class LlmSettings(FrozenModel):
    classify_temperature: float = Field(ge=0, le=2)
    classify_batch_size: int = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)
    requests_per_minute: dict[str, int]


class ScoringWeights(FrozenModel):
    relevance: float = Field(ge=0)
    engagement: float = Field(ge=0)
    size_fit: float = Field(ge=0)
    activity: float = Field(ge=0)
    contactability: float = Field(ge=0)

    @model_validator(mode="after")
    def weights_sum_to_100(self) -> "ScoringWeights":
        total = self.relevance + self.engagement + self.size_fit + self.activity
        if total + self.contactability != MAX_SCORE:
            raise ValueError(f"Scoring weights must sum to {MAX_SCORE}")
        return self


class ScoringSettings(FrozenModel):
    weights: ScoringWeights
    engagement_rate_for_full_marks: float = Field(gt=0)
    size_sweet_spot_min: int = Field(gt=0)
    size_sweet_spot_max: int = Field(gt=0)
    uploads_for_full_activity: int = Field(gt=0)


class EnrichmentSettings(FrozenModel):
    max_pages_per_site: int = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)
    seconds_between_requests_per_domain: float = Field(ge=0)
    user_agent: str


class Settings(FrozenModel):
    campaign_id: str
    rules_version: str
    niche_name: str
    discovery: DiscoverySettings
    youtube: YouTubeSettings
    metrics: MetricsSettings
    filters: FilterSettings
    llm: LlmSettings
    scoring: ScoringSettings
    enrichment: EnrichmentSettings


@lru_cache
def load_secrets() -> Secrets:
    return Secrets()


@lru_cache
def load_settings(path: Path = SETTINGS_FILE) -> Settings:
    with path.open(encoding="utf-8") as settings_file:
        return Settings.model_validate(yaml.safe_load(settings_file))
