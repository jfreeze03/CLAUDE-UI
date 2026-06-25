"""Shared UI kit + page helpers.

Reusable, runtime-safe building blocks so every page looks consistent and runs
on the OLD Streamlit runtime used by Streamlit-in-Snowflake (see lib/compat.py).
No st.column_config / st.navigation / st.segmented_control / st.dialog. Charts use
Altair (bundled with Streamlit, SiS-safe) with a graceful fallback to native charts.

Public kit: inject_css, kpi, kpi_row, issue_card, render_table, alt_bar, alt_line,
empty, loading, subview, section_title — plus scope, header, SEVERITY_EMOJI, md_escape.
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import config

try:  # formulas is pure; import defensively so the kit never hard-fails
    from lib import formulas as _formulas
    _fmt_usd = _formulas.fmt_usd
except Exception:  # pragma: no cover
    def _fmt_usd(v):
        try:
            return f"${float(v):,.0f}"
        except Exception:
            return str(v)


# --------------------------------------------------------------------------
# Scope + headers
# --------------------------------------------------------------------------

def scope() -> tuple[str, str, int]:
    return (
        st.session_state.get("company", config.DEFAULT_COMPANY),
        st.session_state.get("environment", config.DEFAULT_ENVIRONMENT),
        int(st.session_state.get("days", config.DEFAULT_LOOKBACK_DAYS)),
    )


def header(title: str, subtitle: str = "") -> None:
    company, _env, days = scope()
    st.header(title)
    if subtitle:
        st.caption(subtitle)
    color = config.COMPANIES.get(company, {}).get("color", "#38bdf8")
    st.markdown(
        f"<div class='sm-scope'><span class='sm-dot' style='background:{color}'></span>"
        f"<b>{html.escape(str(company))}</b> · last {int(days)}d "
        f"<span class='sm-scope-src'>· {html.escape(config.ACCOUNT_USAGE_FRESHNESS)}</span></div>",
        unsafe_allow_html=True,
    )


def section_title(text: str, sub: str = "") -> None:
    st.subheader(text)
    if sub:
        st.caption(sub)


SEVERITY_EMOJI = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "⚪"}
_SEV_COLOR = {"Critical": "#ef4444", "High": "#f59e0b", "Medium": "#eab308",
              "Low": "#64748b", "ok": "#22c55e", "neutral": "#64748b"}


def md_escape(text: object) -> str:
    """Escape Markdown specials so dynamic text renders literally (esp. '$' -> LaTeX, '_' -> italics)."""
    s = str(text if text is not None else "")
    for ch in ("\\", "$", "_", "*", "`"):
        s = s.replace(ch, "\\" + ch)
    return s


# --------------------------------------------------------------------------
# CSS (injected once per session)
# --------------------------------------------------------------------------

_CSS = """
<style>
:root {
  --sm-crit:#ef4444; --sm-high:#f59e0b; --sm-med:#eab308; --sm-ok:#22c55e; --sm-neutral:#64748b;
  --sm-card:#0e1b2a; --sm-border:#1e293b; --sm-text:#e2e8f0; --sm-muted:#94a3b8;
}
html, body, [class*="css"] { font-family: 'Inter', -apple-system, system-ui, 'Segoe UI', Roboto, sans-serif; }
.sm-scope { font-size:0.82rem; color:var(--sm-muted); margin:-4px 0 6px 0; }
.sm-scope b { color:var(--sm-text); }
.sm-scope-src { color:#64748b; }
.sm-dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; vertical-align:middle; }
.sm-kpi { background:var(--sm-card); border:1px solid var(--sm-border); border-left:4px solid var(--sm-neutral);
  border-radius:8px; padding:10px 13px; min-height:78px; }
.sm-kpi .lbl { font-size:0.7rem; text-transform:uppercase; letter-spacing:.04em; color:var(--sm-muted); margin-bottom:2px; }
.sm-kpi .val { font-size:1.5rem; font-weight:700; color:var(--sm-text); line-height:1.15; }
.sm-kpi .dlt { font-size:0.74rem; color:var(--sm-muted); margin-top:1px; }
.sm-kpi.crit { border-left-color:var(--sm-crit); }
.sm-kpi.high { border-left-color:var(--sm-high); }
.sm-kpi.med  { border-left-color:var(--sm-med); }
.sm-kpi.ok   { border-left-color:var(--sm-ok); }
.sm-kpi .dlt.bad { color:#fca5a5; } .sm-kpi .dlt.good { color:#86efac; }
.sm-badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.68rem; font-weight:600;
  background:#1e293b; color:#cbd5e1; vertical-align:middle; }
.sm-sevdot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:7px; vertical-align:middle; }
.sm-empty { color:var(--sm-muted); background:var(--sm-card); border:1px dashed #334155; border-radius:8px;
  padding:10px 14px; font-size:0.85rem; }
.sm-pill { display:inline-block; padding:2px 10px; border-radius:12px; font-size:0.74rem; font-weight:600; margin:1px 2px; }
.sm-navgroup { font-size:0.68rem; text-transform:uppercase; letter-spacing:.08em; color:#64748b;
  margin:10px 0 2px 2px; font-weight:700; }
/* tighten default chrome a touch */
div[data-testid="stMetricValue"] { font-size:1.5rem; }
section[data-testid="stSidebar"] .stButton button { text-align:left; }
</style>
"""


def inject_css() -> None:
    if st.session_state.get("_css_injected"):
        return
    st.markdown(_CSS, unsafe_allow_html=True)
    st.session_state["_css_injected"] = True


# --------------------------------------------------------------------------
# KPI cards
# --------------------------------------------------------------------------

def kpi(label: str, value: object, delta: str | None = None, status: str = "neutral",
        good: bool | None = None, help: str | None = None) -> None:
    """Status-colored KPI card. status in {neutral, ok, med, high, crit}."""
    cls = {"neutral": "", "ok": "ok", "med": "med", "medium": "med", "high": "high",
           "crit": "crit", "critical": "crit"}.get(str(status).lower(), "")
    dcls = "good" if good is True else ("bad" if good is False else "")
    title = f" title='{html.escape(str(help))}'" if help else ""
    dhtml = f"<div class='dlt {dcls}'>{html.escape(str(delta))}</div>" if delta else ""
    st.markdown(
        f"<div class='sm-kpi {cls}'{title}><div class='lbl'>{html.escape(str(label))}</div>"
        f"<div class='val'>{html.escape(str(value))}</div>{dhtml}</div>",
        unsafe_allow_html=True,
    )


def kpi_row(cards: list[dict]) -> None:
    """Render a row of KPI cards from a list of kwargs dicts."""
    cols = st.columns(len(cards))
    for c, kw in zip(cols, cards):
        with c:
            kpi(**kw)


# --------------------------------------------------------------------------
# Issue card (unified alert/risk row)
# --------------------------------------------------------------------------

def issue_card(severity: str, domain: str, title: str, why: str = "", action: str = "",
               key: str = "", on_ack=None, on_investigate=None,
               investigate_label: str = "Investigate →") -> None:
    color = _SEV_COLOR.get(severity, _SEV_COLOR["neutral"])
    with st.container(border=True):
        st.markdown(
            f"<span class='sm-sevdot' style='background:{color}'></span>"
            f"<b>{html.escape(str(title))}</b> &nbsp;"
            f"<span class='sm-badge'>{html.escape(str(domain))}</span> &nbsp;"
            f"<span class='sm-badge' style='background:{color}22;color:{color}'>{html.escape(str(severity))}</span>",
            unsafe_allow_html=True,
        )
        if why:
            st.markdown(md_escape(why))
        if action:
            st.caption("→ " + md_escape(action))
        if on_ack or on_investigate:
            cols = st.columns([1, 1, 4])
            if on_investigate and cols[0].button(investigate_label, key=f"inv_{key}"):
                on_investigate()
            if on_ack and cols[1].button("Acknowledge", key=f"ack_{key}",
                                         help="Record + mute until it recurs (use for confirmed false positives)."):
                on_ack()


# --------------------------------------------------------------------------
# Tables — humanized headers + formatted values (no column_config on old runtime)
# --------------------------------------------------------------------------

_ACRONYMS = {"USD", "GB", "TB", "MB", "IP", "SQL", "MFA", "ATO", "DB", "ID", "AI",
             "RCA", "SLA", "P95", "P90", "CPU", "URL", "CLI", "TTL", "QPS"}


def _pretty_col(name: str) -> str:
    raw = str(name).replace("_", " ").strip()
    out = []
    for w in raw.split():
        out.append(w if w.upper() in _ACRONYMS else w.capitalize())
        if w.upper() in _ACRONYMS:
            out[-1] = w.upper()
    return " ".join(out)


def _fmt_value(col: str, v) -> str:
    if pd.isna(v):
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    u = col.upper()
    if u.endswith("_USD") or "COST_USD" in u or u.endswith("SAVINGS") or "SAVING" in u:
        return _fmt_usd(f)
    if "PCT" in u or u.endswith("_RATE"):
        return f"{f:,.1f}%"
    if "GB" in u:
        return f"{f:,.1f} GB"
    if u.endswith("_TB") or u == "TB":
        return f"{f:,.2f} TB"
    if u.endswith("_SEC") or u.endswith("_SECONDS") or "DURATION_SEC" in u:
        return f"{f:,.0f}s"
    if u.endswith("_MIN") or "MINUTES" in u or u.endswith("_MINUTES"):
        return f"{f:,.0f} min"
    if "CREDIT" in u:
        return f"{f:,.1f}"
    if float(f).is_integer():
        return f"{int(f):,}"
    return f"{f:,.2f}"


def render_table(df: pd.DataFrame, rename: dict | None = None, max_rows: int | None = None,
                 hide_cols: list | None = None) -> None:
    """Display a DataFrame with Title-Case headers and formatted money/%/GB/duration values."""
    if df is None or df.empty:
        empty("No rows.")
        return
    out = df.copy()
    if hide_cols:
        out = out.drop(columns=[c for c in hide_cols if c in out.columns], errors="ignore")
    if max_rows:
        out = out.head(max_rows)
    numeric_like = ("USD", "PCT", "GB", "TB", "_SEC", "MINUTES", "_MIN", "CREDIT",
                    "DURATION", "RATE", "SAVING", "RUNS", "CALLS", "ATTEMPTS", "LOGINS",
                    "COUNT", "TOKENS", "QUERIES")
    for col in out.columns:
        u = str(col).upper()
        if any(tok in u for tok in numeric_like):
            try:
                out[col] = out[col].map(lambda v, c=col: _fmt_value(c, v))
            except Exception:
                pass
    headers = {c: (rename or {}).get(c, _pretty_col(c)) for c in out.columns}
    out = out.rename(columns=headers)
    st.dataframe(out, use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------
# Charts (Altair with native fallback)
# --------------------------------------------------------------------------

def _alt():
    try:
        import altair as alt
        return alt
    except Exception:
        return None


def alt_bar(df: pd.DataFrame, x: str, y: str, money: bool = False, title: str = "",
            top: int = 15, horizontal: bool = True) -> None:
    if df is None or df.empty or x not in df.columns or y not in df.columns:
        empty("No data to chart.")
        return
    data = df.head(top).copy()
    alt = _alt()
    if alt is None:
        st.bar_chart(data.set_index(x)[y])
        return
    try:
        fmt = "$,.0f" if money else ",.0f"
        ax, ay = (alt.Y, alt.X) if horizontal else (alt.X, alt.Y)
        enc_cat = ax(f"{x}:N", sort="-x" if horizontal else None, title=_pretty_col(x))
        enc_val = ay(f"{y}:Q", title=_pretty_col(y), axis=alt.Axis(format=fmt))
        chart = (alt.Chart(data).mark_bar(color="#38bdf8", cornerRadius=2)
                 .encode(x=enc_val if horizontal else enc_cat,
                         y=enc_cat if horizontal else enc_val,
                         tooltip=[alt.Tooltip(f"{x}:N", title=_pretty_col(x)),
                                  alt.Tooltip(f"{y}:Q", title=_pretty_col(y), format=fmt)]))
        if title:
            chart = chart.properties(title=title)
        st.altair_chart(chart, use_container_width=True)
    except Exception:
        st.bar_chart(data.set_index(x)[y])


def alt_line(df: pd.DataFrame, x: str, ys, money: bool = False, title: str = "") -> None:
    ys = [ys] if isinstance(ys, str) else list(ys)
    if df is None or df.empty or x not in df.columns or not all(y in df.columns for y in ys):
        empty("No data to chart.")
        return
    alt = _alt()
    if alt is None:
        st.line_chart(df.set_index(x)[ys])
        return
    try:
        fmt = "$,.0f" if money else ",.0f"
        long = df[[x] + ys].melt(id_vars=[x], var_name="Series", value_name="Value")
        long["Series"] = long["Series"].map(_pretty_col)
        chart = (alt.Chart(long).mark_line(point=False, strokeWidth=2)
                 .encode(x=alt.X(f"{x}:T", title=_pretty_col(x)),
                         y=alt.Y("Value:Q", title="", axis=alt.Axis(format=fmt)),
                         color=alt.Color("Series:N", title="",
                                         scale=alt.Scale(range=["#38bdf8", "#f59e0b", "#22c55e", "#c084fc"])),
                         tooltip=[alt.Tooltip(f"{x}:T"), "Series:N",
                                  alt.Tooltip("Value:Q", format=fmt)]))
        if title:
            chart = chart.properties(title=title)
        st.altair_chart(chart, use_container_width=True)
    except Exception:
        st.line_chart(df.set_index(x)[ys])


# --------------------------------------------------------------------------
# States
# --------------------------------------------------------------------------

def empty(msg: str) -> None:
    """Neutral empty state — grey, NOT green (green is reserved for verified-healthy)."""
    st.markdown(f"<div class='sm-empty'>{html.escape(str(msg))}</div>", unsafe_allow_html=True)


def healthy(msg: str) -> None:
    st.success(msg)


def loading(msg: str = "Loading…"):
    """Context manager for a spinner with real copy: `with loading('Loading cost…'):`."""
    return st.spinner(msg)


# --------------------------------------------------------------------------
# Sub-view selector — replaces eager st.tabs so only the chosen view queries
# --------------------------------------------------------------------------

def subview(options: list[str], key: str, label: str = "View") -> str:
    kwargs = {"horizontal": True, "label_visibility": "collapsed"}
    try:
        return st.radio(label, options, key=f"sv_{key}", **kwargs)
    except TypeError:
        try:
            return st.radio(label, options, key=f"sv_{key}", horizontal=True)
        except TypeError:
            return st.radio(label, options, key=f"sv_{key}")
