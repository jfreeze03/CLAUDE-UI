"""Guarded control actions — warehouse timeouts and Cortex usage limits.

Pure SQL generation + validation (no Snowflake/Streamlit imports, fully tested).
Every action returns a `ControlAction` carrying the forward SQL, an explicit
**rollback** statement, a one-line summary, and the privilege the operator needs.
The UI (sections/controls.py) is responsible for current-state display, typed
confirmation, execution gating, and writing the audit row from `audit_insert_sql`.

Why these levers:
  * Warehouse STATEMENT_TIMEOUT_IN_SECONDS / STATEMENT_QUEUED_TIMEOUT_IN_SECONDS —
    cap runaway and queued statements (cost + reliability control).
  * Cortex access (SNOWFLAKE.CORTEX_USER database role) — turn Cortex on/off per role.
  * Cortex model allowlist (CORTEX_MODELS_ALLOWLIST account parameter) — restrict to
    approved/cheaper models. (Snowflake has no single Cortex dollar cap; these plus a
    budget/alert are the real limits.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import config

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,254}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

AUDIT = config.monitoring_fqn(config.ACTION_AUDIT_TABLE)


def safe_identifier(value: str, *, allow_qualified: bool = False) -> str:
    """Validate a Snowflake identifier; raise ValueError if unsafe."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Identifier cannot be blank")
    parts = raw.split(".") if allow_qualified else [raw]
    if any(not _IDENT_RE.match(p) for p in parts):
        raise ValueError(f"Unsafe Snowflake identifier: {raw}")
    return ".".join(p.upper() for p in parts)


def _sql_lit(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _bounded_seconds(value: object) -> int:
    n = int(value)
    if n < config.WAREHOUSE_TIMEOUT_MIN_S or n > config.WAREHOUSE_TIMEOUT_MAX_S:
        raise ValueError(
            f"Timeout must be between {config.WAREHOUSE_TIMEOUT_MIN_S} and "
            f"{config.WAREHOUSE_TIMEOUT_MAX_S} seconds"
        )
    return n


@dataclass(frozen=True)
class ControlAction:
    title: str
    summary: str
    sql: str
    rollback_sql: str
    privilege_note: str

    def as_row(self) -> dict:
        return {"title": self.title, "summary": self.summary, "sql": self.sql,
                "rollback_sql": self.rollback_sql, "privilege": self.privilege_note}


# --------------------------------------------------------------------------
# Warehouse timeouts
# --------------------------------------------------------------------------

def warehouse_timeout_current_sql(warehouse: str) -> str:
    """SHOW the current statement/queued timeout parameters for a warehouse."""
    wh = safe_identifier(warehouse)
    return f"SHOW PARAMETERS LIKE 'STATEMENT%TIMEOUT_IN_SECONDS' IN WAREHOUSE {wh}"


def set_warehouse_timeout_action(
    warehouse: str,
    statement_timeout_s: int,
    queued_timeout_s: int | None,
    current_statement_s: int | None,
    current_queued_s: int | None,
) -> ControlAction:
    """ALTER WAREHOUSE timeouts, with a rollback to the supplied current values."""
    wh = safe_identifier(warehouse)
    stmt = _bounded_seconds(statement_timeout_s)
    sets = [f"STATEMENT_TIMEOUT_IN_SECONDS = {stmt}"]
    rollback_sets = []
    if current_statement_s is not None:
        rollback_sets.append(f"STATEMENT_TIMEOUT_IN_SECONDS = {_bounded_seconds(current_statement_s)}")
    if queued_timeout_s is not None:
        q = _bounded_seconds(queued_timeout_s)
        sets.append(f"STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = {q}")
        if current_queued_s is not None:
            rollback_sets.append(f"STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = {_bounded_seconds(current_queued_s)}")

    sql = f"ALTER WAREHOUSE {wh} SET {', '.join(sets)};"
    rollback = (
        f"ALTER WAREHOUSE {wh} SET {', '.join(rollback_sets)};"
        if rollback_sets else f"-- No prior values captured; rollback manually for {wh}."
    )
    parts = [f"statement timeout -> {stmt}s"]
    if queued_timeout_s is not None:
        parts.append(f"queued timeout -> {queued_timeout_s}s")
    return ControlAction(
        title=f"Set timeouts on {wh}",
        summary="; ".join(parts),
        sql=sql, rollback_sql=rollback,
        privilege_note="Requires MODIFY (or OWNERSHIP) on the warehouse, or MANAGE WAREHOUSES.",
    )


# --------------------------------------------------------------------------
# Cortex usage limits
# --------------------------------------------------------------------------

def cortex_access_action(action: str, role: str) -> ControlAction:
    """Grant or revoke Cortex access (SNOWFLAKE.CORTEX_USER) to/from a role."""
    role_id = safe_identifier(role)
    act = str(action or "").upper()
    if act not in ("GRANT", "REVOKE"):
        raise ValueError("action must be GRANT or REVOKE")
    cortex_role = config.CORTEX_USER_ROLE  # e.g. SNOWFLAKE.CORTEX_USER
    if act == "GRANT":
        sql = f"GRANT DATABASE ROLE {cortex_role} TO ROLE {role_id};"
        rollback = f"REVOKE DATABASE ROLE {cortex_role} FROM ROLE {role_id};"
        summary = f"enable Cortex for role {role_id}"
    else:
        sql = f"REVOKE DATABASE ROLE {cortex_role} FROM ROLE {role_id};"
        rollback = f"GRANT DATABASE ROLE {cortex_role} TO ROLE {role_id};"
        summary = f"disable Cortex for role {role_id}"
    return ControlAction(
        title=f"{act.title()} Cortex access · {role_id}",
        summary=summary, sql=sql, rollback_sql=rollback,
        privilege_note="Requires the privilege to grant SNOWFLAKE database roles (e.g. ACCOUNTADMIN).",
    )


def cortex_model_allowlist_action(models: list[str] | None) -> ControlAction:
    """Restrict Cortex to an approved model list, or reset to all models.

    Pass a non-empty list to restrict; pass None/empty to reset (UNSET).
    """
    if models:
        clean = []
        for m in models:
            m = str(m).strip()
            if not _MODEL_RE.match(m):
                raise ValueError(f"Unsafe model name: {m}")
            clean.append(m)
        allow = ",".join(clean)
        sql = f"ALTER ACCOUNT SET CORTEX_MODELS_ALLOWLIST = {_sql_lit(allow)};"
        rollback = "ALTER ACCOUNT UNSET CORTEX_MODELS_ALLOWLIST;"
        summary = f"restrict Cortex to: {allow}"
    else:
        sql = "ALTER ACCOUNT UNSET CORTEX_MODELS_ALLOWLIST;"
        rollback = "-- Re-apply your previous CORTEX_MODELS_ALLOWLIST value if you had one."
        summary = "reset Cortex model allowlist (allow all)"
    return ControlAction(
        title="Cortex model allowlist",
        summary=summary, sql=sql, rollback_sql=rollback,
        privilege_note="Requires ACCOUNTADMIN (ALTER ACCOUNT). Parameter availability varies by region/edition.",
    )


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

def audit_insert_sql(actor: str, action: ControlAction) -> str:
    """INSERT one row recording an executed control action."""
    return f"""INSERT INTO {AUDIT}
        (ACTOR, TITLE, SUMMARY, SQL_TEXT, ROLLBACK_SQL)
        VALUES ({_sql_lit(actor)}, {_sql_lit(action.title)}, {_sql_lit(action.summary)},
                {_sql_lit(action.sql)}, {_sql_lit(action.rollback_sql)})"""
