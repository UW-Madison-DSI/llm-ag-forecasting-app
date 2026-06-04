"""Optional LLM helpers — three opt-in features powered by an OpenAI key.

  1. **Ask about this forecast** (Disease tab)
       render_chat_expander(forecast_date, disease_label, df, risk_field, class_field)
  2. **Ask about these trends** (Risk Trends tab)
       render_trends_chat_expander(forecast_date, disease_label, df, risk_field,
                                   class_field, selected_sids)
  3. **Explain this model** (button inside the "About this model" expander)
       render_model_explain_button(disease_label, model_name, model_info)

All three:
  • Read ``OPENAI_API_KEY`` (required) and ``OPENAI_MODEL`` (optional,
    default ``gpt-4o-mini``) from ``os.environ`` on each call — never
    embedded in source.
  • Render an instructive setup hint when the key is unset, so the
    feature is invisible in dev/demo deploys.
  • Feed the model ONLY the data/metadata already on screen plus the
    disease's encyclopedia URL. Never an advice-shaped prompt.
  • Refuse treatment/spray/agronomic recommendations via a baked-in
    system policy and refer users to UW Extension.
  • Support a ``lang`` parameter (English or Spanish reply) so the same
    answer is accessible to Spanish-speaking growers.
"""

from __future__ import annotations

import os
from typing import Iterable

import pandas as pd
import streamlit as st

# Map disease label → authoritative reference URL. Mirrors the
# "Crop risk models" section of README.md so the model cites the same
# source the user reads.
DISEASE_REFERENCES: dict[str, str] = {
    "Tar Spot (corn)":
        "https://cropprotectionnetwork.org/encyclopedia/tar-spot-of-corn",
    "Gray Leaf Spot (corn)":
        "https://cropprotectionnetwork.org/encyclopedia/gray-leaf-spot-of-corn",
    "Frogeye Leaf Spot (soybean)":
        "https://cropprotectionnetwork.org/encyclopedia/frogeye-leaf-spot-of-soybean",
    "White Mold — Non-irrigated (soybean)":
        "https://cropprotectionnetwork.org/encyclopedia/white-mold-of-soybean",
    "White Mold — Irrigated 30in (soybean)":
        "https://cropprotectionnetwork.org/encyclopedia/white-mold-of-soybean",
    "White Mold — Irrigated 15in (soybean)":
        "https://cropprotectionnetwork.org/encyclopedia/white-mold-of-soybean",
}

# Two-letter ISO 639-1 codes the chat understands.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "es": "Spanish (español)",
}

_BASE_SYSTEM_PROMPT = """\
You are an assistant that explains daily crop-disease risk forecasts to
farmers, extension agents, and students using the Wisconsin Agricultural
Forecasting Advisory System (UW–Madison).

Your job is ONLY to:
  • Explain what the data on screen shows — risk values, classes, station
    locations, distributions, trends across dates.
  • Describe what the disease model is and cite the encyclopedia URL
    when one is provided in the context.
  • Translate technical terms (GDD, sentinel -1, "Inactive" model state,
    etc.) into plain language.

You MUST NOT:
  • Recommend whether or when to spray, treat, or apply fungicides.
  • Recommend specific products, application rates, or agronomic actions.
  • Predict yield or financial outcomes.

When asked about treatment, respond: "For management decisions, please
consult a certified agronomist or your local UW–Madison Extension office
(https://extension.wisc.edu/)."

Be concise — 3-5 sentences unless asked for detail. Cite the
encyclopedia URL the user is already viewing whenever it's relevant.
"""

_LANG_INSTRUCTIONS = {
    "en": "Reply in English.",
    "es": (
        "Responde en español. Usa un lenguaje claro y técnico apropiado "
        "para productores agrícolas y agentes de extensión. Cuando "
        "mantengas términos científicos en inglés, inclúyelos entre "
        "paréntesis después de la traducción."
    ),
}

DEFAULT_MODEL = "gpt-4o-mini"

# Hard caps so a runaway prompt can't quietly burn tokens.
MAX_STATIONS_IN_CONTEXT = 25
MAX_TREND_ROWS_PER_STATION = 7
MAX_QUESTION_CHARS = 500


# ===========================================================================
# Pure helpers — tested in tests/test_llm.py
# ===========================================================================

def reference_url_for(disease_label: str) -> str | None:
    """Authoritative reference URL for a disease label, or None."""
    return DISEASE_REFERENCES.get(disease_label)


def summarize_class_counts(class_series: Iterable[str]) -> dict[str, int]:
    """Tally `{class_label: count}` for the model's class column."""
    counts: dict[str, int] = {}
    for c in class_series:
        key = str(c) if c is not None else "Unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def system_prompt_for(lang: str = "en") -> str:
    """Append a reply-language instruction to the base policy prompt."""
    code = lang if lang in SUPPORTED_LANGUAGES else "en"
    return _BASE_SYSTEM_PROMPT + "\n" + _LANG_INSTRUCTIONS[code]


def build_forecast_context(
    forecast_date: str,
    disease_label: str,
    df: pd.DataFrame,
    risk_field: str,
    class_field: str,
) -> str:
    """Render the on-screen single-day forecast into a compact context block."""
    parts: list[str] = []
    parts.append(f"Forecast date: {forecast_date}")
    parts.append(f"Disease model: {disease_label}")
    ref = reference_url_for(disease_label)
    if ref:
        parts.append(f"Reference (cite this URL when relevant): {ref}")

    if df.empty:
        parts.append("(No station data returned for this date.)")
        return "\n".join(parts)

    parts.append(f"Total stations: {len(df)}")

    if class_field in df.columns:
        counts = summarize_class_counts(df[class_field].astype(str))
        nice = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        parts.append(f"Class distribution: {nice}")

    cols = [c for c in ("station_name", "city", "county", risk_field, class_field)
            if c in df.columns]
    if cols and risk_field in df.columns:
        sortable = df[cols].copy()
        sortable[risk_field] = pd.to_numeric(sortable[risk_field], errors="coerce")
        active = sortable[sortable[risk_field].notna() & (sortable[risk_field] != -1)]
        if not active.empty:
            top = active.sort_values(risk_field, ascending=False).head(MAX_STATIONS_IN_CONTEXT)
            parts.append("Top stations by risk:")
            for _, row in top.iterrows():
                line = f"  - {row.get('station_name', '?')}"
                loc = ", ".join(str(row.get(k)) for k in ("city", "county") if row.get(k))
                if loc:
                    line += f" ({loc})"
                line += f": {row[risk_field]:.3f} [{row.get(class_field, '?')}]"
                parts.append(line)

    return "\n".join(parts)


def build_trends_context(
    forecast_date: str,
    disease_label: str,
    df: pd.DataFrame,
    risk_field: str,
    class_field: str,
) -> str:
    """Render the multi-day Risk Trends data into a compact context block.

    ``df`` is the post-filter trends frame: one row per (station, date),
    sorted by ``["station_name", "plot_date"]``.
    """
    parts: list[str] = []
    parts.append(f"View date: {forecast_date}")
    parts.append(f"Disease model: {disease_label}")
    ref = reference_url_for(disease_label)
    if ref:
        parts.append(f"Reference (cite this URL when relevant): {ref}")

    if df.empty:
        parts.append("(No multi-day data available.)")
        return "\n".join(parts)

    date_col = "plot_date" if "plot_date" in df.columns else "date"
    dates = sorted(set(str(d)[:10] for d in df[date_col].dropna()))
    if dates:
        parts.append(f"Date window: {dates[0]} → {dates[-1]} ({len(dates)} day(s))")

    stations = sorted(set(df["station_name"].astype(str)))
    parts.append(f"Stations selected: {len(stations)}")

    if class_field in df.columns:
        counts = summarize_class_counts(df[class_field].astype(str))
        nice = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        parts.append(f"Class distribution across (station × day) rows: {nice}")

    # Per-station trajectory — capped to keep the prompt small.
    parts.append("Per-station trajectory (latest first):")
    for name in stations[:MAX_STATIONS_IN_CONTEXT]:
        sub = (df[df["station_name"].astype(str) == name]
               .sort_values(date_col).tail(MAX_TREND_ROWS_PER_STATION))
        if sub.empty:
            continue
        pts = []
        for _, row in sub.iterrows():
            d = str(row.get(date_col, ""))[:10]
            v = row.get(risk_field)
            cls = row.get(class_field, "?")
            if pd.isna(v) or v == -1:
                pts.append(f"{d}=Inactive")
            else:
                pts.append(f"{d}={float(v):.2f}[{cls}]")
        parts.append(f"  - {name}: " + " → ".join(pts))

    if len(stations) > MAX_STATIONS_IN_CONTEXT:
        parts.append(
            f"  (+ {len(stations) - MAX_STATIONS_IN_CONTEXT} more stations not shown)"
        )

    return "\n".join(parts)


def build_model_context(
    disease_label: str,
    model_name: str,
    model_info: dict | None,
) -> str:
    """Compact summary of one disease model's metadata for the explain button."""
    parts: list[str] = []
    parts.append(f"Disease model: {disease_label}")
    parts.append(f"Internal model name: {model_name}")
    ref = reference_url_for(disease_label)
    if ref:
        parts.append(f"Reference (cite this URL): {ref}")

    if not model_info:
        parts.append("(No additional metadata returned by the API.)")
        return "\n".join(parts)

    if model_info.get("crop"):
        parts.append(f"Crop: {model_info['crop']}")
    if model_info.get("version"):
        parts.append(f"Version: {model_info['version']}")
    if model_info.get("description"):
        parts.append(f"Description: {model_info['description']}")
    if model_info.get("model_type"):
        parts.append(f"Model type: {model_info['model_type']}")
    if model_info.get("risk_output"):
        parts.append(f"Risk output: {model_info['risk_output']}")
    if model_info.get("inactive_rule"):
        parts.append(f"Inactive rule: {model_info['inactive_rule']}")
    variables = model_info.get("variables") or []
    if variables:
        parts.append("Input variables: " + ", ".join(str(v) for v in variables))

    return "\n".join(parts)


# ===========================================================================
# OpenAI call
# ===========================================================================

def _get_client():
    """Lazy import + construct OpenAI client so missing `openai` doesn't
    block the rest of the app from loading."""
    try:
        from openai import OpenAI
    except ImportError as err:
        raise RuntimeError(
            "The `openai` package is not installed. "
            "Run `pip install openai` to enable the chat features."
        ) from err
    return OpenAI()  # reads OPENAI_API_KEY from env automatically


def ask_about_forecast(
    question: str,
    context: str,
    lang: str = "en",
    model: str | None = None,
) -> str:
    """Send (question + context) to the LLM and return the plain-text reply."""
    if not question.strip():
        return ""
    question = question[:MAX_QUESTION_CHARS]

    client = _get_client()
    chosen_model = model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)

    resp = client.chat.completions.create(
        model=chosen_model,
        temperature=0.2,
        max_tokens=500,
        messages=[
            {"role": "system", "content": system_prompt_for(lang)},
            {"role": "user", "content":
                f"Current forecast context:\n\n{context}\n\n"
                f"User question:\n{question}"
            },
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def explain_model(context: str, lang: str = "en", model: str | None = None) -> str:
    """Ask the LLM to explain a disease model in plain language."""
    client = _get_client()
    chosen_model = model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)

    resp = client.chat.completions.create(
        model=chosen_model,
        temperature=0.2,
        max_tokens=500,
        messages=[
            {"role": "system", "content": system_prompt_for(lang)},
            {"role": "user", "content":
                f"Model context:\n\n{context}\n\n"
                "Please explain in plain language: what this model predicts, "
                "what inputs it uses, when it's considered Inactive, and how "
                "to read its output. Cite the reference URL above."
            },
        ],
    )
    return (resp.choices[0].message.content or "").strip()


# ===========================================================================
# Streamlit fragments
# ===========================================================================

_LANG_RADIO_LABELS = {"en": "🇺🇸 English", "es": "🇪🇸 Español"}


def _language_selector(key: str) -> str:
    """Tiny inline language picker. Returns "en" or "es"."""
    choice = st.radio(
        "Reply language",
        options=list(_LANG_RADIO_LABELS.keys()),
        format_func=lambda c: _LANG_RADIO_LABELS[c],
        horizontal=True,
        key=key,
        label_visibility="collapsed",
    )
    return choice


def _api_key_present() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _setup_hint() -> None:
    st.info(
        "Set the `OPENAI_API_KEY` environment variable before launching "
        "Streamlit to enable AI-assisted explanations. "
        "Leave it unset to hide this feature entirely."
    )


def _disclaimer() -> None:
    st.caption(
        "_This feature summarizes the data on screen only — it does not "
        "provide management recommendations. For treatment decisions, "
        "consult a certified agronomist or your local "
        "[UW–Madison Extension office](https://extension.wisc.edu/)._"
    )


def render_chat_expander(
    forecast_date: str,
    disease_label: str,
    df: pd.DataFrame,
    risk_field: str,
    class_field: str,
) -> None:
    """Render the "Ask about this forecast" expander on the Disease tab."""
    with st.expander("💬 Ask about this forecast (AI)", expanded=False):
        if not _api_key_present():
            _setup_hint()
            return

        st.caption(
            "Ask a plain-language question about the data on screen — *why is "
            "Arlington High this week?*, *what does GDD mean?*, *which counties "
            "have the most stations at risk?*"
        )

        lang = _language_selector(key=f"llm_lang_{disease_label}")
        question = st.text_area(
            "Your question",
            key=f"llm_q_{disease_label}",
            max_chars=MAX_QUESTION_CHARS,
            placeholder="Why is the risk Inactive at every station this week?",
            label_visibility="collapsed",
        )
        if st.button("Ask", key=f"llm_ask_{disease_label}", type="primary") and question.strip():
            context = build_forecast_context(
                forecast_date, disease_label, df, risk_field, class_field
            )
            try:
                with st.spinner("Thinking…"):
                    answer = ask_about_forecast(question, context, lang=lang)
            except Exception as err:  # noqa: BLE001
                st.error(f"LLM call failed: **{type(err).__name__}** — {err}")
                return
            if answer:
                st.markdown(answer)
            else:
                st.warning("The model returned an empty response.")

        _disclaimer()


def render_trends_chat_expander(
    forecast_date: str,
    disease_label: str,
    df: pd.DataFrame,
    risk_field: str,
    class_field: str,
) -> None:
    """Render the "Ask about these trends" expander on the Risk Trends tab."""
    with st.expander("💬 Ask about these trends (AI)", expanded=False):
        if not _api_key_present():
            _setup_hint()
            return

        st.caption(
            "Ask a plain-language question about the multi-day trends on "
            "screen — *which station's risk is rising fastest?*, *when did "
            "the most stations cross into High?*, *what does the gap on day 3 mean?*"
        )

        lang = _language_selector(key=f"llm_trends_lang_{disease_label}")
        question = st.text_area(
            "Your question",
            key=f"llm_trends_q_{disease_label}",
            max_chars=MAX_QUESTION_CHARS,
            placeholder="Which station's risk is rising the fastest?",
            label_visibility="collapsed",
        )
        if st.button("Ask", key=f"llm_trends_ask_{disease_label}", type="primary") and question.strip():
            context = build_trends_context(
                forecast_date, disease_label, df, risk_field, class_field
            )
            try:
                with st.spinner("Thinking…"):
                    answer = ask_about_forecast(question, context, lang=lang)
            except Exception as err:  # noqa: BLE001
                st.error(f"LLM call failed: **{type(err).__name__}** — {err}")
                return
            if answer:
                st.markdown(answer)
            else:
                st.warning("The model returned an empty response.")

        _disclaimer()


def render_model_explain_button(
    disease_label: str,
    model_name: str,
    model_info: dict | None,
) -> None:
    """Render a small "Explain in plain language" button — meant to live
    inside the existing "About this model" expander on the Disease tab.
    """
    if not _api_key_present():
        # Silently no-op when the key is unset — the existing
        # model_info metadata already covers the basics.
        return

    st.markdown("---")
    lang = _language_selector(key=f"llm_model_lang_{model_name}")
    if st.button(
        "🤖 Explain this model in plain language",
        key=f"llm_explain_{model_name}",
    ):
        context = build_model_context(disease_label, model_name, model_info)
        try:
            with st.spinner("Thinking…"):
                answer = explain_model(context, lang=lang)
        except Exception as err:  # noqa: BLE001
            st.error(f"LLM call failed: **{type(err).__name__}** — {err}")
            return
        if answer:
            st.markdown(answer)
        else:
            st.warning("The model returned an empty response.")
