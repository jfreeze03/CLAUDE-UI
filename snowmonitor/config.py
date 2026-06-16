"""SnowMonitor configuration — the single source of truth.

Everything that defines *what we monitor and how we price it* lives here:
companies (ALFA + Trexis), billing rates, environment mapping, and alert
thresholds. Nothing else in the app hardcodes a rate, a warehouse name, or a
threshold — they import from here. That is the discipline that keeps a
monitoring tool trustworthy as it grows.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Billing rates (USD). Override per-deployment via Streamlit secrets if needed.
# --------------------------------------------------------------------------
CREDIT_PRICE_USD: float = 3.68          # per compute credit
AI_CREDIT_PRICE_USD: float = 2.20       # per Cortex AI credit
STORAGE_COST_PER_TB_USD: float = 23.00  # per TB-month

# --------------------------------------------------------------------------
# Companies. ALFA is the DEFAULT and the catch-all; Trexis is identified by an
# explicit allow-list of warehouses, database patterns, and a user prefix.
# This is the clean segregation rule the previous tool lacked: one place,
# deterministic, and ALFA-by-default so nothing is silently misattributed.
# --------------------------------------------------------------------------
DEFAULT_COMPANY: str = "ALFA"

# Rules use LITERAL matching (prefix/suffix/contains), not SQL LIKE wildcards, so
# there is zero ambiguity about underscores or escaping. company.py turns these
# into Snowflake STARTSWITH / ENDSWITH / CONTAINS predicates and the parallel
# Python checks, guaranteeing the SQL and Python classifications agree.
COMPANIES: dict[str, dict] = {
    "ALFA": {
        "label": "ALFA",
        "color": "#38bdf8",
        # ALFA is the catch-all (any account object that is NOT Trexis, with
        # context, is ALFA). These positively-identify ALFA for env mapping.
        "db_prefixes": ["ALFA_", "ADMIN"],
        "prod_dbs": ["ALFA_EDW_PROD", "ALFA_EDW_MGM"],  # exact, PROD
    },
    "Trexis": {
        "label": "Trexis",
        "color": "#c084fc",
        # Trexis is an explicit allow-list: exact warehouses + literal db/user keys.
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

# Value used when a row cannot be confidently assigned to either company.
# We never silently fold the unknown into ALFA — it is surfaced honestly.
UNCLASSIFIED_LABEL: str = "Unclassified"

# Environment options. "ALL" means no environment narrowing.
ENVIRONMENTS: tuple[str, ...] = ("ALL", "PROD", "DEV")
DEFAULT_ENVIRONMENT: str = "ALL"

# --------------------------------------------------------------------------
# Alert thresholds. Proactive alerts fire BEFORE a budget/SLA is breached;
# reactive alerts fire on current failures. All tunable here.
# --------------------------------------------------------------------------
THRESHOLDS: dict[str, float] = {
    # Cost (proactive)
    "daily_spend_spike_pct": 50.0,      # today's spend vs trailing-7d average
    "monthly_budget_usd": 50_000.0,     # used for pacing/forecast overage
    "budget_pacing_warn_pct": 90.0,     # forecast month-end spend >= this % of budget
    "storage_growth_warn_pct": 20.0,    # storage TB growth vs prior window
    # Performance (proactive/reactive)
    "queued_queries_warn": 25,          # concurrently queued queries now
    "long_running_query_min": 30,       # query elapsed minutes
    "remote_spill_gb_warn": 50.0,       # remote spill in window
    "failed_query_rate_warn_pct": 5.0,  # failed / total queries in window
    # Tasks (reactive)
    "failed_task_runs_warn": 1,         # any failed task run in window
    "task_duration_spike_pct": 100.0,   # run duration vs task's trailing median
    # Security (reactive)
    "failed_logins_warn": 20,           # failed logins in window
    "users_without_mfa_warn": 1,        # any human user lacking MFA
    "new_grants_warn": 25,              # privilege grants in window (review trigger)
}

# Default lookback windows (days) per view. Kept short so ACCOUNT_USAGE scans
# stay bounded and fast; users can widen in the UI.
DEFAULT_LOOKBACK_DAYS: int = 7
MAX_LOOKBACK_DAYS: int = 90

# Snowflake notification integration name used when generating real ALERT /
# email SQL. Set to the integration configured in your account.
NOTIFICATION_INTEGRATION: str = "OVERWATCH_EMAIL_INT"
DEFAULT_ALERT_RECIPIENTS: str = "data-platform@example.com"

# ACCOUNT_USAGE latency disclosure shown next to delayed metrics.
ACCOUNT_USAGE_FRESHNESS = "Source: SNOWFLAKE.ACCOUNT_USAGE (latency up to ~45 min, up to ~3h for some views)."
INFORMATION_SCHEMA_FRESHNESS = "Source: INFORMATION_SCHEMA (near real-time)."

# --------------------------------------------------------------------------
# Monitoring objects (mart + alert ledger + app error log). These are CREATEd
# by setup/setup.sql in a database/schema you control. The app auto-detects them:
# mart-first reads activate when the mart exists; alert history + error logging
# activate when their tables exist. Until then the app runs live-only, no errors.
# --------------------------------------------------------------------------
MONITORING_DATABASE: str = "SNOWMONITOR_DB"
MONITORING_SCHEMA: str = "PUBLIC"

MART_WAREHOUSE_DAILY = "MART_WAREHOUSE_COST_DAILY"
MART_QUERY_ATTR_DAILY = "MART_QUERY_ATTR_DAILY"
ALERT_LEDGER_TABLE = "ALERT_LEDGER"
APP_LOG_TABLE = "APP_LOG"


def monitoring_fqn(table: str) -> str:
    """Fully-qualified name for a monitoring object."""
    return f"{MONITORING_DATABASE}.{MONITORING_SCHEMA}.{table}"


# --------------------------------------------------------------------------
# Anomaly detection (per-entity baselines for proactive alerts).
# --------------------------------------------------------------------------
ANOMALY_Z_THRESHOLD: float = 2.5      # latest value this many std devs above baseline
ANOMALY_MIN_BASELINE_DAYS: int = 5    # require at least this much history to judge
ANOMALY_MIN_ABS_USD: float = 50.0     # ignore anomalies below this dollar floor (noise)

# --------------------------------------------------------------------------
# Access control (optional). Empty = no app-level gate (rely on who can run the
# app / its Snowflake role). If ALLOWED_VIEWER_ROLES is set, only those current
# roles may view. ROLE_COMPANY_LOCK pins a role to one company and hides the picker.
# --------------------------------------------------------------------------
ALLOWED_VIEWER_ROLES: tuple = ()          # e.g. ("PLATFORM_ADMIN", "FINOPS")
ROLE_COMPANY_LOCK: dict = {}              # e.g. {"TREXIS_VIEWER": "Trexis"}

APP_NAME = "SnowMonitor"
APP_VERSION = "1.1.0"
