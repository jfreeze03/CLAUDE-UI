"""Streamlit compatibility shim — makes SnowMonitor run on older runtimes.

Snowflake Streamlit-in-Snowflake may run an older default Streamlit (e.g. ~1.22)
than the version shown in the package picker, unless the version is explicitly
pinned. This module polyfills functions and strips unsupported keyword arguments
that newer Streamlit added, so the same code runs on old and new runtimes.

Import this **first** (before rendering anything). It patches the shared `streamlit`
module object, so every module that does `import streamlit as st` gets the shims.

Covered:
  - st.rerun            (added 1.27)  -> falls back to st.experimental_rerun
  - st.divider          (added 1.23)  -> falls back to a markdown rule
  - st.container(border=) (added 1.29) -> strips `border` if unsupported
  - st.dataframe(hide_index=/use_container_width=) -> strips if unsupported
  - st.button(use_container_width=)    -> strips if unsupported
"""

from __future__ import annotations

import inspect

import streamlit as st


def _supports(fn, param: str) -> bool:
    try:
        return param in inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return True  # builtin / not introspectable -> assume supported


# st.rerun (1.27)
if not hasattr(st, "rerun"):
    if hasattr(st, "experimental_rerun"):
        st.rerun = st.experimental_rerun           # type: ignore[attr-defined]
    else:  # pragma: no cover - extremely old
        st.rerun = lambda: None                    # type: ignore[attr-defined]

# st.divider (1.23)
if not hasattr(st, "divider"):
    st.divider = lambda: st.markdown("---")        # type: ignore[attr-defined]

# st.container(border=...) (1.29)
if not _supports(st.container, "border"):
    _orig_container = st.container

    def _container(*args, **kwargs):
        kwargs.pop("border", None)
        return _orig_container(*args, **kwargs)

    st.container = _container                       # type: ignore[assignment]

# st.dataframe(hide_index=..., use_container_width=...)
_orig_dataframe = st.dataframe
_df_hide = _supports(_orig_dataframe, "hide_index")
_df_ucw = _supports(_orig_dataframe, "use_container_width")
if not (_df_hide and _df_ucw):
    def _dataframe(*args, **kwargs):
        if not _df_hide:
            kwargs.pop("hide_index", None)
        if not _df_ucw:
            kwargs.pop("use_container_width", None)
        return _orig_dataframe(*args, **kwargs)

    st.dataframe = _dataframe                       # type: ignore[assignment]

# st.button(use_container_width=...)
_orig_button = st.button
if not _supports(_orig_button, "use_container_width"):
    def _button(*args, **kwargs):
        kwargs.pop("use_container_width", None)
        return _orig_button(*args, **kwargs)

    st.button = _button                            # type: ignore[assignment]


def applied() -> bool:
    """Trivial hook so importers can reference the module without lint noise."""
    return True
