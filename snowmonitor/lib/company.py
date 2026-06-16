"""Company segregation — ALFA vs Trexis.

This is the module the previous tool lacked: ONE deterministic place that decides,
for any account object, whether it belongs to ALFA, Trexis, or is genuinely
Unclassified. ALFA is the default catch-all; Trexis is an explicit allow-list.

Two parallel implementations are kept in lock-step:
  - `classify_company(...)`  : Python, used for tests and any in-app DataFrame work.
  - `company_case_sql(...)`  : the identical logic as a Snowflake CASE expression,
                               usable directly in GROUP BY for attribution.
Both derive from the same rules in config.COMPANIES, and `test_company.py` asserts
they agree on a shared table of cases. That parity is what makes the segregation
trustworthy.

Design choices:
  * Literal matching only (STARTSWITH / ENDSWITH / CONTAINS), never LIKE wildcards,
    so underscores are never accidental wildcards.
  * "Has context but not Trexis" => ALFA (the default). "No context at all"
    (account-level rows with no warehouse/db/user) => Unclassified, never silently ALFA.
"""

from __future__ import annotations

import config

_TREXIS = config.COMPANIES["Trexis"]
_ALFA = config.COMPANIES["ALFA"]


# --------------------------------------------------------------------------
# Python classification (source of truth for tests)
# --------------------------------------------------------------------------

def _norm(value: object) -> str:
    return str(value or "").strip().upper()


def is_trexis(warehouse: object = None, database: object = None, user: object = None) -> bool:
    """True if any signal positively identifies the row as Trexis."""
    wh, db, usr = _norm(warehouse), _norm(database), _norm(user)
    if wh and wh in {w.upper() for w in _TREXIS["warehouses"]}:
        return True
    if db and (
        any(db.startswith(p) for p in _TREXIS["db_prefixes"])
        or any(c in db for c in _TREXIS["db_contains"])
    ):
        return True
    if usr and any(usr.startswith(p) for p in _TREXIS["user_prefixes"]):
        return True
    return False


def classify_company(warehouse: object = None, database: object = None, user: object = None) -> str:
    """Return 'Trexis', 'ALFA' (default/catch-all), or 'Unclassified' (no context)."""
    if is_trexis(warehouse, database, user):
        return "Trexis"
    if _norm(warehouse) or _norm(database) or _norm(user):
        return _ALFA["label"]  # has context, not Trexis -> ALFA default
    return config.UNCLASSIFIED_LABEL


def classify_environment(database: object) -> str:
    """Map a database to PROD / DEV / UNKNOWN using the company's env rules."""
    db = _norm(database)
    if not db:
        return "UNKNOWN"
    # Trexis env keys are suffix/contains based.
    if any(db.endswith(s) for s in _TREXIS["prod_db_suffixes"]) or any(c in db for c in _TREXIS["prod_db_contains"]):
        return "PROD"
    if any(db.endswith(s) for s in _TREXIS["dev_db_suffixes"]) or any(c in db for c in _TREXIS["dev_db_contains"]):
        return "DEV"
    # ALFA env keys.
    if db in {d.upper() for d in _ALFA["prod_dbs"]}:
        return "PROD"
    if any(db.startswith(p) for p in _ALFA["db_prefixes"]):
        return "DEV"
    return "UNKNOWN"


# --------------------------------------------------------------------------
# SQL generation (identical logic, for GROUP BY attribution and scoping)
# --------------------------------------------------------------------------

def _sql_str_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''").upper() + "'" for v in values)


def trexis_predicate_sql(
    wh_col: str | None = "warehouse_name",
    db_col: str | None = "database_name",
    user_col: str | None = "user_name",
) -> str:
    """Return a Snowflake boolean expression that is TRUE for Trexis rows.

    Pass None for any column not present in the query; that signal is skipped.
    """
    parts: list[str] = []
    if wh_col:
        parts.append(f"UPPER({wh_col}) IN ({_sql_str_list(_TREXIS['warehouses'])})")
    if db_col:
        for p in _TREXIS["db_prefixes"]:
            parts.append(f"STARTSWITH(UPPER({db_col}), '{p}')")
        for c in _TREXIS["db_contains"]:
            parts.append(f"CONTAINS(UPPER({db_col}), '{c}')")
    if user_col:
        for p in _TREXIS["user_prefixes"]:
            parts.append(f"STARTSWITH(UPPER({user_col}), '{p}')")
    return "(" + " OR ".join(parts) + ")" if parts else "FALSE"


def _has_context_sql(cols: list[str]) -> str:
    present = [c for c in cols if c]
    if not present:
        return "FALSE"
    return "(" + " OR ".join(f"{c} IS NOT NULL" for c in present) + ")"


def company_case_sql(
    wh_col: str | None = "warehouse_name",
    db_col: str | None = "database_name",
    user_col: str | None = "user_name",
    alias: str = "COMPANY",
) -> str:
    """Return a CASE expression labeling each row ALFA / Trexis / Unclassified.

    Drop this straight into a SELECT and GROUP BY for company attribution.
    """
    trexis = trexis_predicate_sql(wh_col, db_col, user_col)
    has_ctx = _has_context_sql([c for c in (wh_col, db_col, user_col) if c])
    return (
        "CASE "
        f"WHEN {trexis} THEN 'Trexis' "
        f"WHEN {has_ctx} THEN '{_ALFA['label']}' "
        f"ELSE '{config.UNCLASSIFIED_LABEL}' "
        f"END AS {alias}"
    )


def company_scope_sql(
    company: str,
    wh_col: str | None = "warehouse_name",
    db_col: str | None = "database_name",
    user_col: str | None = "user_name",
) -> str:
    """Return a WHERE fragment (leading 'AND ...') scoping rows to one company.

    'ALL' (or empty) returns '' (no scoping). ALFA = NOT Trexis but has context.
    """
    company = str(company or "").strip()
    if not company or company.upper() == "ALL":
        return ""
    trexis = trexis_predicate_sql(wh_col, db_col, user_col)
    if company == "Trexis":
        return f"AND {trexis}"
    if company == _ALFA["label"]:
        has_ctx = _has_context_sql([c for c in (wh_col, db_col, user_col) if c])
        return f"AND (NOT {trexis}) AND {has_ctx}"
    if company == config.UNCLASSIFIED_LABEL:
        has_ctx = _has_context_sql([c for c in (wh_col, db_col, user_col) if c])
        return f"AND (NOT {has_ctx})"
    return ""


def environment_case_sql(db_col: str = "database_name", alias: str = "ENVIRONMENT") -> str:
    """Return a CASE expression mapping a database column to PROD / DEV / UNKNOWN."""
    prod_suffix = " OR ".join(f"ENDSWITH(UPPER({db_col}), '{s}')" for s in _TREXIS["prod_db_suffixes"])
    prod_contains = " OR ".join(f"CONTAINS(UPPER({db_col}), '{c}')" for c in _TREXIS["prod_db_contains"])
    dev_suffix = " OR ".join(f"ENDSWITH(UPPER({db_col}), '{s}')" for s in _TREXIS["dev_db_suffixes"])
    dev_contains = " OR ".join(f"CONTAINS(UPPER({db_col}), '{c}')" for c in _TREXIS["dev_db_contains"])
    alfa_prod = f"UPPER({db_col}) IN ({_sql_str_list(_ALFA['prod_dbs'])})"
    alfa_any = " OR ".join(f"STARTSWITH(UPPER({db_col}), '{p}')" for p in _ALFA["db_prefixes"])
    return (
        "CASE "
        f"WHEN {prod_suffix} OR {prod_contains} OR {alfa_prod} THEN 'PROD' "
        f"WHEN {dev_suffix} OR {dev_contains} THEN 'DEV' "
        f"WHEN {alfa_any} THEN 'DEV' "
        "ELSE 'UNKNOWN' "
        f"END AS {alias}"
    )


def environment_scope_sql(environment: str, db_col: str = "database_name") -> str:
    """Return a WHERE fragment scoping to PROD or DEV; '' for ALL/unknown."""
    env = str(environment or "").strip().upper()
    if env not in {"PROD", "DEV"}:
        return ""
    # Reuse the CASE by wrapping it; simplest correct approach.
    case_expr = environment_case_sql(db_col, alias="_ENV").rsplit(" AS ", 1)[0]
    return f"AND ({case_expr}) = '{env}'"
