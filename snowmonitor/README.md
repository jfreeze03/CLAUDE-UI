# SnowMonitor

A focused, production-ready Snowflake monitoring app: **cost, task graphs, security, and
proactive + reactive alerting**, with first-class **ALFA / Trexis** company segregation.

Built deliberately small and testable — the opposite of a sprawling dashboard. The logic that
matters (company segregation, cost formulas, the alert engine) is pure Python with unit tests;
the UI is a thin Streamlit layer over it.

## What it does

- **Overview** — MTD spend, today vs 7-day average, failed tasks/logins, live alert count, spend
  trend, top warehouses, and the active alert feed.
- **Cost** — breakdown by **warehouse, database, schema, user, role, query type, and application**.
  Warehouse cost is exact (warehouse-hour metering); everything else is *allocated* by query
  elapsed-time share and labeled as an estimate. Plus storage cost by database.
- **Task Graphs** — per-graph (root task) rollup of runs, failures, and durations, with a
  failures-only recent-run view.
- **Security** — failed logins, enabled password users lacking MFA, and recent privilege grants.
- **Alerts** — proactive (budget pacing forecast, daily spend spike, storage growth, and
  **per-warehouse anomaly detection** vs each warehouse's own trailing baseline) and reactive
  (failed tasks, failed-query rate, queueing, remote spill, failed logins, MFA gaps, grant volume).
  Fired alerts are written to an **alert ledger** (history, run-count, acknowledge), and the page
  generates real **Snowflake `ALERT` object SQL** so the same checks run server-side and email you
  with the app closed.

## Server-side objects (recommended)

`setup/setup.sql` creates four objects in a database you control and makes the app faster and
stateful. The app **auto-detects** them — until deployed it runs live-only with no errors:

- **Cost marts** (`MART_WAREHOUSE_COST_DAILY`, `MART_QUERY_ATTR_DAILY`) refreshed hourly by a task,
  so Cost/Overview read pre-aggregated daily data instead of scanning `ACCOUNT_USAGE` on every page
  load — much faster and cheaper (a cost tool shouldn't be a cost problem). Per-warehouse anomaly
  detection also uses the mart's daily grain.
- **`ALERT_LEDGER`** — alert history with acknowledgment.
- **`APP_LOG`** — render-error log, so failures are visible off-box instead of silently swallowed.

Run it once as a role with `CREATE` on the monitoring database and `IMPORTED PRIVILEGES` on
`SNOWFLAKE`. Edit the database/schema/warehouse names at the top of the file first.

## Access control (optional)

In `config.py`: `ALLOWED_VIEWER_ROLES` restricts who can open the app; `ROLE_COMPANY_LOCK` pins a
role to one company and hides the picker. Both are empty by default (access governed by who can run
the app).

## Company segregation (ALFA + Trexis)

The previous tool had no clean way to split the two companies. SnowMonitor solves it in one place,
`lib/company.py`, with deterministic **literal** matching (no `LIKE` wildcard ambiguity):

- **Trexis** is an explicit allow-list: warehouses `WH_TRXS_*`, databases starting `TRXS_` or
  containing `_TRXS_`, and users starting `TRXS_`.
- **ALFA is the default catch-all**: any object with context that is *not* Trexis.
- **Unclassified**: account-level rows with no warehouse/database/user context — never silently
  folded into ALFA.

Rules live in `config.py`; `lib/company.py` turns them into both a Python classifier and the
identical Snowflake `CASE` / `WHERE` SQL, so attribution in the database matches attribution in
the app. `Company` selector defaults to **ALFA**; `ALL` shows both.

To change the rules (warehouse names, prefixes, rates, thresholds), edit `config.py` only.

## Rates & thresholds

Set in `config.py`: `$3.68`/credit, `$2.20`/AI credit, `$23.00`/TB-month, plus all alert
thresholds (budget, spike %, queueing, failed logins, etc.). Nothing else hardcodes a rate.

## Run it

**Streamlit-in-Snowflake (recommended):** create a Streamlit app, add `streamlit`, `pandas`,
`snowflake-snowpark-python` via the packages picker, and point it at `app.py`. The app uses the
native session and needs a role with access to `SNOWFLAKE.ACCOUNT_USAGE`
(`IMPORTED PRIVILEGES` on the `SNOWFLAKE` database).

**Community Cloud / local:** `pip install -r requirements.txt`, add a `[connections.snowflake]`
secret, then `streamlit run app.py`.

## Tests

Pure-logic modules are unit-tested (segregation, formulas, alert engine, query shapes):

```
python -m unittest discover -s tests
```

## Layout

```
app.py                 # entry: sidebar scope (ALFA default), nav, dispatch
config.py              # SINGLE source of truth: companies, rates, thresholds
lib/
  company.py           # ALFA/Trexis segregation (Python + SQL, in lock-step)
  formulas.py          # rates + credit/cost/allocation math
  queries.py           # company-scoped ACCOUNT_USAGE SQL builders
  metrics.py           # gathers the numbers for Overview + alert engine
  alerts.py            # proactive/reactive engine + generated ALERT SQL
  session.py           # Snowflake session + tiered, guarded query cache
sections/              # one module per page (thin UI over lib/)
tests/                 # unit tests for the logic that matters
```

## Notes & limits

- `SNOWFLAKE.ACCOUNT_USAGE` lags (~45 min, up to ~3h for some views); the UI labels this.
- Non-warehouse cost is an allocation, not exact billing — surfaced honestly.
- The generated `ALERT` SQL requires a configured email **notification integration** in your account.
