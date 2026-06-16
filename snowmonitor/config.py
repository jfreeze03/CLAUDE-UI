"""SnowMonitor configuration — the single source of truth."""

from __future__ import annotations

# Billing rates (USD).
CREDIT_PRICE_USD: float = 3.68
AI_CREDIT_PRICE_USD: float = 2.20
STORAGE_COST_PER_TB_USD: float = 23.00

# Companies. ALFA is the DEFAULT and catch-all; Trexis is an explicit allow-list.
DEFAULT_COMPANY: str = "ALFA"

COMPANIES: dict[str, dict] = {
    "ALFA": {
        "label": "ALFA",
        "color": "#38bdf8",
        "db_prefixes": ["ALFA_", "ADMIN"],
        "prod_dbs": ["ALFA_EDW_PROD", "ALFA_EDW_MGM"],
    },
    "Trexis": {
        "label": "Trexis",
        "color": "#c084fc",
        "warehouses": ["WH_TRXS_LOAD", "WH_TRXS_QUERY", "WH_TRXS_TRANSFORM", "WH_TRXS_UNLOAD"],
        "db_prefixes": ["TRXS_"],
        "db_contains": ["_TRXS_"],
        "user_prefixes": ["TRXS_"],
        "prod_db_suffixes": ["_PRD"],
        "prod_db_contains": ["_PRD_"],
        "dev_db_suffixes": ["_DEV", "_SIT"],
        "dev_db_contains": ["_DEV_", "_SIT_"],
    },
}

UNCLASSIFIED_LABEL: str = "Unclassified"
ENVIRONMENTS: tuple[str, ...] = ("ALL", "PROD", "DEV")
DEFAULT_ENVIRONMENT: str = "ALL"

# Alert thresholds.
THRESHOLDS: dict[str, float] = {
    "daily_spend_spike_pct": 50.0,
    "monthly_budget_usd": 50_000.0,
    "budget_pacing_warn_pct": 90.0,
    "storage_growth_warn_pct": 20.0,
    "queued_queries_warn": 25,
    "long_running_query_min": 30,
    "remote_spill_gb_warn": 50.0,
    "failed_query_rate_warn_pct": 5.0,
    "failed_task_runs_warn": 1,
    "task_duration_spike_pct": 100.0,
    "failed_logins_warn": 20,
    "users_without_mfa_warn": 1,
    "new_grants_warn": 25,
}

DEFAULT_LOOKBACK_DAYS: int = 7
MAX_LOOKBACK_DAYS: int = 90

NOTIFICATION_INTEGRATION: str = "OVERWATCH_EMAIL_INT"
DEFAULT_ALERT_RECIPIENTS: str = "data-platform@example.com"

ACCOUNT_USAGE_FRESHNESS = "Source: SNOWFLAKE.ACCOUNT_USAGE (latency up to ~45 min, up to ~3h for some views)."
INFORMATION_SCHEMA_FRESHNESS = "Source: INFORMATION_SCHEMA (near real-time)."

# Monitoring objects (mart + ledger + app log). Auto-detected when present.
MONITORING_DATABASE: str = "SNOWMONITOR_DB"
MONITORING_SCHEMA: str = "PUBLIC"
MART_WAREHOUSE_DAILY = "MART_WAREHOUSE_COST_DAILY"
MART_QUERY_ATTR_DAILY = "MART_QUERY_ATTR_DAILY"
ALERT_LEDGER_TABLE = "ALERT_LEDGER"
APP_LOG_TABLE = "APP_LOG"


def monitoring_fqn(table: str) -> str:
    """Fully-qualified name for a monitoring object."""
    return f"{MONITORING_DATABASE}.{MONITORING_SCHEMA}.{table}"


# Anomaly detection.
ANOMALY_Z_THRESHOLD: float = 2.5
ANOMALY_MIN_BASELINE_DAYS: int = 5
ANOMALY_MIN_ABS_USD: float = 50.0

# Access control (optional).
ALLOWED_VIEWER_ROLES: tuple = ()
ROLE_COMPANY_LOCK: dict = {}

# --------------------------------------------------------------------------
# Controls (state-changing admin actions: warehouse timeouts, Cortex limits).
# SAFE BY DEFAULT: with CONTROLS_ENABLED = False the Controls page only *generates*
# SQL (with rollback) for you to run as an operator. Set CONTROLS_ENABLED = True
# AND run the app as a role in CONTROLS_OPERATOR_ROLES to allow in-app execution,
# which always requires typed confirmation and writes an audit row.
# --------------------------------------------------------------------------
CONTROLS_ENABLED: bool = False
CONTROLS_OPERATOR_ROLES: tuple = ()           # e.g. ("PLATFORM_ADMIN", "SYSADMIN")
ACTION_AUDIT_TABLE = "ACTION_AUDIT"

WAREHOUSE_TIMEOUT_MIN_S: int = 0
WAREHOUSE_TIMEOUT_MAX_S: int = 172800         # Snowflake max (2 days)

CORTEX_USER_ROLE: str = "SNOWFLAKE.CORTEX_USER"

APP_NAME = "SnowMonitor"
APP_VERSION = "1.6.0"
