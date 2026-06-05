/* Main controller for the dashboard. Composes the Disease Forecast,
   Risk Trends, and Weather Data tabs, wiring them to the FastAPI
   /proxy/* endpoints. Bundled latest.json is used as a fast first
   paint; for today's date we replace it with a live API call so
   stale-container deploys don't show stale data. */

(function () {
  "use strict";

  const DATA_URL = "data/latest.json";

  /* Fire a Google Analytics (GA4) custom event for feature-usage tracking.
     No-ops when gtag isn't loaded (GA_MEASUREMENT_ID unset). Log feature
     names / UI choices only — never field-level location or personal data. */
  function track(event, params) {
    try {
      if (typeof window.gtag === "function") window.gtag("event", event, params || {});
    } catch (e) { /* analytics must never break the UI */ }
  }

  // Three white-mold variants live in the snapshot models[] separately.
  // The UI collapses them into one "White Mold (soybean)" entry plus a
  // radio (Non-irrigated / Irr 30in / Irr 15in) that picks the variant.
  const WM_LABELS = {
    non:  "White Mold — Non-irrigated (soybean)",
    "30in": "White Mold — Irrigated 30in (soybean)",
    "15in": "White Mold — Irrigated 15in (soybean)",
  };
  const WM_GROUP_LABEL = "White Mold (soybean)";

  // Mirror of features/config.py:CLASS_COLORS. Used as a fallback when a
  // bundled snapshot omits class_colors — e.g. one built by an older
  // build_site.py, or a degraded build where the model_info/weather
  // upstreams were unreachable. Without this default the renderers index
  // an undefined palette and throw, blanking the whole page (the exact
  // "works locally, blank in deployment" failure: the prod snapshot was
  // missing class_colors while the local one had it).
  const DEFAULT_CLASS_COLORS = {
    "Low":      "#009E73",
    "Moderate": "#E69F00",
    "High":     "#D55E00",
    "Inactive": "#999999",
    "No Risk":  "#009E73",
    "Unknown":  "#CCCCCC",
  };

  // Mirror of features/config.py:WEATHER_FIELDS. Keys are wiscopy field
  // names (passed straight to /proxy/weather); values are human-readable
  // labels shown in the selectbox + chart title. Keep keys verbatim.
  const WEATHER_FIELDS = {
    "60min_air_temp_f_avg":            "Avg temp (°F, hourly)",
    "60min_air_temp_f_min":            "Min temp (°F, hourly)",
    "60min_air_temp_f_max":            "Max temp (°F, hourly)",
    "60min_relative_humidity_pct_avg": "Humidity (%, hourly)",
    "60min_dew_point_temp_f_avg":      "Dew point (°F, hourly)",
    "daily_air_temp_f_avg":            "Avg temp (°F, daily)",
    "daily_rain_in_tot":               "Rainfall (in, daily total)",
    "daily_rainfall_in":               "Rainfall (in, daily)",
    "60min_solar_rad_w_m2_avg":        "Solar radiation (W/m², hourly)",
    "60min_wind_speed_mph_avg":        "Wind speed (mph, hourly)",
  };

  const state = {
    snapshot: null,
    // The current multi-day forecast payload (raw API). We always fetch
    // with whatever the slider is at and re-derive views from this.
    forecastPayload: null,
    forecastRows: null,         // ForecastAPI.flattenForecast(payload)
    forecastDate: null,
    plantDate: null,
    riskDays: 1,
    currentModel: null,         // entry from snapshot.models OR a synthesized WM variant
    map: null,
    biomassResponse: null,      // last /proxy/biomass response
    biomassPlantDate: null,
    biomassPrecip: 200,
    biomassUseReal: true,
    activeTab: "forecast",
  };

  /* ==================================================================
     Boot
     ================================================================== */

  async function boot() {
    try {
      const resp = await fetch(DATA_URL, { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      state.snapshot = await resp.json();
    } catch (err) {
      document.getElementById("metrics").innerHTML =
        `<div class="metric-tile" style="grid-column:1/-1;color:#9B0000">
           <div class="metric-label" style="color:#9B0000">Could not load bundled data</div>
           <div class="metric-value">${escapeHtml(err.message || err)}</div>
           <p class="muted">Run <code>python build_site.py</code>.</p>
         </div>`;
      return;
    }

    // Defensive defaults — a snapshot built by an older build_site.py (or a
    // degraded build where wiscopy / the model_info API was unreachable) can
    // omit these keys. The map/metrics/table renderers index class_colors
    // directly, so a missing palette would throw and blank the whole page.
    // Default every optional key so a partial snapshot degrades gracefully.
    state.snapshot.class_colors = state.snapshot.class_colors || DEFAULT_CLASS_COLORS;
    state.snapshot.model_info = state.snapshot.model_info || {};
    state.snapshot.weather = state.snapshot.weather || {};
    state.snapshot.available_dates = state.snapshot.available_dates || [];

    state.forecastDate = todayIso();
    state.plantDate = state.snapshot.plant_date || defaultPlantDate(state.forecastDate);
    state.currentModel = state.snapshot.models[0];

    // Initial paint from bundled snapshot so the page isn't blank during
    // the live fetch. If the snapshot has today, use it directly; else
    // grab whatever's freshest in it.
    const bundledToday = (state.snapshot.forecasts || {})[state.forecastDate];
    const fallbackKey = (state.snapshot.available_dates || [])[0] || state.snapshot.forecasting_date;
    const initialStations = bundledToday || (state.snapshot.forecasts || {})[fallbackKey] || state.snapshot.stations;
    paintFromBundled(initialStations, bundledToday ? state.forecastDate : fallbackKey);

    setMeta();
    populateModelSelect();
    populateMultiStationPickers();
    populateWeatherFieldSelect();
    initWeatherDateRange();
    bindControls();
    bindTabs();

    state.map = WIMap.initMap("map");
    setDataSource("bundled");
    rerender();
    renderInlineWeatherSection();

    // Always try to refresh with today's live data so the page is current
    // even when the container's bundled snapshot is days old.
    refreshLive("Loading today's forecast…").catch((err) => {
      console.warn("Live boot fetch failed; staying on bundled view.", err);
    });
  }

  function paintFromBundled(stations, isoDate) {
    state.forecastDate = isoDate;
    state.forecastRows = (stations || []).map((s) => Object.assign({}, s));
    // Bundled stations don't carry a per-row date; stamp one for the
    // table/trends views.
    state.forecastRows.forEach((s) => { s.date = isoDate; });
  }

  /* ==================================================================
     Live fetch
     ================================================================== */

  async function refreshLive(statusMsg) {
    const status = document.getElementById("date-status");
    status.innerHTML = `<span class="pending-text">${escapeHtml(statusMsg || "Fetching…")}</span>`;

    try {
      const before = Date.now();
      const payload = await ForecastAPI.fetchForecast(state.forecastDate, state.riskDays);
      const elapsed = ((Date.now() - before) / 1000).toFixed(2);

      state.forecastPayload = payload;
      state.forecastRows = ForecastAPI.flattenForecast(payload, ForecastAPI.normalizeClass);

      setMeta();
      const source = ForecastAPI.lastSource || "proxy";
      setDataSource(source);
      status.innerHTML =
        `<span class="success-text">Loaded ${escapeHtml(state.forecastDate)} ` +
        `(risk_days=${state.riskDays}) in ${elapsed}s.</span>`;

      rerender();
      renderTrendsTab();
      renderInlineWeatherSection();

      // If biomass is the active model, kick off a fresh server-side compute.
      if (state.currentModel && state.currentModel.type === "biomass") {
        runBiomass({ silent: true });
      }
    } catch (err) {
      console.error("refreshLive failed:", err);
      status.innerHTML =
        `<span class="error-text">Live fetch failed: ${escapeHtml(err.message || err)}.</span>` +
        `<br><span class="muted">Showing bundled snapshot instead.</span>`;
    }
  }

  /* ==================================================================
     Disease Forecast tab — metrics, map, model info, table
     ================================================================== */

  function attachModelView(station, model) {
    const out = Object.assign({}, station);
    if (model.type === "biomass") {
      out._value = station.biomass ?? null;
      out._class = station.biomass_class || "Unknown";
    } else {
      const raw = station[model.risk_field];
      out._value = raw === -1 || raw == null ? null : raw;
      out._class = station[model.class_field] || "Unknown";
    }
    return out;
  }

  function latestPerStation(rows) {
    // Multi-day rows → one row per station (most recent date).
    return ForecastAPI.latestRowPerStation(rows);
  }

  async function rerender() {
    const rowsLatest = latestPerStation(state.forecastRows || []);
    const decorated = rowsLatest.map((s) => attachModelView(s, state.currentModel));

    WIMap.renderStations(state.map, decorated, state.currentModel, state.snapshot.class_colors);
    WIRender.renderMetrics(decorated, state.currentModel, state.snapshot.class_colors);
    WIRender.renderTable(decorated, state.currentModel, state.snapshot.class_colors);

    // Model info: try the snapshot's cache first, then hit the proxy.
    const slug = state.currentModel.model_name;
    const cached = (state.snapshot.model_info || {})[slug];
    if (cached) {
      WIRender.renderModelInfo(state.currentModel, state.snapshot.model_info);
    } else {
      const info = await ForecastAPI.fetchModelInfo(slug);
      const mi = Object.assign({}, state.snapshot.model_info || {}, info ? { [slug]: info } : {});
      WIRender.renderModelInfo(state.currentModel, mi);
    }
  }

  /* ==================================================================
     Biomass — live compute via /proxy/biomass (falls back to client replay)
     ================================================================== */

  function showBiomassControls(show) {
    document.getElementById("biomass-controls").hidden = !show;
    document.getElementById("biomass-detail").hidden = !show;
    if (show) {
      populateBiomassDetailStation();
      renderBiomassDetail();
    }
  }

  /* ------------------------------------------------------------------
     Single-station biomass debug — mirrors the Streamlit tab's
     per-station drill-down. Pulls /proxy/weather for one station,
     runs the per-day biomass series in JS (Biomass.biomassDailyRowsFromSeries),
     and renders headline + metric tiles + daily breakdown + raw weather.
     ------------------------------------------------------------------ */

  function populateBiomassDetailStation() {
    const sel = document.getElementById("biomass-detail-station");
    if (!sel) return;
    const wanted = sel.value;
    sel.innerHTML = "";
    const stations = (state.snapshot.stations || []).slice()
      .sort((a, b) => a.name.localeCompare(b.name));
    stations.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = `${s.name} (${s.id})`;
      sel.appendChild(opt);
    });
    // Default to Arlington when nothing is selected yet — same as Streamlit.
    if (wanted) {
      sel.value = wanted;
    } else {
      const arl = stations.find((s) => /arlington/i.test(s.name));
      if (arl) sel.value = arl.id;
    }
    if (!sel.dataset.bound) {
      sel.addEventListener("change", () => {
        track("biomass_station_view", { station: sel.value });
        renderBiomassDetail();
      });
      sel.dataset.bound = "1";
    }
  }

  async function renderBiomassDetail() {
    if (!state.currentModel || state.currentModel.type !== "biomass") return;
    const sel = document.getElementById("biomass-detail-station");
    const stationId = sel ? sel.value : null;
    if (!stationId) return;

    const station = (state.snapshot.stations || []).find((s) => s.id === stationId);
    if (!station) return;

    const status = document.getElementById("biomass-detail-status");
    const headline = document.getElementById("biomass-headline");
    const metrics = document.getElementById("biomass-detail-metrics");
    const daily = document.getElementById("biomass-daily-table");
    const raw = document.getElementById("biomass-raw-table");
    const caption = document.getElementById("biomass-detail-caption");

    const plantIso = state.biomassPlantDate || state.plantDate;
    const fcstIso = state.forecastDate;
    if (!plantIso || !fcstIso || plantIso >= fcstIso) {
      status.innerHTML = `<span class="error-text">Planting date must be before the forecast date.</span>`;
      headline.innerHTML = "";
      metrics.innerHTML = "";
      daily.innerHTML = "";
      raw.innerHTML = "";
      caption.textContent = "";
      return;
    }

    status.innerHTML = `<span class="pending-text">Pulling weather for ${escapeHtml(stationId)}…</span>`;

    let series = null;
    let source = "proxy";
    try {
      // /proxy/weather legacy single-station shape returns
      // {station, start, tavg_f, precip_in} — exactly what
      // biomassDailyRowsFromSeries() consumes.
      const startD = new Date(plantIso + "T00:00:00Z");
      const endD = new Date(fcstIso + "T00:00:00Z");
      const days = Math.max(1, Math.round((endD - startD) / 86400000) + 1);
      series = await ForecastAPI.fetchWeatherLegacy(stationId, days);
    } catch (err) {
      console.warn("biomass-detail proxy fetch failed:", err);
    }

    // Fall back to the bundled per-station weather series if the proxy
    // call returned nothing (matches the all-station replay path).
    if (!series || !series.tavg_f) {
      series = (state.snapshot.weather || {})[stationId.toUpperCase()] || null;
      source = "bundled";
    }

    if (!series || !series.tavg_f) {
      status.innerHTML = `<span class="error-text">No weather data available for ${escapeHtml(stationId)}.</span>`;
      headline.innerHTML = "";
      metrics.innerHTML = "";
      daily.innerHTML = "";
      raw.innerHTML = "";
      caption.textContent = "";
      return;
    }

    const fallback = state.biomassUseReal ? null : (state.biomassPrecip || 200);
    const rows = Biomass.biomassDailyRowsFromSeries(series, plantIso, fcstIso, fallback);

    if (!rows.length) {
      status.innerHTML = `<span class="error-text">Planting date falls outside the weather window for this station.</span>`;
      headline.innerHTML = "";
      metrics.innerHTML = "";
      daily.innerHTML = "";
      raw.innerHTML = "";
      caption.textContent = "";
      return;
    }

    const last = rows[rows.length - 1];
    const thr = state.snapshot.biomass_thresholds || { low_max: 500, high_min: 1500 };
    const klass = Biomass.classifyBiomass(last.biomass_pred, thr.low_max, thr.high_min);
    const colors = state.snapshot.class_colors || {};
    const color = colors[klass] || "#34495e";
    const plantDoy = Biomass.dayOfYear(new Date(plantIso + "T00:00:00Z"));
    const daysSince = Math.round(
      (new Date(fcstIso + "T00:00:00Z") - new Date(plantIso + "T00:00:00Z")) / 86400000
    );

    headline.innerHTML = `
      <div class="biomass-headline-card" style="border-left-color:${color}">
        <div class="biomass-headline-label">
          Predicted biomass — ${escapeHtml(station.name)} (${escapeHtml(station.id)})
        </div>
        <div class="biomass-headline-value">
          ${Math.round(last.biomass_pred).toLocaleString()}
          <span class="biomass-headline-unit">lb/ac</span>
        </div>
        <div class="biomass-headline-class" style="color:${color}">
          ${escapeHtml(klass)} risk bucket
        </div>
      </div>`;

    const NEUTRAL = "#34495e";
    metrics.innerHTML =
      tile(NEUTRAL, "Plant DOY", plantDoy) +
      tile(NEUTRAL, "Days since planting", daysSince) +
      tile(NEUTRAL, "Cumulative GDD (°C)", Math.round(last.gdd_total).toLocaleString()) +
      tile(NEUTRAL,
        state.biomassUseReal ? "Fall precip (mm)" : "Fall precip — fallback (mm)",
        Number(last.precip_total_mm).toFixed(1)
      );

    daily.innerHTML = renderDailyTable(rows);
    raw.innerHTML = renderRawWeatherTable(series, 50);
    caption.textContent =
      `Last observed: ${last.date}  ·  source: ${source}  ·  loaded ${nowStamp()}`;
    status.innerHTML =
      `<span class="success-text">Computed ${rows.length} day(s) for ${escapeHtml(stationId)}.</span>`;
  }

  function tile(color, label, value) {
    return `
      <div class="metric-tile">
        <div class="metric-label" style="color:${color}">${escapeHtml(label)}</div>
        <div class="metric-value">${escapeHtml(value)}</div>
      </div>`;
  }

  function renderDailyTable(rows) {
    const head = `
      <thead><tr>
        <th>Date</th><th>Tavg (°F)</th><th>Tavg (°C)</th>
        <th>GDD/day</th><th>Cum. GDD</th>
        <th>Precip (in)</th><th>Cum. precip (mm)</th>
        <th>Biomass (lb/ac)</th>
      </tr></thead>`;
    const body = rows.map((r) => `
      <tr>
        <td>${escapeHtml(r.date)}</td>
        <td>${r.tavg_f == null ? "—" : r.tavg_f}</td>
        <td>${r.tavg_c == null ? "—" : r.tavg_c}</td>
        <td>${r.gdd_day}</td>
        <td>${r.gdd_total}</td>
        <td>${r.precip_in == null ? "—" : r.precip_in}</td>
        <td>${r.precip_total_mm}</td>
        <td>${Math.round(r.biomass_pred).toLocaleString()}</td>
      </tr>`).join("");
    return head + `<tbody>${body}</tbody>`;
  }

  function renderRawWeatherTable(series, limit) {
    if (!series || !series.tavg_f) return "";
    const n = Math.min(series.tavg_f.length, limit);
    const startD = new Date(series.start + "T00:00:00Z");
    const head = `
      <thead><tr>
        <th>Date</th><th>tavg_f</th><th>precip_in</th>
      </tr></thead>`;
    const rows = [];
    for (let i = 0; i < n; i++) {
      const d = new Date(startD.getTime() + i * 86400000).toISOString().slice(0, 10);
      const t = series.tavg_f[i];
      const p = series.precip_in ? series.precip_in[i] : null;
      rows.push(`<tr><td>${d}</td><td>${t == null ? "—" : t}</td><td>${p == null ? "—" : p}</td></tr>`);
    }
    return head + `<tbody>${rows.join("")}</tbody>`;
  }

  function nowStamp() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
           `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  async function runBiomass({ silent } = {}) {
    if (!state.currentModel || state.currentModel.type !== "biomass") return;
    const status = document.getElementById("biomass-status");
    const plant = state.biomassPlantDate || state.plantDate;
    const fcst = state.forecastDate;
    if (!plant || !fcst || plant >= fcst) {
      status.innerHTML = `<span class="error-text">Planting date must be before the forecast date.</span>`;
      return;
    }
    if (!silent) {
      status.innerHTML = `<span class="pending-text">Computing biomass for every station…</span>`;
    }
    try {
      const payload = await ForecastAPI.fetchBiomass(fcst, plant, state.biomassPrecip);
      state.biomassResponse = payload;

      // Merge biomass values into the per-station rows.
      const byId = new Map();
      (payload.stations || []).forEach((row) => byId.set(row.station_id, row));
      const latest = latestPerStation(state.forecastRows || []);
      latest.forEach((s) => {
        const b = byId.get(s.id);
        if (b) {
          s.biomass = b.biomass_pred;
          s.biomass_class = b.biomass_class || "Unknown";
          s.biomass_gdd_total = b.gdd_total;
          s.biomass_precip_total_mm = b.precip_total_mm;
        }
      });

      const ok = payload.stations.filter((r) => r.biomass_pred != null).length;
      status.innerHTML =
        `<span class="success-text">Biomass computed for ${ok}/${payload.stations.length} ` +
        `stations (planting ${escapeHtml(plant)} → forecast ${escapeHtml(fcst)}).</span>`;

      rerender();
      renderBiomassDetail();
    } catch (err) {
      console.warn("Live biomass failed, falling back to client replay:", err);
      replayBiomassClient();
      status.innerHTML =
        `<span class="error-text">Live biomass call failed (${escapeHtml(err.message || err)}). ` +
        `Showing bundled-weather replay instead.</span>`;
      rerender();
    }
  }

  function replayBiomassClient() {
    const thr = state.snapshot.biomass_thresholds || { low_max: 500, high_min: 1500 };
    const fallback = state.biomassPrecip || state.snapshot.fall_precip_default_mm || 200;
    const weather = state.snapshot.weather || {};
    const plantIso = state.biomassPlantDate || state.plantDate;
    const fcstIso = state.forecastDate;
    const plantOk = plantIso && fcstIso && plantIso < fcstIso;

    const latest = latestPerStation(state.forecastRows || []);
    latest.forEach((s) => {
      // Bundle keys by station_id (uppercase), but tolerate older
      // builds that used the lowercase station name.
      const sid = s.id ? String(s.id).toUpperCase() : null;
      const legacy = s.name ? String(s.name).toLowerCase() : null;
      const series = (sid && weather[sid]) || (legacy && weather[legacy]) || null;
      const result = plantOk
        ? Biomass.biomassFromWeatherSeries(series, plantIso, fcstIso, fallback)
        : null;
      if (result && Number.isFinite(result.biomass)) {
        s.biomass = result.biomass;
        s.biomass_gdd_total = result.gddTotal;
        s.biomass_precip_total_mm = result.precipTotalMm;
        s.biomass_class = Biomass.classifyBiomass(result.biomass, thr.low_max, thr.high_min);
      } else {
        s.biomass = null;
        s.biomass_class = "Unknown";
      }
    });
  }

  /* ==================================================================
     Risk Trends tab
     ================================================================== */

  function renderTrendsTab() {
    if (!state.forecastRows || !state.currentModel) return;
    if (state.currentModel.type === "biomass") {
      const empty = document.getElementById("trends-empty");
      empty.style.display = "block";
      empty.textContent = "Risk Trends is only available for disease models.";
      document.getElementById("trends-table").innerHTML = "";
      WITrends.destroy();
      return;
    }
    const selected = getSelectedTrendsStations();
    WITrends.render(
      state.forecastRows,
      state.currentModel,
      selected,
      state.snapshot.class_colors || {}
    );
    const meta = document.getElementById("trends-meta");
    const dates = new Set();
    state.forecastRows.forEach((r) => { if (r.date) dates.add(r.date); });
    meta.textContent = dates.size
      ? `${selected.length} stations · ${dates.size} forecasting day(s) in payload.`
      : "No multi-day rows in the current payload.";
  }

  function getSelectedTrendsStations() {
    const sel = document.getElementById("trends-stations");
    return Array.from(sel.selectedOptions).map((o) => o.value);
  }

  /* ==================================================================
     Weather Data tab
     ================================================================== */

  function getSelectedWeatherStations() {
    const sel = document.getElementById("weather-stations");
    return Array.from(sel.selectedOptions).map((o) => o.value);
  }

  async function runWeather() {
    const status = document.getElementById("weather-status");
    const stations = getSelectedWeatherStations();
    const field = document.getElementById("weather-field").value;
    const start = document.getElementById("weather-start").value;
    const end = document.getElementById("weather-end").value;
    if (!stations.length) {
      status.innerHTML = `<span class="error-text">Pick at least one station.</span>`;
      return;
    }
    if (!field) {
      status.innerHTML = `<span class="error-text">Pick a weather field.</span>`;
      return;
    }
    if (!start || !end || start > end) {
      status.innerHTML = `<span class="error-text">Pick a valid date range.</span>`;
      return;
    }
    status.innerHTML = `<span class="pending-text">Fetching ${escapeHtml(stations.length.toString())} station(s)…</span>`;
    try {
      const payload = await ForecastAPI.fetchWeather(stations, [field], start, end);
      const names = stationIdToDisplay();
      WIWeather.renderMultiWeather(payload, field, names);
      status.innerHTML = `<span class="success-text">Loaded weather for ${stations.length} station(s).</span>`;
    } catch (err) {
      console.error(err);
      status.innerHTML = `<span class="error-text">${escapeHtml(err.message || err)}</span>`;
    }
  }

  /* ==================================================================
     Inline Disease-tab weather chart
     (single station — the chart-canvas#weather-chart in the Disease tab
     was removed from HTML; this is a noop kept for back-compat.)
     ================================================================== */

  function renderInlineWeatherSection() {
    // The Disease tab no longer has the inline single-station weather
    // chart; that view moved to the dedicated Weather Data tab.
  }

  /* ==================================================================
     UI population
     ================================================================== */

  function populateModelSelect() {
    const sel = document.getElementById("model-select");
    sel.innerHTML = "";

    // Build display options, collapsing the three WM variants into one.
    const visibleEntries = [];
    let wmInserted = false;
    state.snapshot.models.forEach((m) => {
      const isWm = m.label && m.label.toLowerCase().startsWith("white mold");
      if (isWm) {
        if (!wmInserted) {
          visibleEntries.push({ label: WM_GROUP_LABEL, kind: "wm" });
          wmInserted = true;
        }
      } else {
        visibleEntries.push({ label: m.label, kind: "model", model: m });
      }
    });

    visibleEntries.forEach((entry) => {
      const opt = document.createElement("option");
      opt.value = entry.label;
      opt.textContent = entry.label;
      sel.appendChild(opt);
    });

    sel.addEventListener("change", () => {
      const label = sel.value;
      track("disease_selected", { model: label });
      if (label === WM_GROUP_LABEL) {
        // Default to the Non-irrigated variant the first time WM is picked.
        const variantKey = currentWmRadio() || "non";
        setWmRadio(variantKey);
        state.currentModel = findWmModel(variantKey);
        document.getElementById("wm-irrigation-wrap").hidden = false;
      } else {
        document.getElementById("wm-irrigation-wrap").hidden = true;
        state.currentModel = state.snapshot.models.find((m) => m.label === label);
      }
      showBiomassControls(state.currentModel && state.currentModel.type === "biomass");
      rerender();
      renderTrendsTab();
    });

    // Wire the WM sub-radio.
    document.getElementById("wm-irrigation").addEventListener("change", () => {
      const key = currentWmRadio();
      if (!key) return;
      state.currentModel = findWmModel(key);
      rerender();
      renderTrendsTab();
    });
  }

  function findWmModel(variantKey) {
    const label = WM_LABELS[variantKey];
    return state.snapshot.models.find((m) => m.label === label) || state.snapshot.models[0];
  }

  function currentWmRadio() {
    const checked = document.querySelector('input[name="wm-irr"]:checked');
    return checked ? checked.value : null;
  }

  function setWmRadio(key) {
    const radios = document.querySelectorAll('input[name="wm-irr"]');
    radios.forEach((r) => { r.checked = (r.value === key); });
  }

  function stationIdToDisplay() {
    const map = {};
    state.snapshot.stations.forEach((s) => {
      map[s.id] = `${s.name} (${s.id})`;
      // Also handle lowercase + uppercase forms in case the backend
      // returns them differently than the picker emitted.
      map[s.id.toUpperCase()] = `${s.name} (${s.id})`;
      map[s.id.toLowerCase()] = `${s.name} (${s.id})`;
    });
    return map;
  }

  function populateMultiStationPickers() {
    const trends = document.getElementById("trends-stations");
    const weather = document.getElementById("weather-stations");
    trends.innerHTML = "";
    weather.innerHTML = "";

    const sorted = state.snapshot.stations.slice().sort((a, b) => a.name.localeCompare(b.name));
    sorted.forEach((s, i) => {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = `${s.name} (${s.id})`;
      if (i < 5) opt.selected = true;
      trends.appendChild(opt);

      // Weather tab also keys by station_id (matches what wiscopy returns
      // and what /proxy/weather expects).
      const opt2 = document.createElement("option");
      opt2.value = s.id;
      opt2.textContent = `${s.name} (${s.id})`;
      if (i < 2) opt2.selected = true;
      weather.appendChild(opt2);
    });

    trends.addEventListener("change", () => renderTrendsTab());
  }

  function populateWeatherFieldSelect() {
    const sel = document.getElementById("weather-field");
    sel.innerHTML = "";
    // value = wiscopy field name (what we send to /proxy/weather);
    // textContent = human-readable label (what the user sees).
    Object.entries(WEATHER_FIELDS).forEach(([code, label]) => {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = label;
      sel.appendChild(opt);
    });
  }

  function initWeatherDateRange() {
    const today = todayIso();
    const start = isoDaysAgo(30);
    document.getElementById("weather-start").value = start;
    document.getElementById("weather-end").value = today;
    document.getElementById("weather-start").max = today;
    document.getElementById("weather-end").max = today;
  }

  function setMeta() {
    document.getElementById("forecast-date").textContent =
      "Forecast for " + state.forecastDate;
    document.getElementById("generated-at").textContent =
      "Snapshot built " + new Date(state.snapshot.generated_at).toLocaleString();
  }

  function setDataSource(source) {
    const el = document.getElementById("data-source");
    const labels = {
      bundled:        "Source: bundled snapshot",
      proxy:          "Source: live API (proxy)",
      direct:         "Source: live API (direct)",
      "proxy-cache":  "Source: cache (proxy)",
      "direct-cache": "Source: cache (direct)",
    };
    if (!source) { el.textContent = ""; el.dataset.source = ""; return; }
    el.textContent = labels[source] || source;
    el.dataset.source = source;
  }

  /* ==================================================================
     Controls / tabs
     ================================================================== */

  function bindControls() {
    const today = todayIso();
    const earliest = "2023-01-01";
    const fcst = document.getElementById("forecast-date-input");
    fcst.value = state.forecastDate;
    fcst.min = earliest;
    fcst.max = today;
    fcst.addEventListener("change", (e) => {
      if (e.target.value) {
        state.forecastDate = e.target.value;
        // Refresh planting-date bounds.
        const plant = document.getElementById("planting-date-input");
        if (plant) plant.max = state.forecastDate;
      }
    });

    const slider = document.getElementById("risk-days-input");
    const sliderLabel = document.getElementById("risk-days-value");
    slider.value = state.riskDays;
    sliderLabel.textContent = state.riskDays;
    slider.addEventListener("input", (e) => {
      state.riskDays = Math.max(1, Math.min(7, Number(e.target.value) || 1));
      sliderLabel.textContent = state.riskDays;
    });

    document.getElementById("run-btn").addEventListener("click", () => {
      track("forecast_run", { risk_days: state.riskDays });
      refreshLive(`Fetching ${state.forecastDate} (risk_days=${state.riskDays})…`);
    });

    // Biomass sub-controls
    const plant = document.getElementById("planting-date-input");
    plant.value = state.plantDate;
    plant.min = "2023-01-01";
    plant.max = state.forecastDate;
    plant.addEventListener("change", (e) => {
      if (e.target.value) {
        state.biomassPlantDate = e.target.value;
        if (state.currentModel && state.currentModel.type === "biomass") renderBiomassDetail();
      }
    });

    const precip = document.getElementById("fall-precip-input");
    precip.value = state.biomassPrecip;
    precip.addEventListener("change", (e) => {
      const v = Number(e.target.value);
      if (Number.isFinite(v) && v >= 0) {
        state.biomassPrecip = v;
        if (state.currentModel && state.currentModel.type === "biomass") renderBiomassDetail();
      }
    });

    document.getElementById("use-real-precip").addEventListener("change", (e) => {
      state.biomassUseReal = !!e.target.checked;
      if (state.currentModel && state.currentModel.type === "biomass") renderBiomassDetail();
    });

    document.getElementById("biomass-run-btn").addEventListener("click", () => {
      track("biomass_run");
      runBiomass({});
    });
    document.getElementById("weather-run-btn").addEventListener("click", () => {
      track("weather_run");
      runWeather();
    });
  }

  function bindTabs() {
    const buttons = document.querySelectorAll(".tab-btn");
    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const tab = btn.dataset.tab;
        if (!tab || tab === state.activeTab) return;
        track("tab_view", { tab });
        buttons.forEach((b) => {
          const active = b.dataset.tab === tab;
          b.classList.toggle("active", active);
          b.setAttribute("aria-selected", active ? "true" : "false");
        });
        document.querySelectorAll(".tab-panel").forEach((p) => {
          p.hidden = p.dataset.tabPanel !== tab;
        });
        state.activeTab = tab;
        if (tab === "trends") renderTrendsTab();
        // Resize the Leaflet map after the panel re-shows.
        if (tab === "forecast" && state.map) {
          setTimeout(() => state.map.invalidateSize(), 50);
        }
      });
    });
  }

  /* ==================================================================
     Date helpers
     ================================================================== */

  function todayIso() {
    return new Date().toISOString().slice(0, 10);
  }
  function isoDaysAgo(n) {
    const d = new Date();
    d.setUTCDate(d.getUTCDate() - n);
    return d.toISOString().slice(0, 10);
  }
  function defaultPlantDate(forecastIso) {
    // 9/15 of current year unless that's after the forecast date.
    const f = new Date(forecastIso + "T00:00:00Z");
    let y = f.getUTCFullYear();
    let cand = `${y}-09-15`;
    if (cand >= forecastIso) cand = `${y - 1}-09-15`;
    return cand;
  }
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /* -------------------- boot trigger -------------------- */

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
