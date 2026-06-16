# SnowMonitor — First-Run Validation Punch-List

Everything that depends on *your* account, in one place. Work top to bottom on a
**read-only** connection (then a privileged one for §7 Controls). Confidence:
**[H]** standard Snowflake · **[M]** varies by account/edition · **[A]** you must set.

---

## 0. Prerequisites

- [ ] **ACCOUNT_USAGE access** — role needs `IMPORTED PRIVILEGES` on `SNOWFLAKE`. **[H]**
- [ ] **Connection** — SiS native session, or a `[connections.snowflake]` secret. **[A]**

## 1. Account config you MUST set (`config.py`) — **[A]**

- [ ] **Trexis warehouses** (`WH_TRXS_*`) — confirm via `SHOW WAREHOUSES;`
- [ ] **Trexis db/user keys** (`TRXS_`, `_TRXS_`) — confirm via `SHOW DATABASES;` / `SHOW USERS;`
- [ ] **ALFA prod dbs** (`ALFA_EDW_PROD`, `ALFA_EDW_MGM`) and `db_prefixes`.
- [ ] **Environment suffixes** (`_PRD` / `_DEV` / `_SIT`).
- [ ] **Sanity-check the split** — see §6.1; a large `Unclassified` bucket means rules miss objects.
- [ ] **Rates** (3.68 / 2.20 / 23.00) — confirm against contract.
- [ ] **Budget & thresholds** (`THRESHOLDS`) — tune so alerts mean something.
- [ ] **Monitoring objects** — `MONITORING_DATABASE`/`SCHEMA`, `MONITOR_WH` in `setup.sql`.

## 2. ACCOUNT_USAGE columns — confirm by opening each page (empty/warn = a column differs)

- [ ] `WAREHOUSE_METERING_HISTORY` credits columns **[H]**
- [ ] `QUERY_HISTORY` (attribution + status/spill/queue) **[H]**
- [ ] `DATABASE_STORAGE_USAGE_HISTORY` **[H]**
- [ ] `TASK_HISTORY` — confirm `root_task_id` populated (graphs collapse otherwise) **[M]**
- [ ] `LOGIN_HISTORY` (factors) **[H]**
- [ ] `USERS` — `ext_authn_duo`, `has_rsa_public_key` exist **[M]**
- [ ] `GRANTS_TO_ROLES` **[H]** · `SESSIONS` (`client_application_name`) — see §4 **[M]**

## 3. Value-string assumptions — most likely to differ

- [ ] Query failure `'FAIL'` · task `'FAILED'`/`'SUCCEEDED'` · login `'YES'`/`'NO'` **[H]**
- [ ] **⚠ Auth factor strings** (`users_without_mfa_sql`) — `'PASSWORD'` + the SSO/key-pair set.
      **[M] — the single most likely mismatch.** Probe:
      `SELECT first_authentication_factor, second_authentication_factor, COUNT(*)
       FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
       WHERE event_timestamp > DATEADD('day',-30,CURRENT_TIMESTAMP()) GROUP BY 1,2 ORDER BY 3 DESC;`
      Update the `sso_or_keypair` tuple to match.

## 4. Performance & cost

- [ ] **Deploy `setup/setup.sql`** so Cost/Overview read the mart (Cost page shows
      "⚡ Reading from pre-aggregated mart"). Live allocation scans `QUERY_HISTORY` — slow/costly. **[M]**
- [ ] **Backfill is heavy** — `SP_REFRESH_MART(90)` runs once; do it off-hours. **[M]**
- [ ] **SESSIONS retention is short** — Applications tab is a recent-window view. **[M]**

## 5. Server-side objects

- [ ] Run `setup/setup.sql`; confirm marts populate and `TASK_REFRESH_MART` is started.
- [ ] App auto-detects marts, ledger, app log, action audit — no code change needed.

## 6. Reconciliation — does the data look *right*?

- [ ] **6.1 Company split** — GROUP BY `company.company_case_sql()`; eyeball ALFA/Trexis/Unclassified.
- [ ] **6.2 Spend** — compare MTD to Snowsight cost view. Gap ⇒ wrong rate/scope.
- [ ] **6.3 MFA list** — are flagged users actually unprotected? Known SSO users appearing ⇒ fix §3.
- [ ] **6.4 Task failures / 6.5 Alerts** — do they match reality / are thresholds tuned?

## 7. Controls (run on a PRIVILEGED connection) — **state-changing, test carefully**

Controls are **off by default** (generate-only). To validate execution:

- [ ] **Privileges.** Executing requires the app's role to hold the right grants:
      - Warehouse timeouts: `MODIFY` (or `OWNERSHIP`) on the warehouse, or `MANAGE WAREHOUSES`. **[A]**
      - Cortex access grant/revoke: privilege to grant `SNOWFLAKE.CORTEX_USER` (usually ACCOUNTADMIN). **[A]**
      - Cortex model allowlist: `ACCOUNTADMIN` (`ALTER ACCOUNT`); **`CORTEX_MODELS_ALLOWLIST`
        availability varies by region/edition** — confirm it exists in your account. **[M]**
- [ ] **Generate-only first.** Leave `CONTROLS_ENABLED = False`; open Controls, pick a
      warehouse, confirm the **current timeouts read** (`SHOW PARAMETERS … IN WAREHOUSE`)
      and that the generated `ALTER` + **rollback** SQL look right. Run them manually as operator.
- [ ] **Then enable execution.** Set `CONTROLS_ENABLED = True` and
      `CONTROLS_OPERATOR_ROLES = ("<your_role>",)`. Execution requires typed confirmation and
      writes `ACTION_AUDIT`. Test on a **non-production warehouse** first; verify with the
      rollback SQL that you can revert.
- [ ] **Cortex value strings** — confirm `SNOWFLAKE.CORTEX_USER` is the correct database role
      name in your account, and your real model identifiers for the allowlist.

## 8. Cost-intelligence sources (v1.3) — verify by opening the new tabs/pages

- [ ] **All services** (Cost → All services) — `METERING_DAILY_HISTORY.service_type` /
      `credits_used`. **[H]** Account-total (no company/object grain — by design).
- [ ] **⚠ Cortex** (Cost → Cortex AI) — `CORTEX_FUNCTIONS_USAGE_HISTORY`
      (`model_name`/`function_name`, `tokens`, `token_credits`, `start_time`) and
      `CORTEX_CODE_CLI_USAGE_HISTORY` (`user_name`, `tokens`, `token_credits`, `usage_date`).
      **[M] — most likely to need column/name adjustment; views vary by region/edition.**
      Both fail gracefully (page shows an info message, not a crash) if unavailable.
- [ ] **Chargeback** (Cost → Chargeback) — `QUERY_HISTORY` allocation grouped by the
      company CASE. **[H]**
- [ ] **Storage detail** (Cost → Storage) — `TABLE_STORAGE_METRICS`
      (`active_bytes`, `time_travel_bytes`, `failsafe_bytes`, `table_catalog`, `deleted`). **[H]**
      Falls back to the summary storage query if unavailable.
- [ ] **Recommendations** — `WAREHOUSE_METERING_HISTORY` + `QUERY_HISTORY` (idle %),
      `TABLE_STORAGE_METRICS` (time-travel), `QUERY_HISTORY.query_parameterized_hash`
      (repeated queries). **[H/M]** — confirm `query_parameterized_hash` exists. Savings are
      **estimates**; review each fix SQL before applying.
- [ ] **Query Explorer** — `QUERY_HISTORY` (`bytes_scanned`,
      `bytes_spilled_to_remote_storage`, `execution_status`, `query_text`). **[H]**

## Fastest path

1. §0–§1 → open every page, note empties/warnings.
2. §3 probe (auth factors) → fix mismatches.
3. §6.1 + §6.2 reconciliation.
4. Deploy `setup/setup.sql` (§5).
5. Controls: §7 generate-only → verify → enable execution on a test warehouse.

Most fixes are one line in `config.py` or `lib/queries.py`. Nothing here needs a rewrite.
