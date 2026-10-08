import tomllib
from pydantic import BaseModel, Field
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent

class Retention(BaseModel):
    """How long each kind of data is kept. Applied only by `python -m capture prune --apply`."""
    raw_events_days: int = Field(default=90, gt=0)
    past_schedule_days: int = Field(default=90, gt=0)
    compile_log_days: int = Field(default=180, gt=0)
    llm_trace_days: int = Field(default=180, gt=0)
    heartbeat_days: int = Field(default=400, gt=0)


class DataQuality(BaseModel):
    heartbeat_interval: float = Field(default=30.0, gt=0)         # collector/ingestor touch their file this often
    heartbeat_sample_interval: float = Field(default=60.0, gt=0)  # the server records their state this often
    heartbeat_stale_after: float = Field(default=90.0, gt=0)      # a file older than this means "not running"
    waking_start: str = "07:00"                                   # gaps are only looked for inside these hours (IST)
    waking_end: str = "23:00"
    min_gap_minutes: int = Field(default=15, gt=0)
    snapshot_time: str = "23:55"                                  # schedule snapshot, taken once the day is over
    day_record_after: str = "00:20"                               # day record waits for the sessionizer to settle
    catchup_days: int = Field(default=7, gt=0)                    # how many missed days a restart will fill in


class Memory(BaseModel):
    """Persistent-file rules. Every number here is a starting point to be tuned by experiment."""
    excitement: float = Field(default=1.0, ge=0.1, le=2.0)  # how eagerly the system counts something as a pattern (not used until patterns exist)
    settle_days: int = Field(default=3, gt=0)               # separate days a provisional entry must be seen before it counts
    cap_tokens: int = Field(default=1500, gt=0)             # live entries beyond this are evicted (least evidence, longest unconfirmed)
    evict_first: list[str] = ["goals", "people"]            # sections whose entries go before the always-on ones
    note_similarity_cutoff: float = Field(default=0.40, ge=0.0, le=1.0)


# One attempt waits at most this long; a call may make two attempts (the chosen model, then the fallback), so the worst case
# is twice this plus a moment. The extension waits 130 s for an approved call (MODEL_CALL_TIMEOUT_MS in
# extension1/shared/api.js) and the server must give up first, or the panel reports a failure while the server keeps
# running (and spending) the call. A test ties the numbers together.
MAX_ATTEMPT_TIMEOUT = 60.0


class Llm(BaseModel):
    """Free-tier discipline. The binding constraint is the provider's request/token allowance, not money, and the
    advertised allowance is not trusted: start low, raise it here once the AI Studio dashboard shows real headroom."""
    daily_call_cap: int = Field(default=25, ge=0)       # model calls per PROVIDER day (midnight Pacific, see llm/quota.py), failed ones included
    background_enabled: bool = False                    # extraction, pattern naming, ...: off until the privacy layer exists (plan §3.9)
    max_item_tokens: int = Field(default=500, gt=0)     # no single context item may exceed this (context/gate.py)
    attempt_timeout: float = Field(default=45.0, gt=0, le=MAX_ATTEMPT_TIMEOUT)  # seconds one attempt waits; replies take 2-30 s normally, but stall for minutes when the service is overloaded
    fallback_model: str = "gemini-3.1-flash-lite"      # tried once when the chosen model is overloaded (503) or silent; "" turns the fallback off


class Patterns(BaseModel):
    """Code-only observation thresholds; starting values to tune against real history."""
    lookback_days: int = Field(default=90, gt=0)
    min_occurrences: int = Field(default=3, gt=0)
    question_after_misses: int = Field(default=2, gt=0)
    drop_after_misses: int = Field(default=3, gt=0)
    max_observations: int = Field(default=3, gt=0)
    focus_hour_min_seconds: int = Field(default=1800, ge=0)
    low_intentional_seconds: int = Field(default=1800, ge=0)


class Privacy(BaseModel):
    """Privacy controls for data leaving the machine."""
    deny_sources: list[str] = Field(default_factory=list)
    deny_tags: list[str] = Field(default=["journal"])

class Cloud(BaseModel):
    """The nightly job runs in a scheduled GitHub Action instead of on the laptop (docs: the cloud plan)."""
    nightly_in_action: bool = False     # true: the laptop server skips the nightly workers (review, daily record, patterns, extractor)
    retry_attempts: int = Field(default=2, ge=1, le=3)   # model attempts for the schedule step inside one run (1 retry = 2)


class Config(BaseModel):
    poll_interval: float = Field(gt=0)
    idle_threshold: float = Field(gt=0)
    max_chunk: float = Field(gt=0)
    spool_dir: Path
    rotate_mb: int = Field(gt=0)
    rotate_minutes: int = Field(gt=0)
    ingest_interval: float = Field(default=10.0, gt=0)
    session_interval: float = Field(default=600.0, gt=0)
    title_blocklist_apps: list[str] = []
    # Added to the built-in sensitive domains (capture/privacy.py): for these,
    # the browser reader keeps the domain but drops the page title.
    title_blocklist_domains: list[str] = []
    # Defaults to monitoring.json beside the spool directory.
    monitoring_file: Path | None = None
    retention: Retention = Retention()
    data_quality: DataQuality = DataQuality()
    memory: Memory = Memory()
    llm: Llm = Llm()
    patterns: Patterns = Patterns()
    privacy: Privacy = Privacy()
    cloud: Cloud = Cloud()


def _flag(name: str) -> bool | None:
    value = os.environ.get(name)
    return None if value is None or value == "" else value.strip().lower() in ("1", "true", "yes", "on")


def load_config() -> Config:
    path = Path(os.environ.get("AGENT_CONFIG", ROOT / "config.toml"))
    with open(path, "rb") as f:
        cfg = Config(**tomllib.load(f))

    # The Action turns these on without editing the committed file.
    if (flag := _flag("AGENT_BACKGROUND_ENABLED")) is not None:
        cfg.llm.background_enabled = flag
    if (flag := _flag("AGENT_NIGHTLY_IN_ACTION")) is not None:
        cfg.cloud.nightly_in_action = flag

    if not cfg.spool_dir.is_absolute():
        cfg.spool_dir = (path.parent / cfg.spool_dir).resolve()

    if cfg.monitoring_file is None:
        cfg.monitoring_file = cfg.spool_dir.parent / "monitoring.json"
    elif not cfg.monitoring_file.is_absolute():
        cfg.monitoring_file = (path.parent / cfg.monitoring_file).resolve()

    return cfg
