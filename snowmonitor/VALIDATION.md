# SnowMonitor — First-Run Validation Punch-List

Everything in SnowMonitor that depends on *your* account, in one place. Work top to
bottom on a **read-only** connection. Each item lists the assumption, where it lives,
my confidence, and how to verify/fix. Confidence: **[H]** standard Snowflake, unlikely
to differ · **[M]** varies by account/edition · **[A]** account-specific, you must set.

---

## 0. Prerequisites (before the app shows any data)

- [ ] **ACCOUNT_USAGE access** — the app's role needs `IMPORTED PRIVILEGES` on the
      `SNOWFLAKE` database. Verify: `SELECT COUNT(*) FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY;`
      returns without error. **[H]**
- [ ] **Connection** — SiS native session, or a `[connections.snowflake]` secret for
      Community Cloud/local (`lib/session.py`). **[A]**

---

## 1. Account config you MUST set (`config.py`) — **[A]**

These are my best reconstruction from your old app. Wrong values here = wrong
attribution, silently. Confirm each against your account.

- [ ] **Trexis warehouses** — `COMPANIES["Trexis"]["warehouses"]`
      (`WH_TRXS_LOAD`, `WH_TRXS_QUERY`, `WH_TRXS_TRANSFORM`, `WH_TRXS_UNLOAD`).
      Verify: `SHOW WAREHOUSES;` — do these names match exactly?
- [ ] **Trexis DB/user keys** — `db_prefixes` (`TRXS_`), `db_contains` (`_TRXS_`),
      `user_prefixes` (`TRXS_`). Verify: `SHOW DATABASES;` and `SHOW USERS;` — do Trexis
      objects actually start with / contain these?
- [ ] **ALFA prod databases** — `COMPANIES["ALFA"]["prod_dbs"]`
      (`ALFA_EDW_PROD`, `ALFA_EDW_MGM`) and `db_prefixes` (`ALFA_`, `ADMIN`).
- [ ] **Environment suffixes** — Trexis `_PRD` / `_DEV` / `_SIT`. Confirm your prod
      databases really end in `_PRD`.
- [ ] **Sanity check the whole rule at once:** run the query in §6.1 and eyeball the
      ALFA / Trexis / Unclassified split. A large `Unclassified` bucket means the rules
      miss real objects.

- [ ] **Rates** — `CREDIT_PRICE_USD` 3.68, `AI_CREDIT_PRICE_USD` 2.20,
      `STORAGE_COST_PER_TB_USD` 23.00. Confirm against your contract.
- [ ] **Budget & thresholds** — `THRESHOLDS["monthly_budget_usd"]` (50k placeholder) and
      the rest. Tune to your reality so alerts mean something.
- [ ] **Monitoring objects** — `MONITORING_DATABASE` / `MONITORING_SCHEMA`, and
      `MONITOR_WH` + DB/schema in `setup/setup.sql`. Set to objects you control.
- [ ] **Alerting** — `NOTIFICATION_INTEGRATION`, `DEFAULT_ALERT_RECIPIENTS` (only needed
      for the generated server-side `ALERT` SQL).

---

## 2. ACCOUNT_USAGE columns — confirm they exist for your role/edition

Each page runs a query in `lib/queries.py`. If a column is missing/renamed in your
account, that page warns and shows empty (it won't crash). Validate by opening each page.

- [ ] **Warehouse metering** (`warehouse_cost_sql`, `daily_spend_sql`) —
      `WAREHOUSE_METERING_HISTORY`: `credits_used`, `credits_used_compute`,
      `credits_used_cloud_services`, `warehouse_name`, `start_time`. **[H]**
- [ ] **Query attribution** (`cost_by_dimension_sql`) — `QUERY_HISTORY`: `warehouse_name`,
      `database_name`, `schema_name`, `user_name`, `role_name`, `query_type`,
      `execution_time`, `start_time`. **[H]**
- [ ] **Storage** (`storage_by_database_sql`) — `DATABASE_STORAGE_USAGE_HISTORY`:
      `database_name`, `average_database_bytes`, `average_failsafe_bytes`, `usage_date`. **[H]**
- [ ] **Tasks** (`task_runs_sql`, `task_graph_sql`) — `TASK_HISTORY`: `name`,
      `database_name`, `schema_name`, `state`, `scheduled_time`, `query_start_time`,
      `completed_time`, `error_code`, `error_message`, `root_task_id`. **[M]** — confirm
      `root_task_id` is populated (graphs collapse to single tasks if not).
- [ ] **Logins** (`failed_logins_sql`) — `LOGIN_HISTORY`: `event_timestamp`, `user_name`,
      `is_success`, `error_message`, `client_ip`, `reported_client_type`,
      `first_authentication_factor`, `second_authentication_factor`. **[H]**
- [ ] **Users / MFA** (`users_without_mfa_sql`) — `USERS`: `name`, `email`, `default_role`,
      `last_success_login`, `disabled`, `has_password`, `ext_authn_duo`,
      `has_rsa_public_key`, `deleted_on`. **[M]** — `ext_authn_duo` / `has_rsa_public_key`
      exist on current accounts but confirm.
- [ ] **Grants** (`recent_grants_sql`) — `GRANTS_TO_ROLES`: `created_on`, `privilege`,
      `granted_on`, `name`, `granted_to`, `grantee_name`, `granted_by`, `deleted_on`. **[H]**
- [ ] **Applications** (`application_cost_sql`) — `SESSIONS`: `session_id`,
      `client_application_name`. **[M]** — see §4.

---

## 3. Value-string assumptions — the items most likely to differ

These compare against literal strings. If your account uses different values, the
metric silently reads zero. Confirm with the probe queries.

- [ ] **Query failure status** = `'FAIL'` (`metrics.py`, `failed_query_rate`). **[H]**
      Probe: `SELECT DISTINCT execution_status FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
      WHERE start_time > DATEADD('day',-2,CURRENT_TIMESTAMP());`
- [ ] **Spill column** = `bytes_spilled_to_remote_storage` (`metrics.py`). **[H]**
- [ ] **Queue columns** = `queued_overload_time`, `queued_provisioning_time` (`metrics.py`). **[H]**
- [ ] **Task state** = `'FAILED'` / `'SUCCEEDED'` (`queries.py`, `metrics.py`). **[H]**
      Probe: `SELECT DISTINCT state FROM SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY
      WHERE scheduled_time > DATEADD('day',-7,CURRENT_TIMESTAMP());`
- [ ] **Login success** = `'YES'` / `'NO'` (`queries.py`, `metrics.py`). **[H]**
- [ ] **⚠ Auth factor strings** (`users_without_mfa_sql`) — `'PASSWORD'`, and the SSO/
      key-pair set `'SAML_2_0','OAUTH','OAUTH_ACCESS_TOKEN','KEY_PAIR','RSA_PUBLIC_KEY'`.
      **[M] — the single most likely mismatch.** Probe:
      `SELECT first_authentication_factor, second_authentication_factor, COUNT(*)
       FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
       WHERE event_timestamp > DATEADD('day',-30,CURRENT_TIMESTAMP())
       GROUP BY 1,2 ORDER BY 3 DESC;`
      Then update the `sso_or_keypair` tuple in `users_without_mfa_sql` to match the real
      values. (One clearly-commented line.)

---

## 4. Performance & cost (a cost tool shouldn't cost much)

- [ ] **Live allocation cost** — `cost_by_dimension_sql` / `application_cost_sql` scan
      `QUERY_HISTORY` with a window function. On a large account/long window this is slow
      and not free. **Deploy `setup/setup.sql`** so the app reads the mart instead; the
      Cost page shows "⚡ Reading from pre-aggregated mart" when active. **[M]**
- [ ] **Mart backfill is heavy** — `setup.sql` calls `SP_REFRESH_MART(90)` once
      (90-day backfill). Run it **off-hours**; the hourly task only refreshes 3 days. **[M]**
- [ ] **SESSIONS retention is short** — the Applications tab joins `QUERY_HISTORY` to
      `SESSIONS`, which Snowflake retains for a limited window. Over long ranges most
      rows won't join (undercount). Treat Applications as a recent-window view. **[M]**

---

## 5. Server-side objects (after §1–3 pass)

- [ ] Run `setup/setup.sql` as a role with `CREATE` on the monitoring DB + ACCOUNT_USAGE
      access. Edit the DB/schema/`MONITOR_WH` at the top first.
- [ ] Confirm the marts populate: `SELECT COUNT(*), MAX(usage_date) FROM
      SNOWMONITOR_DB.PUBLIC.MART_WAREHOUSE_COST_DAILY;`
- [ ] Confirm the task is running: `SHOW TASKS LIKE 'TASK_REFRESH_MART';` (state = started).
- [ ] App auto-detects the marts, the ledger, and `APP_LOG` — no code change needed.
- [ ] For real email alerts, set a notification integration and run the generated SQL on
      the Alerts page.

---

## 6. Reconciliation — does the data look *right*?

Numbers can be schema-valid but wrong. Spot-check:

- [ ] **6.1 Company split** — paste `lib/company.company_case_sql()` into a GROUP BY over
      `WAREHOUSE_METERING_HISTORY` (warehouse-only) and `QUERY_HISTORY` (all three signals)
      and confirm ALFA/Trexis/Unclassified totals look sane. Large `Unclassified` ⇒ fix §1.
- [ ] **6.2 Spend reconciliation** — compare SnowMonitor MTD spend to Snowsight's cost
      view for the same window. Large gap ⇒ wrong rate or scope.
- [ ] **6.3 MFA list** — are flagged users actually unprotected? If known SSO users appear,
      fix the factor strings (§3).
- [ ] **6.4 Task failures** — do the failures shown match what you know failed?
- [ ] **6.5 Alerts** — do fired alerts make sense, or are thresholds (§1) mis-tuned?

---

## Fastest path

1. §0 + §1 (set config) → open the app, click every page, note any empty/warning panel.
2. Run the §3 probe queries; fix any string mismatches (mostly the auth factors).
3. §6.1 + §6.2 reconciliation.
4. Deploy `setup/setup.sql` (§5) for speed + history.

Most fixes are one line in `config.py` or `lib/queries.py`. Nothing here requires a
rewrite — the structure already handles missing data gracefully.
