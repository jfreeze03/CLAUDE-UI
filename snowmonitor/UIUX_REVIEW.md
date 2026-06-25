# SnowMonitor — Brutal UI/UX Review & Redesign Plan

Reviewer panel: senior product designer + Streamlit/Snowflake architect + UX researcher + perf reviewer.
Scope reviewed: `app.py`, `.streamlit/config.toml`, `lib/compat.py`, all 10 `sections/*.py`, supporting `lib/*`.
Hard constraint discovered in `lib/compat.py`: **this app targets an OLD Streamlit runtime (~1.22)**. That rules out `st.navigation`/`st.Page`, `st.column_config`, `st.segmented_control`, `st.dialog`, `st.toast`. Every recommendation below stays inside that box (HTML/CSS via `st.markdown`, `st.columns`, `st.metric`, and **Altair**, which ships with Streamlit and works in SiS — no new dependency).

---

## 1. Brutal executive assessment

The backend is genuinely strong — the segmentation, formula registry, mart-first reads, anomaly engine, and detection logic are better than most internal tools. **The front end actively undersells it.** Right now this looks like a competent analyst's SQL notebook wearing the default dark Streamlit theme, not a control center a DBA trusts during an incident.

Three things make it feel amateur on sight:

1. **It's a wall of uniform `st.metric` tiles and raw `st.dataframe` dumps with UPPERCASE SQL column names** (`COST_USD`, `PRUNING_PCT`, `MINUTES_SINCE_LAST`). Nothing is formatted as money, percent, or duration in the tables. The eye has no hierarchy — a $40k budget breach and a label like "Unknown cadence" have identical visual weight.
2. **There's a control that lies.** The Environment selector (PROD/DEV/ALL) is wired to nothing — `scope()` returns `env`, and the only place it's used is a caption string in `_common.header`. A DBA who filters to PROD and sees numbers change in their head but not in the data will stop trusting the whole app.
3. **The most urgent page is 9th in the nav.** Order is Overview → Cost → Recommendations → Optimization → Query Explorer → Digest → Task Graphs → Security → **Alerts** → Controls. "Is anything on fire?" is buried below three analysis pages and a CSV explorer. The IA is the order the features were built, not the order a DBA works.

And the thing that will actually hurt you at 12 concurrent users: **`st.tabs` renders all tabs eagerly.** Cost has 8 tabs, Task Graphs has 5. Every visit to Cost executes ~15 ACCOUNT_USAGE queries whether or not the user opens those tabs, because Streamlit runs all tab bodies on every script run. That's your dominant performance risk and it's self-inflicted.

Bottom line: **competent monitoring engine, sub-amateur cockpit.** This is a 1–2 week front-end pass away from looking like a product, and most of the work is reusable components, not per-page fiddling.

---

## 2. Top 10 UX problems (most damaging first)

1. **Dead Environment filter** (`app.py` L63, `_common.py` L23). Appears functional, filters nothing. Either wire it through `company.company_case_sql` (the PROD/DEV logic already exists in `company.py`) or remove it. Leaving it is a trust bug.
2. **Eager `st.tabs` = mass over-fetching** (`cost.py` L23, `tasks.py` L64, `optimize.py` L29, `controls.py` L57). All sub-view queries run every load. Biggest perf and clutter problem.
3. **Raw SQL column dumps as the primary data surface.** Every `st.dataframe(df,...)` shows `USER`, `GB_SCANNED`, `AVG_SEC` with no humanization and no number formatting. Looks like a query result, not a report.
4. **No triage hero / "what do I do now."** Overview shows data; it never says "These 3 things need you, in this order." Alerts live in a narrow right column (`overview.py` L60-70), capped at 8, easy to miss.
5. **Flat, undifferentiated KPI tiles.** `st.metric` everywhere, identical weight, emoji sprinkled inconsistently ("🔴 Stale (overdue)" vs "Failed runs"). No severity color, no grouping, no "this one is bad" signal.
6. **Weak charts.** Everything is `st.bar_chart`/`st.line_chart` — no titles, no axis number formatting ($/GB), no tooltips, default colors. Several pages show a bar_chart AND the same data as a table right below (redundant: `cost.py` dim tab, `tasks.py` cost tab).
7. **Nav is 10 flat buttons with no grouping** (`app.py` L73-78). No sense of Monitor vs Investigate vs Act. Two pages (Recommendations, Optimization) overlap conceptually and sit adjacent without explaining the difference.
8. **Error states leak raw exceptions to users.** `session.run` shows `st.warning("Query failed (standard): <SQL error>")` and the page dispatcher prints `str(exc)[:300]`. Users should never see a Snowflake stack message; they should see "Couldn't load X" with detail tucked behind an expander.
9. **No real loading state.** Caches use `show_spinner=False`, so multi-second pages render blank then pop. No skeleton, no "Loading cost data…". Feels broken/slow even when it's working.
10. **Empty states conflate "good" with "no data."** `st.success("No serverless task cost in range...")` paints absence-of-data green like it's a win. Green should mean "checked, healthy," not "nothing here."

---

## 3. Page-by-page scoring (1–10; Performance = 10 means LOW risk/fast)

| Page | Visual polish | Usability | Info value | Performance (10=fast) | DBA usefulness |
|---|---|---|---|---|---|
| Overview | 5 | 6 | 7 | 6 | 7 |
| Cost | 4 | 4 | 8 | **2** | 8 |
| Recommendations | 4 | 6 | 8 | 6 | 8 |
| Optimization | 4 | 5 | 8 | 4 | 9 |
| Query Explorer | 5 | 7 | 6 | 6 | 7 |
| Digest | 5 | 6 | 7 | 6 | 7 |
| Task Graphs | 4 | 5 | 8 | **3** | 8 |
| Security | 5 | 6 | 8 | 4 | 8 |
| Alerts | 4 | 6 | 7 | 6 | 7 |
| Controls | 5 | 6 | 6 | 7 | 7 |
| **Average** | **4.5** | **5.7** | **7.3** | **4.9** | **7.6** |

Read this honestly: **information value and DBA usefulness are high (7–8); polish and performance are the failures (4.5 / 4.9).** You don't have a content problem. You have a presentation and over-fetching problem. Cost and Task Graphs are the perf emergencies; everything is a polish emergency.

---

## 4. Recommended redesign concept

### 4.1 App shell — grouped nav + density
Keep button-based nav (old runtime), but group it under three sidebar headers so the IA mirrors how a DBA works:

- **MONITOR** — Command Center (new landing), Alerts & Risks
- **INVESTIGATE** — Cost, Performance (rename "Task Graphs" → workloads+tasks+queries live here), Security, Query Explorer
- **ACT** — Optimize (merge Recommendations + Optimization), Controls
- **REPORT** — Executive Digest

Add a compact global status pill at the top of the sidebar: `● 2 Critical · 5 High` colored, click → Command Center. Tighten sidebar typography with one CSS block.

### 4.2 Landing = "Command Center" (replaces today's Overview)
A triage screen, not a dashboard:
- **Hero row:** one sentence — "**2 issues need attention now**" — plus the top 3 ranked issues across ALL domains (cost spike, stale task, ATO detection), each a colored card with severity, one-line why, and a "Investigate →" button that deep-links to the right page with context set.
- **KPI strip (one row, 5–6):** MTD spend + forecast vs budget (with a tiny progress bar), failed tasks, stale pipelines, open alerts, security detections. Each card carries a status color, not just a number.
- **One trend, done well:** a single Altair spend-trend chart with budget line and forecast band — formatted axis, tooltip — not three small ones.

### 4.3 Unified Issue Feed (the heart)
Today, risks are scattered: `alerts.evaluate`, security `assess_*`, task failures, anomalies. Merge them into one ranked feed component fed by all engines, each item: `severity dot · domain badge · title · why (the metrics) · action · [Investigate] [Acknowledge]`. This is the single most valuable UX change — it turns "ten pages of data" into "one prioritized list of decisions." Reuse the existing `Alert` dataclass and `ledger` ack flow.

### 4.4 Reusable component kit (in `sections/_common.py`)
- `kpi(label, value, *, delta=None, status="neutral", help=None)` → HTML card with a left status bar (red/amber/green/neutral). Replaces bare `st.metric`.
- `issue_card(severity, domain, title, why, action, key, on_ack)` → the feed row.
- `section_title(text, sub)` → consistent heading + caption spacing.
- `render_table(df, money=[...], pct=[...], gb=[...], rename={...})` → humanizes columns (Title Case), formats $/%/GB/durations in-value (old runtime has no `column_config`), right-aligns numerics.
- `alt_bar(df, x, y, *, money=False)` / `alt_line(...)` → Altair helpers with formatted axes, titles, tooltips, brand palette.
- `empty(msg)` (neutral grey, not green) and `loading(msg)` wrappers.
- `subview(options)` → renders a horizontal `st.radio` and returns the choice, so pages run **only the selected** sub-view's queries (replaces eager `st.tabs`).

### 4.5 Drilldown model
Replace `st.tabs` with `subview()` on Cost, Task Graphs, Optimization, Controls. Same labels, but only one query set runs at a time. Add "Investigate →" buttons from Command Center / Issue Feed that set `st.session_state["page"]` plus a context key (e.g. `focus_warehouse`) so the target page opens pre-filtered.

### 4.6 Visual system (one CSS block, injected once in `app.py`)
Define CSS variables for severity colors, card style, spacing, and a real font stack. Tighten `st.metric`/headers. Inter or system-ui font. This single block is ~70% of the "looks enterprise" jump.

---

## 5. Prioritized backlog

### P0 — must fix immediately
**P0-1 · Kill eager-tab over-fetching**
- Problem: `st.tabs` runs every tab body each load; Cost fires ~15 queries, Tasks ~8, regardless of what's open.
- Why it matters: dominant latency + warehouse cost at 12 concurrent users; makes the app feel slow.
- Solution: add `subview()` helper; convert `cost.py`, `tasks.py`, `optimize.py`, `controls.py` to run only the selected sub-view.
- Files: `sections/_common.py` (new helper), `sections/cost.py`, `sections/tasks.py`, `sections/optimize.py`, `sections/controls.py`.
- Difficulty: M. UX impact: **High** (speed + declutter).

**P0-2 · Fix or remove the dead Environment filter**
- Problem: PROD/DEV/ALL changes nothing.
- Why: silent wrong-trust; users assume filtered data.
- Solution (preferred): thread `environment` into queries via a `company.environment_scope_sql(env, db_col)` built on the existing prod/dev classifiers in `company.py`; apply in the same place as company scope. Fallback: remove the selector.
- Files: `app.py`, `lib/company.py`, `sections/_common.py` (`scope()`), every section that builds scoped SQL, `lib/queries.py`.
- Difficulty: M (wire) / S (remove). UX impact: **High** (trust).

**P0-3 · Stop leaking raw SQL errors**
- Problem: `session.run` and `sections/__init__.py` print exception text to users.
- Why: looks broken; can expose schema/SQL internals.
- Solution: friendly message + detail behind `st.expander("Details")`; always log via `observability.log_error`. Add a `quiet`-style consistent error card.
- Files: `lib/session.py`, `sections/__init__.py`.
- Difficulty: S. UX impact: Medium-High.

### P1 — high impact
**P1-1 · Component kit + CSS system** (`kpi`, `issue_card`, `render_table`, `alt_bar/alt_line`, `empty`, `loading`, one CSS block). Files: `sections/_common.py`, `app.py`. Difficulty: M. Impact: **High** (the polish jump).

**P1-2 · Command Center landing.** Rebuild `overview.py` into triage hero + status-colored KPI strip + one good Altair chart. Files: `sections/overview.py`, `lib/metrics.py` (maybe a `top_issues()` aggregator). Difficulty: M-L. Impact: **High**.

**P1-3 · Unified Issue Feed.** New `lib/issues.py` that merges `alerts.evaluate`, security `assess_*`, task failures, spend anomalies into one ranked list; render via `issue_card`. Reuse on Command Center + Alerts. Files: `lib/issues.py` (new), `sections/alerts.py`, `sections/overview.py`. Difficulty: M. Impact: **High**.

**P1-4 · Humanize every table.** Route all `st.dataframe` calls through `render_table` with money/pct/gb formatting and Title-Case headers. Files: all `sections/*.py`. Difficulty: M (mechanical). Impact: **High** (kills the "SQL dump" look).

**P1-5 · Grouped, sectioned nav + status pill.** Files: `app.py`, `sections/__init__.py` (group metadata). Difficulty: S-M. Impact: Medium-High.

### P2 — polish
- **P2-1 · Altair charts everywhere** (replace `st.bar_chart`/`st.line_chart`; remove chart+table redundancy). Files: all sections. Difficulty: M. Impact: Medium.
- **P2-2 · Loading spinners with real copy** around query batches; turn select caches' spinner on or wrap in `st.spinner`. Files: `sections/*`, optionally `lib/session.py`. Difficulty: S. Impact: Medium.
- **P2-3 · Neutral empty states** (`empty()` grey, reserve green for verified-healthy). Files: all sections. Difficulty: S. Impact: Medium.
- **P2-4 · Merge Recommendations + Optimization into "Optimize"** with two sub-views (Savings / Performance) to remove conceptual overlap. Files: `sections/__init__.py`, `recommendations.py`, `optimize.py`. Difficulty: M. Impact: Medium.

### P3 — nice-to-have
- **P3-1 · Deep-link context** ("Investigate →" sets `focus_*` and target page pre-filters). Files: `_common.py`, target sections. Difficulty: M. Impact: Medium.
- **P3-2 · Density toggle / consistent number locale.** Difficulty: S. Impact: Low-Medium.
- **P3-3 · Per-page "last refreshed" + cache-age chip.** Difficulty: S. Impact: Low.
- **P3-4 · Keyboard-free quick filters** on Explorer (preset chips: "Failed >60s", "Top scanners"). Difficulty: S. Impact: Low-Medium.

---

## 6. Aggressive implementation plan (one pass)

**Order:** build the kit → fix perf/trust → rebuild landing+feed → roll formatting across pages.

1. **`sections/_common.py` — component kit (do first, everything depends on it).**
   - Add `CSS` constant + `inject_css()`; call once in `app.py` after `set_page_config`.
   - Add `kpi()`, `issue_card()`, `render_table()`, `alt_bar()`, `alt_line()`, `empty()`, `loading()`, `subview()`, `section_title()`.
   - Keep `md_escape`, `scope`, `header`, `SEVERITY_EMOJI`.
2. **Perf: convert eager tabs → `subview()`** in `cost.py`, `tasks.py`, `optimize.py`, `controls.py`. Guard each branch so only the selected sub-view's `session.run` calls execute.
3. **Trust: Environment filter** — implement `company.environment_scope_sql()`; thread through `scope()` consumers, or remove the widget if you'd rather defer. Pick one in this pass; don't ship it half-wired.
4. **Errors:** centralize a friendly error card in `session.run` (detail in expander, log always) and in `sections/__init__.py` dispatcher.
5. **`lib/issues.py`** — `gather_issues(company, days) -> list[Alert-like]` merging alert engine + security assessors + task failures + anomalies; ranked.
6. **`sections/overview.py` → Command Center** — hero (top 3 issues via `issues.gather_issues`), KPI strip via `kpi()`, one `alt_line` spend+budget chart.
7. **`sections/alerts.py`** — render the unified feed via `issue_card`; keep ledger history + server-side ALERT SQL expander.
8. **Roll `render_table` + `alt_*` + `empty()`** through Cost, Tasks, Security, Optimization, Recommendations, Explorer, Digest.
9. **`app.py`** — grouped nav with section headers + colored status pill; inject CSS.

**Performance safeguards (must hold):**
- Only the active sub-view runs queries (no eager tabs).
- Keep `@st.cache_data` tiers; don't add per-widget reruns inside loops beyond existing ack buttons.
- Altair only (bundled) — no Plotly/AgGrid/components that may break in SiS.
- Format DataFrames before `st.dataframe` (no `column_config` on old runtime).
- One CSS injection per session, not per component.

**Testing / validation checklist:**
- [ ] All existing `tests/` pass unchanged (logic untouched).
- [ ] New pure helpers (`issues.gather_issues`, `render_table` formatters, `company.environment_scope_sql`) have unit tests; verify in `/tmp` isolation (null-strip the mount copies).
- [ ] Cost page issues ≤ 1 sub-view's worth of queries per load (instrument query count).
- [ ] Environment = PROD actually changes row counts vs ALL (or selector removed).
- [ ] No raw SQL error string reaches the UI (force a bad query; confirm friendly card).
- [ ] Renders on the SiS old runtime: no `column_config`/`navigation`/`segmented_control`/`dialog` used; `compat.py` still imported first.
- [ ] md_escape still applied to all dynamic `$`/`_` text (recommendations, security, issue feed).
- [ ] Smoke test each page with empty data (neutral empty states, no crashes).

---

## 7. Final Cursor prompt to execute the redesign

> **Context:** SnowMonitor is a Streamlit-in-Snowflake app (`snowmonitor/`) that targets an OLD Streamlit runtime — see `lib/compat.py`. Do NOT use `st.navigation`/`st.Page`, `st.column_config`, `st.segmented_control`, `st.dialog`, or any non-bundled component. Charts must use **Altair** (bundled). Preserve all existing functionality and all `lib/` business logic; this is a UI/UX pass. Keep `lib/compat` imported first in `app.py`.
>
> **Goal:** Make it look and feel like an enterprise Snowflake control center, and fix the two perf/trust defects.
>
> **Do this, in order:**
> 1. In `sections/_common.py`, add a reusable kit: `inject_css()` (one CSS block: severity color vars `--crit/#ef4444 --high/#f59e0b --med/#eab308 --ok/#22c55e --neutral`, card style with left status bar, tightened metric/heading spacing, system-ui/Inter font); `kpi(label,value,delta=None,status='neutral',help=None)`; `issue_card(severity,domain,title,why,action,key,on_ack=None)`; `render_table(df,money=(),pct=(),gb=(),secs=(),rename=None)` that Title-Cases headers and formats values (old runtime has no column_config); `alt_bar(df,x,y,money=False,title=None)` and `alt_line(df,x,ys,money=False,title=None)` using Altair with formatted axes + tooltips; `empty(msg)` (neutral grey) and `loading(msg)`; `subview(options, key)` that renders a horizontal `st.radio` and returns the selection. Keep `md_escape`, `scope`, `header`, `SEVERITY_EMOJI`. Call `inject_css()` once in `app.py` right after `st.set_page_config`.
> 2. Replace `st.tabs` with `subview()` in `sections/cost.py`, `sections/tasks.py`, `sections/optimize.py`, `sections/controls.py`, and gate each branch so ONLY the selected sub-view executes its `session.run` queries. Preserve every existing query and label.
> 3. Wire the Environment filter: add `environment_scope_sql(env, db_col)` to `lib/company.py` using the existing prod/dev classifiers; thread `env` from `scope()` into the scoped SQL builders in `lib/queries.py` / cost / tasks / security. If wiring is out of scope, instead REMOVE the Environment selectbox from `app.py` and `_common.header`. Do not leave it non-functional.
> 4. Centralize friendly errors: in `lib/session.run`, on failure render a compact error card with the raw message hidden behind `st.expander("Details")` and always call `observability.log_error`; do the same in `sections/__init__.py`'s dispatcher. Never print raw SQL errors inline.
> 5. Create `lib/issues.py` with `gather_issues(company, days)` that merges `alerts.evaluate(metrics)`, the security `assess_*` results, active task failures, and spend anomalies into one severity-ranked list of `Alert`-shaped items. Unit-test the ranking purely.
> 6. Rebuild `sections/overview.py` as a Command Center: a hero showing the top 3 issues from `issues.gather_issues` as `issue_card`s with Investigate buttons (set `st.session_state['page']` + a `focus_*` key), a one-row KPI strip via `kpi()` with status colors, and a single `alt_line` spend-vs-budget chart with a forecast band.
> 7. Rebuild the `sections/alerts.py` lists with `issue_card`; keep the ledger history and the server-side ALERT SQL expander.
> 8. Route every `st.dataframe` in all sections through `render_table` (money/pct/gb/secs formatting, Title-Case headers); replace `st.bar_chart`/`st.line_chart` with `alt_bar`/`alt_line`; replace green "no data" `st.success` calls with `empty()`; wrap each page's query batch in `loading()`/`st.spinner`.
> 9. In `app.py`, group the nav buttons under MONITOR / INVESTIGATE / ACT / REPORT headers and add a colored status pill (counts from `issues.gather_issues`).
>
> **Constraints:** bundled deps only (Altair OK); format DataFrame values before display; one CSS injection per session; keep all `tests/` green and add tests for new pure helpers; verify nothing uses unsupported new-Streamlit APIs. After changes, run the full test suite and smoke-test every page with empty data.
