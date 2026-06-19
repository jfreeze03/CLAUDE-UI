"""Shared helpers for pages."""

from __future__ import annotations

import html

import streamlit as st

import config


def inject_global_styles() -> None:
    """Apply SnowMonitor's visual system once from the app shell."""
    st.markdown(
        """
<style>
    :root {
        --sm-bg: #07111f;
        --sm-panel: rgba(15, 23, 42, 0.74);
        --sm-panel-strong: rgba(15, 23, 42, 0.92);
        --sm-line: rgba(148, 163, 184, 0.22);
        --sm-sky: #38bdf8;
        --sm-violet: #c084fc;
        --sm-amber: #fbbf24;
        --sm-text: #e2e8f0;
        --sm-muted: #94a3b8;
    }

    [data-testid="stAppViewContainer"] {
        background:
            radial-gradient(circle at 15% 5%, rgba(56, 189, 248, 0.18), transparent 28rem),
            radial-gradient(circle at 92% 10%, rgba(192, 132, 252, 0.14), transparent 24rem),
            linear-gradient(135deg, #07111f 0%, #0f172a 48%, #111827 100%);
    }

    [data-testid="stHeader"] {
        background: transparent;
    }

    .block-container {
        max-width: 1500px;
        padding-top: 2rem;
        padding-bottom: 3rem;
    }

    section[data-testid="stSidebar"] > div {
        background:
            linear-gradient(180deg, rgba(15, 23, 42, 0.98), rgba(2, 6, 23, 0.98)),
            radial-gradient(circle at top left, rgba(56, 189, 248, 0.18), transparent 16rem);
        border-right: 1px solid var(--sm-line);
    }

    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1 {
        letter-spacing: -0.04em;
    }

    div[data-testid="stMetric"],
    div[data-testid="stDataFrame"],
    div[data-testid="stExpander"],
    div[data-testid="stAlert"] {
        border-radius: 18px;
    }

    div[data-testid="stMetric"] {
        padding: 1rem 1rem 0.8rem;
        background:
            linear-gradient(145deg, rgba(15, 23, 42, 0.92), rgba(30, 41, 59, 0.68)),
            radial-gradient(circle at top right, rgba(56, 189, 248, 0.16), transparent 9rem);
        border: 1px solid var(--sm-line);
        box-shadow: 0 18px 48px rgba(2, 6, 23, 0.28);
    }

    div[data-testid="stMetric"] label {
        color: var(--sm-muted);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.74rem;
    }

    .stButton > button,
    .stDownloadButton > button {
        border-radius: 999px;
        border: 1px solid rgba(56, 189, 248, 0.32);
        box-shadow: 0 10px 26px rgba(2, 6, 23, 0.18);
        transition: transform 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
    }

    .stButton > button:hover,
    .stDownloadButton > button:hover {
        border-color: rgba(56, 189, 248, 0.72);
        box-shadow: 0 14px 34px rgba(2, 6, 23, 0.28);
        transform: translateY(-1px);
    }

    .sm-page-hero {
        position: relative;
        overflow: hidden;
        margin: 0 0 1.15rem;
        padding: 1.1rem 1.25rem 1.2rem;
        background:
            linear-gradient(135deg, rgba(15, 23, 42, 0.90), rgba(15, 23, 42, 0.58)),
            radial-gradient(circle at 85% 20%, rgba(56, 189, 248, 0.20), transparent 16rem);
        border: 1px solid var(--sm-line);
        border-radius: 24px;
        box-shadow: 0 24px 70px rgba(2, 6, 23, 0.30);
    }

    .sm-page-hero:after {
        content: "";
        position: absolute;
        inset: auto -10% -42% 45%;
        height: 9rem;
        background: linear-gradient(90deg, transparent, rgba(56, 189, 248, 0.16), transparent);
        transform: rotate(-6deg);
    }

    .sm-page-kicker {
        color: var(--sm-sky);
        font-size: 0.74rem;
        font-weight: 700;
        letter-spacing: 0.14em;
        text-transform: uppercase;
    }

    .sm-page-hero h1 {
        margin: 0.15rem 0 0.15rem;
        color: var(--sm-text);
        font-size: clamp(2rem, 4vw, 3.5rem);
        line-height: 0.95;
        letter-spacing: -0.06em;
    }

    .sm-page-subtitle {
        max-width: 56rem;
        margin: 0.35rem 0 0;
        color: #cbd5e1;
        font-size: 1rem;
    }

    .sm-page-scope {
        display: inline-flex;
        gap: 0.55rem;
        flex-wrap: wrap;
        margin-top: 0.85rem;
        color: var(--sm-muted);
        font-size: 0.84rem;
    }

    .sm-page-scope span {
        padding: 0.28rem 0.62rem;
        background: rgba(15, 23, 42, 0.72);
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 999px;
    }

    .sm-cost-picker {
        margin: 0.25rem 0 1rem;
        padding: 0.8rem 1rem;
        background: rgba(15, 23, 42, 0.58);
        border: 1px solid var(--sm-line);
        border-radius: 20px;
    }
</style>
        """,
        unsafe_allow_html=True,
    )


def scope() -> tuple[str, str, int]:
    return (
        st.session_state.get("company", config.DEFAULT_COMPANY),
        st.session_state.get("environment", config.DEFAULT_ENVIRONMENT),
        int(st.session_state.get("days", config.DEFAULT_LOOKBACK_DAYS)),
    )


def header(title: str, subtitle: str = "") -> None:
    company, env, days = scope()
    safe_title = html.escape(title)
    safe_subtitle = html.escape(subtitle)
    safe_company = html.escape(company)
    safe_env = html.escape(env)
    safe_freshness = html.escape(config.ACCOUNT_USAGE_FRESHNESS)
    st.markdown(
        f"""
<div class="sm-page-hero">
    <div class="sm-page-kicker">SnowMonitor control room</div>
    <h1>{safe_title}</h1>
    <p class="sm-page-subtitle">{safe_subtitle}</p>
    <div class="sm-page-scope">
        <span>{safe_company}</span>
        <span>{safe_env}</span>
        <span>{days}d lookback</span>
        <span>{safe_freshness}</span>
    </div>
</div>
        """,
        unsafe_allow_html=True,
    )


SEVERITY_EMOJI = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "⚪"}


def md_escape(text: object) -> str:
    """Escape Markdown specials so dynamic text renders literally.

    Critically prevents '$' (which Streamlit reads as LaTeX math) and '_'/'*'
    (italics/bold) in values like '$1,220/mo' or 'AUTO_SUSPEND' from garbling.
    """
    s = str(text if text is not None else "")
    for ch in ("\\", "$", "_", "*", "`"):
        s = s.replace(ch, "\\" + ch)
    return s

