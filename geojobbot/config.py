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
    max_job_age_hours: int = 360  # 15 days: postings older than this are never alerted
    rotation_max_job_age_hours: int = 360
    max_alerts_per_run: int = 20  # the rest follow next run, best first: a digest should be readable
    max_notify_attempts: int = 5
    alert_on_changes: bool = False
    preferred_locations: list[str] = field(default_factory=list)
    accepted_remote_scopes: list[str] = field(default_factory=list)
    strict_location: bool = False
    extra_negative_titles: list[str] = field(default_factory=list)
    exclude_internships: bool = True
    # runtime preferences set from Telegram (state/prefs.json), never from the environment
    muted_terms: list[str] = field(default_factory=list)
    hidden_ids: list[str] = field(default_factory=list)
    alerts_paused: bool = False
    watch_list: list[dict] = field(default_factory=list)  # /watch: [{"name", "url", "at"}] from the stored preferences
    exclude_work_auth_required: bool = True  # drop postings needing existing authorisation / no sponsorship
    home_countries: list[str] = field(default_factory=lambda: ["Tunisia"])

    # telegram
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_delay_s: float = 1.2
    alert_format: str = "digest"  # digest: one numbered list per run | individual: one message per job

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
    jobspy_sites: list[str] = field(default_factory=lambda: ["indeed", "linkedin"])
    jobspy_terms_per_run: int = 3
    # "Location" or "Location@indeed_country" (e.g. "Canada@canada", "Tunisia@worldwide"); the country
    # selects the Indeed/Glassdoor site and falls back to jobspy_country_indeed.
    jobspy_locations: list[str] = field(default_factory=lambda: ["Remote"])
    jobspy_results_wanted: int = 15
    jobspy_locations_per_run: int = 3  # locations rotate across runs so runtime stays flat as the list grows
    jobspy_country_indeed: str = "USA"
    jobspy_linkedin_fetch_description: bool = True
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None
    adzuna_countries: list[str] = field(default_factory=lambda: ["ca", "gb", "us"])
    adzuna_requests_per_run: int = 30  # free tier: 250 calls/day
    jooble_api_key: str | None = None
    francetravail_client_id: str | None = None  # free application at francetravail.io (API "Offres d'emploi v2")
    francetravail_client_secret: str | None = None
    jooble_locations: list[str] = field(default_factory=list)  # falls back to preferred_locations
    jooble_requests_per_run: int = 12
    # Workers AI second opinion (free daily allowance). The account id is derived from the R2 endpoint.
    cloudflare_ai_token: str | None = None
    cloudflare_account_id: str | None = None
    ai_model: str = "@cf/meta/llama-3.1-8b-instruct"
    ai_reviews_per_run: int = 40  # ~35 neurons each; 6 runs a day stays inside the free 10,000/day
    ai_veto_possible: bool = True
    candidate_profile: str = ""  # empty = the built-in profile in geojobbot/ai/review.py
    reliefweb_appname: str | None = None  # approved app name from ReliefWeb (free): UN/NGO jobs, hired internationally
    weekly_summary: bool = True
    my_skills: list[str] = field(default_factory=list)  # empty = the list in insights/radar.py
    monthly_radar: bool = True
    market_signals: bool = True  # World Bank procurement: geospatial awards, consultancies, tenders
    sponsor_registers: bool = True  # UK / Canada / Netherlands official sponsor registers, refreshed weekly
    prospects_per_run: int = 10  # employers looked up per run on the public ATS APIs (insights/prospects.py); 0 = off
    visa_penalty: int = 12  # points lost when the visa route is hard or blocked and nothing says the employer sponsors; 0 = label only
    visa_paths: bool = True  # per-job check against config/visa_paths.toml (licence, salary minimum, occupation)
    my_languages: list[str] = field(default_factory=lambda: ["English", "French", "Arabic"])  # opens language-based routes
    # JSearch (RapidAPI): "query@country" entries rotated across runs, JSEARCH_REQUESTS_PER_RUN per run.
    # The free tier allows 200 requests/month: one per 4-hourly run stays inside it.
    jsearch_api_key: str | None = None
    jsearch_requests_per_run: int = 1
    jsearch_queries: list[str] = field(default_factory=lambda: [
        "GIS geospatial geomatics@ca", "SIG géomatique topographe@tn", "GIS analyst remote@us",
        "LiDAR photogrammetry surveying@ca", "cartographer remote sensing@ca", "ingénieur SIG géomatique@fr",
        "GIS visa sponsorship@us", "GIS specialist@ae", "GIS engineer@sa", "GIS analyst@qa", "géomaticien SIG@be",
        "GIS Geomatik@ch", "GIS mining exploration@au", "GIS visa sponsorship@gb", "GIS analyst@de",
    ])
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

    def negative_titles(self) -> list[str]:
        """User exclusions plus internship titles when they are excluded."""
        from .matching.profile import INTERNSHIP_TITLES
        return list(self.extra_negative_titles) + (list(INTERNSHIP_TITLES) if self.exclude_internships else [])

    def match_config(self) -> MatchConfig:
        return MatchConfig(
            high_threshold=self.high_threshold,
            medium_threshold=self.medium_threshold,
            preferred_locations=self.preferred_locations,
            accepted_remote_scopes=self.accepted_remote_scopes,
            strict_location=self.strict_location,
            extra_negative_titles=self.negative_titles(),
            exclude_work_auth_required=self.exclude_work_auth_required,
            home_countries=self.home_countries,
        )

    def secrets(self) -> list[str]:
        return [s for s in (self.r2_access_key_id, self.r2_secret_access_key, self.telegram_bot_token,
                            self.usajobs_api_key, self.adzuna_app_key, self.jooble_api_key,
                            self.jsearch_api_key, self.cloudflare_ai_token, self.francetravail_client_secret) if s]


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
    s.exclude_internships = env_bool("EXCLUDE_INTERNSHIPS", s.exclude_internships)
    s.exclude_work_auth_required = env_bool("EXCLUDE_WORK_AUTH_REQUIRED", s.exclude_work_auth_required)
    s.home_countries = env_list("HOME_COUNTRIES", s.home_countries)

    s.telegram_bot_token = env_str("TELEGRAM_BOT_TOKEN")
    s.telegram_chat_id = env_str("TELEGRAM_CHAT_ID")
    s.telegram_delay_s = max(1.2, env_float("TELEGRAM_DELAY_SECONDS", s.telegram_delay_s))
    s.alert_format = (env_str("ALERT_FORMAT", s.alert_format) or s.alert_format).lower()
    if s.alert_format not in ("digest", "individual"):
        raise ValueError(f"ALERT_FORMAT must be 'digest' or 'individual', got {s.alert_format!r}")

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
    s.jobspy_linkedin_fetch_description = env_bool("JOBSPY_LINKEDIN_FETCH_DESCRIPTION", s.jobspy_linkedin_fetch_description)
    s.adzuna_app_id = env_str("ADZUNA_APP_ID")
    s.adzuna_app_key = env_str("ADZUNA_APP_KEY")
    s.adzuna_countries = [c.lower() for c in env_list("ADZUNA_COUNTRIES", s.adzuna_countries)]
    s.jooble_api_key = env_str("JOOBLE_API_KEY")
    s.francetravail_client_id = env_str("FRANCETRAVAIL_CLIENT_ID")
    s.francetravail_client_secret = env_str("FRANCETRAVAIL_CLIENT_SECRET")
    s.jooble_locations = env_list("JOOBLE_LOCATIONS", s.jooble_locations)
    s.jooble_requests_per_run = env_int("JOOBLE_REQUESTS_PER_RUN", s.jooble_requests_per_run)
    s.adzuna_requests_per_run = env_int("ADZUNA_REQUESTS_PER_RUN", s.adzuna_requests_per_run)
    s.jobspy_locations_per_run = max(1, env_int("JOBSPY_LOCATIONS_PER_RUN", s.jobspy_locations_per_run))
    s.reliefweb_appname = env_str("RELIEFWEB_APPNAME")
    s.cloudflare_ai_token = env_str("CLOUDFLARE_AI_TOKEN")
    s.cloudflare_account_id = env_str("CLOUDFLARE_ACCOUNT_ID")
    s.ai_model = env_str("AI_MODEL", s.ai_model)
    s.ai_reviews_per_run = max(0, env_int("AI_REVIEWS_PER_RUN", s.ai_reviews_per_run))
    s.ai_veto_possible = env_bool("AI_VETO_POSSIBLE", s.ai_veto_possible)
    s.candidate_profile = env_str("CANDIDATE_PROFILE", s.candidate_profile) or ""
    s.weekly_summary = env_bool("WEEKLY_SUMMARY", s.weekly_summary)
    s.sponsor_registers = env_bool("SPONSOR_REGISTERS", s.sponsor_registers)
    s.visa_paths = env_bool("VISA_PATHS", s.visa_paths)
    s.visa_penalty = max(0, min(30, env_int("VISA_PENALTY", s.visa_penalty)))
    s.prospects_per_run = max(0, env_int("PROSPECTS_PER_RUN", s.prospects_per_run))
    s.my_languages = env_list("MY_LANGUAGES", s.my_languages)
    s.my_skills = env_list("MY_SKILLS", s.my_skills)
    s.monthly_radar = env_bool("MONTHLY_RADAR", s.monthly_radar)
    s.market_signals = env_bool("MARKET_SIGNALS", s.market_signals)
    s.jsearch_api_key = env_str("JSEARCH_API_KEY")
    s.jsearch_requests_per_run = max(0, env_int("JSEARCH_REQUESTS_PER_RUN", s.jsearch_requests_per_run))
    s.jsearch_queries = env_list("JSEARCH_QUERIES", s.jsearch_queries)
    s.feeds_enabled = env_list("FEEDS_ENABLED", s.feeds_enabled)
    s.disabled_backends = env_list("DISABLED_BACKENDS", s.disabled_backends)

    s.sources_file = env_str("SOURCES_FILE", s.sources_file)
    s.sources = load_sources(s.sources_file)
    if s.medium_threshold > s.high_threshold:
        raise ValueError("MEDIUM_MATCH_THRESHOLD must not exceed HIGH_MATCH_THRESHOLD")
    return s
