# WI Agricultural Forecasting Advisory System

Two front-ends for the same UW–Madison Ag Forecasting API + Wisconet
weather-station data:

| Front-end | Status | Location | Run with |
|---|---|---|---|
| **Streamlit app** | **Live** — production dashboard | [`app.py`](app.py) + [`streamlit_app/`](streamlit_app/) | `streamlit run app.py` |
| **Static website** | In progress | [`web/`](web/) | `cd web && docker compose up -d --build` |

The two share the underlying model + API code in [`features/`](features/) so
there's a single source of truth for the forecast math.

---

## Repository layout

```
ag_forecasting_app_v3/
├── app.py                  # Streamlit entrypoint (thin — just wires the pieces)
├── streamlit_app/          # Streamlit UI (live, modular)
│   ├── analytics.py        #   GA bootstrap (reads GA_MEASUREMENT_ID)
│   ├── page.py             #   set_page_config + logo + title
│   ├── sidebar.py          #   sidebar controls + White-Mold sub-radio
│   ├── ui.py               #   metric tile + footer
│   ├── main.py             #   top-level composition
│   └── tabs/               #   one module per tab
│       ├── forecast.py     #     Disease Forecast
│       ├── trends.py       #     Risk Trends
│       ├── weather.py      #     Weather Data
│       └── biomass.py      #     Cereal Rye Biomass
│
├── features/               # Shared model + API code (used by BOTH front-ends)
│   ├── api.py              #   Cached HTTP client for the forecasting API
│   ├── config.py           #   API URL, TTL, disease catalog, palette
│   ├── data.py             #   Payload → tidy DataFrame, class normalization
│   ├── weather.py          #   wiscopy weather fetcher
│   ├── crereal_rye_biomass.py  #   Cereal rye biomass NLS model
│   └── map_view.py         #   Plotly map figure
│
├── web/                    # IN-PROGRESS static website
│   ├── backend/main.py     #   FastAPI proxy (/proxy/forecast, …)
│   ├── build_site.py       #   Builds site/data/latest.json from the API
│   ├── site/               #   Static HTML/JS/CSS (Leaflet + Chart.js)
│   ├── Dockerfile          #   nginx + uvicorn in one container
│   ├── docker-compose.yml  #   Traefik labels for the DSI VM deploy
│   ├── nginx.conf
│   └── start.sh
│
├── assets/                 # UW logos (shared)
├── tests/                  # Pytest suite (pure-function tests)
├── requirements.txt        # Shared deps (covers both front-ends)
└── .github/workflows/build-data.yml   # Daily snapshot job for the website
```

---

## Streamlit app (live)

The production dashboard. Composes a sidebar (date · risk-days slider ·
disease model) with three tabs:

- **🌽 Disease Forecast** — metric tiles + Wisconsin map + station table
- **📈 Risk Trends** — N-day per-station risk line chart + class-distribution bar
- **🌤 Weather Data** — multi-station wiscopy time-series for any field

Plus a dedicated single-station **Cereal Rye Biomass** view when that
model is selected.

### Quick start

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Opens at <http://localhost:8501>.

### Optional: Google Analytics

```bash
export GA_MEASUREMENT_ID=G-XXXXXXXXXX
streamlit run app.py
```

The measurement id is read from `os.environ` at startup; leave the env
var unset to disable analytics. See [`streamlit_app/analytics.py`](streamlit_app/analytics.py).

### Optional: AI-assisted explanations (OpenAI)

When `OPENAI_API_KEY` is set, three opt-in features light up across the
app — each is invisible when the key is unset, so dev/demo deploys
without a key look exactly like before:

| Where | What it does |
|---|---|
| **💬 Ask about this forecast** — Disease Forecast tab | Plain-language Q&A about the single-day map ("why is Arlington High?", "which counties have the most stations at risk?") |
| **💬 Ask about these trends** — Risk Trends tab | Same, scoped to the multi-day per-station trajectories ("which station is rising fastest?", "when did most stations cross into High?") |
| **🤖 Explain this model** — button inside *About this model* | Plain-language summary of the selected disease model — what it predicts, what inputs it uses, the inactive rule, how to read its output |

All three:

- **Reply language toggle** — 🇺🇸 English / 🇪🇸 Español radio per expander,
  so the same explanation is accessible to Spanish-speaking growers and
  extension agents without leaving the app.
- **No treatment recommendations.** A baked-in system prompt forbids
  spray/product/timing advice; on those questions the model refers
  users to a certified agronomist or
  [UW–Madison Extension](https://extension.wisc.edu/).
- **Cites the encyclopedia.** Each disease's authoritative reference
  URL (from *Crop risk models* below) is injected into the context, so
  replies link back to the same source the user can read independently.

```bash
export OPENAI_API_KEY=sk-...
# Optional override; defaults to gpt-4o-mini (~$0.0001/query):
export OPENAI_MODEL=gpt-4o-mini
streamlit run app.py
```

Both env vars are read on every call — no source-level secrets. See
[`streamlit_app/llm.py`](streamlit_app/llm.py).

### Cached forecast calls

`features/api.py:fetch_forecast()` is decorated with
`@st.cache_data(ttl=86_400, persist="disk")` so a given
`(forecasting_date, risk_days)` pair hits the API at most once per day,
even across app restarts. The sidebar **🔄 Refresh data** button calls
`fetch_forecast.clear()` to force a re-fetch.

---

## Static website (in progress)

Deployed at <https://ag-forecasting.services.dsi.wisc.edu>. All
website-specific code lives under [`web/`](web/) — same Python helpers
as the Streamlit app, fronted by a FastAPI proxy + Leaflet/Chart.js UI.

```bash
cd web
docker compose up -d --build
```

The Docker build sets its context to the repo root so the image can pull
in `features/` and `assets/` alongside `web/site/` and `web/backend/`.

Daily data refresh is handled by [`.github/workflows/build-data.yml`](.github/workflows/build-data.yml),
which runs `python web/build_site.py` on a cron and commits the
regenerated snapshot back to the repo.

---

## Tests

```bash
pip install pytest
pytest
```

Covers the pure helpers shared by both front-ends:

- `features/data.py` — `normalize_class`, `flatten_features`, `prepare_disease_df`
- `features/crereal_rye_biomass.py` — `predict_rye_biomass`, `sine_gdd`,
  `classify_biomass`, the unit converters
- `streamlit_app/sidebar.py` — `build_visible_options`, `resolve_disease_label`
- `streamlit_app/tabs/forecast.py` — `count_risk_buckets`
- `streamlit_app/tabs/biomass.py` — `_default_plant_date`

UI-shaped logic (anything inside a `render_*_tab`) is exercised through
manual testing or `streamlit.testing.v1.AppTest`; the pure helpers are
where most of the bugs live, and they're fully covered here.

---

## Crop risk models

Selected field-crop outputs are provided.

### Field-crop diseases

- **White mold (soybean)** — <https://cropprotectionnetwork.org/encyclopedia/white-mold-of-soybean>
- **Frogeye leaf spot (soybean)** — <https://cropprotectionnetwork.org/encyclopedia/frogeye-leaf-spot-of-soybean>
- **Gray leaf spot (corn)** — <https://cropprotectionnetwork.org/encyclopedia/gray-leaf-spot-of-corn>
- **Tar spot (corn)** — <https://cropprotectionnetwork.org/encyclopedia/tar-spot-of-corn>
- **Cereal Rye Biomass** - N/A


---

## API reference

```
GET https://connect.doit.wisc.edu/ag_forecasting_api/v2/ag_models_wrappers/wisconet_g
    ?forecasting_date=YYYY-MM-DD
    &risk_days=N           # 1–7
```

Response is a FeatureCollection-style object: a top-level `fields`
schema plus a `features` list, each containing a `station` block (id,
name, lat/lon, county, region) and a `timeseries` list of daily
records. `features/data.py:flatten_features()` pivots
`timeseries[].data[]` key/value pairs into named columns.

### Risk values

- Numeric `*_risk` fields are in `[0, 1]`, with `-1` meaning "model
  inactive for this station/date".
- `*_class` fields hold the discrete label; during the active season
  the API sometimes prefixes them with a sort key (`"1.Low"`,
  `"2.Moderate"`, `"3.High"`). `normalize_class` strips the prefix so
  the values match `CLASS_COLORS` / `CLASS_ORDER`.
- Out of season, all stations report `-1` / `Inactive` — the map will
  be mostly gray. Pick a mid-growing-season date to see live colors.

---

## Credits and contacts

- **Damon Smith** — Extension Field Crops Pathologist
  · <damon.smith@wisc.edu>
- **María Oros** — Data Scientist, Data Science Institute at UW–Madison
  · <moros2@wisc.edu>
