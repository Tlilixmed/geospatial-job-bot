"""Runtime configuration: environment variables + config/sources.toml.

Environment variables override defaults; nothing secret lives in code or config files.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .matching.profile import MatchConfig

ROOT = Path(__file__).resolve().parent.parent


def env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value is not None and value.strip() != "" else default


def env_bool(name: str, default: bool) -> bool:
    value = env_str(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on", "y"}


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    try:
        return int(value) if value is not None else default
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {value!r}") from None


def env_float(name: str, default: float) -> float:
    value = env_str(name)
    try:
        return float(value) if value is not None else default
    except ValueError:
        raise ValueError(f"{name} must be a number, got {value!r}") from None


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    value = env_str(name)
    if value is None:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    # execution
    dry_run: bool = False
    dry_run_write_state: bool = False
    run_time_budget_s: int = 40 * 60
    source_concurrency: int = 6
    log_level: str = "INFO"

    # http
    user_agent: str = "GeospatialJobBot/1.0 (+https://github.com/your-account/geospatial-job-bot)"
    robots_agent_token: str = "GeospatialJobBot"
    default_host_delay: float = 1.5
    http_timeout: float = 30.0

    # storage
    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str | None = None
    require_r2: bool = False
    local_state_dir: str = ".state"
    state_prefix: str = ""
    allow_state_reset: bool = False
    raw_retention_days: int = 14
    run_retention_days: int = 90
    state_backups_keep: int = 20
    store_raw_snapshots: bool = True
    rejected_retention_days: int = 30
    job_retention_days: int = 180

    # matching / alerts
    high_threshold: int = 70
    medium_threshold: int = 55
    notify_possible: bool = True
    max_job_age_hours: int = 48
    rotation_max_job_age_hours: int = 336
    max_alerts_per_run: int = 25
    max_notify_attempts: int = 5
    alert_on_changes: bool = False
    preferred_locations: list[str] = field(default_factory=list)
    accepted_remote_scopes: list[str] = field(default_factory=list)
    strict_location: bool = False
    extra_negative_titles: list[str] = field(default_factory=list)

    # telegram
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_delay_s: float = 1.2

    # discovery budgets
    rotation_boards_per_ats: int = 60
    hot_board_days: int = 30
    max_detail_fetches_per_board: int = 30
    generic_pages_per_run: int = 80
    generic_pages_per_host: int = 15
    page_refetch_days: int = 7
    commoncrawl_enabled: bool = True
    commoncrawl_pages_per_run: int = 2
    commoncrawl_max_new_boards: int = 3000
    search_queries_per_run: int = 8
    searxng_url: str | None = None
    duckduckgo_enabled: bool = True
    usajobs_api_key: str | None = None
    usajobs_email: str | None = None
    jobspy_enabled: bool = True
    jobspy_sites: list[str] = field(default_factory=lambda: ["indeed"])
    jobspy_terms_per_run: int = 3
    jobspy_locations: list[str] = field(default_factory=lambda: ["Remote"])
    jobspy_results_wanted: int = 30
    jobspy_country_indeed: str = "USA"
    feeds_enabled: list[str] = field(default_factory=lambda: ["remotive", "jobicy", "himalayas", "arbeitnow", "remoteok"])
    disabled_backends: list[str] = field(default_factory=list)

    # sources file content
    sources_file: str = str(ROOT / "config" / "sources.toml")
    sources: dict = field(default_factory=dict)

    @property
    def r2_configured(self) -> bool:
        return all([self.r2_endpoint_url, self.r2_access_key_id, self.r2_secret_access_key, self.r2_bucket_name])

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def match_config(self) -> MatchConfig:
        return MatchConfig(
            high_threshold=self.high_threshold,
            medium_threshold=self.medium_threshold,
            preferred_locations=self.preferred_locations,
            accepted_remote_scopes=self.accepted_remote_scopes,
            strict_location=self.strict_location,
            extra_negative_titles=self.extra_negative_titles,
        )

    def secrets(self) -> list[str]:
        return [s for s in (self.r2_access_key_id, self.r2_secret_access_key, self.telegram_bot_token,
                            self.usajobs_api_key) if s]


def load_sources(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("rb") as fh:
        return tomllib.load(fh)


def load_settings() -> Settings:
    s = Settings()
    s.dry_run = env_bool("DRY_RUN", s.dry_run)
    s.dry_run_write_state = env_bool("DRY_RUN_WRITE_STATE", s.dry_run_write_state)
    s.run_time_budget_s = env_int("RUN_TIME_BUDGET_MINUTES", s.run_time_budget_s // 60) * 60
    s.source_concurrency = max(1, env_int("SOURCE_CONCURRENCY", s.source_concurrency))
    s.log_level = env_str("LOG_LEVEL", s.log_level)
    s.user_agent = env_str("BOT_USER_AGENT", s.user_agent)
    s.default_host_delay = env_float("DEFAULT_HOST_DELAY", s.default_host_delay)
    s.http_timeout = env_float("HTTP_TIMEOUT", s.http_timeout)

    s.r2_endpoint_url = env_str("R2_ENDPOINT_URL")
    s.r2_access_key_id = env_str("R2_ACCESS_KEY_ID")
    s.r2_secret_access_key = env_str("R2_SECRET_ACCESS_KEY")
    s.r2_bucket_name = env_str("R2_BUCKET_NAME")
    s.require_r2 = env_bool("REQUIRE_R2", s.require_r2)
    s.local_state_dir = env_str("LOCAL_STATE_DIR", s.local_state_dir)
    s.state_prefix = env_str("STATE_PREFIX", s.state_prefix)
    s.allow_state_reset = env_bool("ALLOW_STATE_RESET", s.allow_state_reset)
    s.raw_retention_days = env_int("RAW_RETENTION_DAYS", s.raw_retention_days)
    s.run_retention_days = env_int("RUN_RETENTION_DAYS", s.run_retention_days)
    s.state_backups_keep = env_int("STATE_BACKUPS_KEEP", s.state_backups_keep)
    s.store_raw_snapshots = env_bool("STORE_RAW_SNAPSHOTS", s.store_raw_snapshots)
    s.rejected_retention_days = env_int("REJECTED_RETENTION_DAYS", s.rejected_retention_days)
    s.job_retention_days = env_int("JOB_RETENTION_DAYS", s.job_retention_days)

    s.high_threshold = env_int("HIGH_MATCH_THRESHOLD", s.high_threshold)
    s.medium_threshold = env_int("MEDIUM_MATCH_THRESHOLD", s.medium_threshold)
    s.notify_possible = env_bool("NOTIFY_POSSIBLE", s.notify_possible)
    s.max_job_age_hours = env_int("MAX_JOB_AGE_HOURS", s.max_job_age_hours)
    s.rotation_max_job_age_hours = env_int("ROTATION_MAX_JOB_AGE_HOURS", s.rotation_max_job_age_hours)
    s.max_alerts_per_run = env_int("MAX_ALERTS_PER_RUN", s.max_alerts_per_run)
    s.max_notify_attempts = env_int("MAX_NOTIFY_ATTEMPTS", s.max_notify_attempts)
    s.alert_on_changes = env_bool("ALERT_ON_CHANGES", s.alert_on_changes)
    s.preferred_locations = env_list("PREFERRED_LOCATIONS", s.preferred_locations)
    s.accepted_remote_scopes = env_list("ACCEPTED_REMOTE_SCOPES", s.accepted_remote_scopes)
    s.strict_location = env_bool("STRICT_LOCATION_FILTER", s.strict_location)
    s.extra_negative_titles = env_list("EXTRA_NEGATIVE_TITLES", s.extra_negative_titles)

    s.telegram_bot_token = env_str("TELEGRAM_BOT_TOKEN")
    s.telegram_chat_id = env_str("TELEGRAM_CHAT_ID")
    s.telegram_delay_s = max(1.2, env_float("TELEGRAM_DELAY_SECONDS", s.telegram_delay_s))

    s.rotation_boards_per_ats = env_int("ROTATION_BOARDS_PER_ATS", s.rotation_boards_per_ats)
    s.max_detail_fetches_per_board = env_int("MAX_DETAIL_FETCHES_PER_BOARD", s.max_detail_fetches_per_board)
    s.generic_pages_per_run = env_int("GENERIC_PAGES_PER_RUN", s.generic_pages_per_run)
    s.generic_pages_per_host = env_int("GENERIC_PAGES_PER_HOST", s.generic_pages_per_host)
    s.commoncrawl_enabled = env_bool("COMMONCRAWL_ENABLED", s.commoncrawl_enabled)
    s.commoncrawl_pages_per_run = env_int("COMMONCRAWL_PAGES_PER_RUN", s.commoncrawl_pages_per_run)
    s.search_queries_per_run = env_int("SEARCH_QUERIES_PER_RUN", s.search_queries_per_run)
    s.searxng_url = env_str("SEARXNG_URL")
    s.duckduckgo_enabled = env_bool("DUCKDUCKGO_ENABLED", s.duckduckgo_enabled)
    s.usajobs_api_key = env_str("USAJOBS_API_KEY")
    s.usajobs_email = env_str("USAJOBS_EMAIL")
    s.jobspy_enabled = env_bool("JOBSPY_ENABLED", s.jobspy_enabled)
    s.jobspy_sites = env_list("JOBSPY_SITES", s.jobspy_sites)
    s.jobspy_terms_per_run = env_int("JOBSPY_TERMS_PER_RUN", s.jobspy_terms_per_run)
    s.jobspy_locations = env_list("JOBSPY_LOCATIONS", s.jobspy_locations)
    s.jobspy_results_wanted = env_int("JOBSPY_RESULTS_WANTED", s.jobspy_results_wanted)
    s.jobspy_country_indeed = env_str("JOBSPY_COUNTRY_INDEED", s.jobspy_country_indeed)
    s.feeds_enabled = env_list("FEEDS_ENABLED", s.feeds_enabled)
    s.disabled_backends = env_list("DISABLED_BACKENDS", s.disabled_backends)

    s.sources_file = env_str("SOURCES_FILE", s.sources_file)
    s.sources = load_sources(s.sources_file)
    if s.medium_threshold > s.high_threshold:
        raise ValueError("MEDIUM_MATCH_THRESHOLD must not exceed HIGH_MATCH_THRESHOLD")
    return s
