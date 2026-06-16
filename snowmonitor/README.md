# SnowMonitor

A focused, production-ready Snowflake monitoring + control app: **cost, task graphs,
security, proactive/reactive alerting**, and **guarded admin controls** (warehouse
timeouts, Cortex limits), with first-class **ALFA / Trexis** company segregation.

Built deliberately small and testable — the logic that matters (segregation, cost
formulas, alert engine, anomaly detection, control SQL) is pure Python with **66 unit
tests**; the UI is a thin Streamlit layer over it.

## What it does

- **Overview** — MTD spend, today vs 7-day average, failed tasks/logins, live alert
  count, spend trend, top warehouses, active alert feed.
- **Cost** — breakdown by **warehouse, database, schema, user, role, query type, and
  application**. Warehouse cost is exact; the rest is *allocated* by query elapsed-time
  share and labeled as an estimate. Plus storage cost by database. Reads a
  pre-aggregated **mart** when deployed (fast/cheap), else live.
- **Task Graphs** — per-root-task rollup of runs, failures, durations; failures-only view.
- **Security** — failed logins, users genuinely lacking MFA (SSO/key-pair users excluded
  via LOGIN_HISTORY cross-check), recent grants.
- **Alerts** — proactive (budget pacing, spend spike, storage growth, **per-warehouse
  anomaly detection**) + reactive (failed tasks/queries, queueing, spill, failed logins,
  MFA gaps, grants). Written to an **alert ledger** (history, run-count, ack). Generates
  real Snowflake `ALERT` SQL for server-side email.
- **Controls** — **guarded** state-changing actions:
  - **Warehouse timeouts** — set `STATEMENT_TIMEOUT_IN_SECONDS` and
    `STATEMENT_QUEUED_TIMEOUT_IN_SECONDS` (runaway/queue control).
  - **Cortex limits** — turn Cortex on/off per role (`SNOWFLAKE.CORTEX_USER`) and restrict
    models (`CORTEX_MODELS_ALLOWLIST`).
  Every action shows current state, the SQL, and a **rollback** statement. **Safe by
  default: generate-only** — copy SQL and run as an operator. Enable in-app execution via
  `CONTROLS_ENABLED` + `CONTROLS_OPERATOR_ROLES`; execution then requires typed
  confirmation and writes an audit row to `ACTION_AUDIT`.

## Company segregation (ALFA + Trexis)

One deterministic place — `lib/company.py` — with literal matching (no LIKE-wildcard
ambiguity). Trexis is an explicit allow-list (`WH_TRXS_*` warehouses, `TRXS_`/`_TRXS_`
databases, `TRXS_` users); **ALFA is the default catch-all**; account-level rows with no
context are **Unclassified** (never silently ALFA). Python classifier and Snowflake
`CASE`/`WHERE` SQL stay in lock-step. Company selector defaults to **ALFA**; `ALL` shows both.
Edit the rules (and rates, thresholds, control bounds) in `config.py` only.

## Server-side objects (recommended)

`setup/setup.sql` creates the cost marts (+ hourly refresh task), the alert ledger, the
app error log, and the control-action audit. The app auto-detects them; until deployed it
runs live-only with no errors. Run as a role with `CREATE` on the monitoring DB +
`IMPORTED PRIVILEGES` on `SNOWFLAKE`. Edit DB/schema/warehouse names at the top first.

## Run it

- **Streamlit-in-Snowflake (recommended):** point a Streamlit app at `app.py`; add
  `streamlit`, `pandas`, `snowflake-snowpark-python` via the packages picker. Needs a role
  with access to `SNOWFLAKE.ACCOUNT_USAGE`.
- **Community Cloud / local:** `pip install -r requirements.txt`, add a
  `[connections.snowflake]` secret, then `streamlit run app.py`.

## Tests

```
python -m unittest discover -s tests
```

## Layout

```
app.py                 # entry: sidebar scope (ALFA default), nav, access gate, dispatch
config.py              # SINGLE source of truth: companies, rates, thresholds, control flags
lib/
  company.py           # ALFA/Trexis segregation (Python + SQL, in lock-step)
  formulas.py          # rates + credit/cost/allocation math
  queries.py           # company-scoped ACCOUNT_USAGE SQL builders
  metrics.py           # gathers numbers for Overview + alert engine
  alerts.py            # proactive/reactive engine + generated ALERT SQL
  anomaly.py           # per-entity baseline anomaly detection
  controls.py          # guarded control-action SQL (timeouts, Cortex) + rollback + audit
  mart.py              # mart-first reads (auto-detected)
  ledger.py            # alert history + acknowledgment
  observability.py     # error logging + access/operator gating
  session.py           # Snowflake session + tiered, guarded query cache
sections/              # one module per page (thin UI over lib/)
setup/setup.sql        # marts, ledger, app log, action audit, refresh task
tests/                 # 66 unit tests for the logic that matters
```

## Access control & safety

`config.py`: `ALLOWED_VIEWER_ROLES` gates who can view; `ROLE_COMPANY_LOCK` pins a role to
one company. Controls are off by default and, when enabled, restricted to
`CONTROLS_OPERATOR_ROLES` with typed confirmation + audit. The app's role needs the
relevant privileges to *execute* controls (e.g. MODIFY on a warehouse, ACCOUNTADMIN for
Cortex account params) — otherwise use generate-only mode and run the SQL as an operator.

## Notes & limits

- `ACCOUNT_USAGE` lags (~45 min, up to ~3h); the UI labels this.
- Non-warehouse cost is an allocation, not exact billing — surfaced honestly.
- See `VALIDATION.md` for the first-run punch-list of account-specific assumptions.
