/* Main controller for the static dashboard.

   Loads data/latest.json (built daily by build_site.py), wires the
   model + date pickers, and re-renders the metric tiles + map + table
   + weather chart on user input.

   Date picker behavior:
     - Disease models  → live-fetched from the forecast API.
     - Biomass         → recomputed client-side from the bundled weather
                         series (no backend needed).
*/

(function () {
  "use strict";

  const DATA_URL = "data/latest.json";

  // Mutable runtime state. `snapshot` is the parsed JSON from disk;
  // `liveStations` is the latest stations array (either from the snapshot
  // or freshly fetched from the API).
  const state = {
    snapshot: null,
    liveStations: null,     // disease values for the currently-selected date
    forecastDate: null,
    plantDate: null,
    currentModel: null,
    map: null,
  };

  /* -------------------- station decoration -------------------- */

  function attachModelView(station, model) {
    const out = Object.assign({}, station);
    if (model.type === "biomass") {
      out._value = station[model.value_field] ?? null;
      out._class = station[model.class_field] || "Unknown";
    } else {
      const raw = station[model.risk_field];
      out._value = raw === -1 || raw == null ? null : raw;
      out._class = station[model.class_field] || "Unknown";
    }
    return out;
  }

  /* -------------------- rerender -------------------- */

  function rerender() {
    const stations = state.liveStations.map((s) => attachModelView(s, state.currentModel));
    WIMap.renderStations(state.map, stations, state.currentModel, state.snapshot.class_colors);
    WIRender.renderMetrics(stations, state.currentModel, state.snapshot.class_colors);
    WIRender.renderModelInfo(state.currentModel, state.snapshot.model_info);
    WIRender.renderTable(stations, state.currentModel, state.snapshot.class_colors);
  }

  /* -------------------- biomass recompute (client-side) -------------------- */

  function recomputeBiomass(stations) {
    const thr = state.snapshot.biomass_thresholds || { low_max: 500, high_min: 1500 };
    const fallback = state.snapshot.fall_precip_default_mm || 200;
    const weather = state.snapshot.weather || {};
    const plantIso = state.plantDate;
    const fcstIso = state.forecastDate;

    // Guard: planting must precede the forecast date for the model
    // to have any data to integrate over.
    const plantOk = plantIso && fcstIso && plantIso < fcstIso;

    stations.forEach((s) => {
      const key = s.name ? String(s.name).toLowerCase() : null;
      const series = key ? weather[key] : null;
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

  /* -------------------- date picker handler -------------------- */

  function updatePlantingMax() {
    // Keep planting <= forecast at all times, otherwise the biomass
    // window has no days to integrate over.
    const plant = document.getElementById("planting-date-input");
    if (!plant) return;
    plant.max = state.forecastDate;
    if (plant.value && plant.value >= state.forecastDate) {
      // Auto-clamp to one day before the new forecast date.
      const d = new Date(state.forecastDate + "T00:00:00Z");
      d.setUTCDate(d.getUTCDate() - 1);
      const clamped = d.toISOString().slice(0, 10);
      plant.value = clamped;
      state.plantDate = clamped;
    }
  }

  // ------- date selection flow -------
  //
  // Picking a date *only* loads automatically when it's in the bundled
  // snapshot (instant, no network). For any other date, the user has
  // to click the Run button so the network call is explicit.

  function setDataSource(source) {
    // source ∈ "" | "bundled" | "proxy" | "direct" | "proxy-cache" | "direct-cache"
    const el = document.getElementById("data-source");
    if (!source) {
      el.textContent = "";
      el.dataset.source = "";
      return;
    }
    const labels = {
      bundled:        "Source: bundled snapshot",
      proxy:          "Source: live API (proxy)",
      direct:         "Source: live API (direct)",
      "proxy-cache":  "Source: cache (proxy)",
      "direct-cache": "Source: cache (direct)",
    };
    el.textContent = labels[source] || source;
    el.dataset.source = source;
  }

  function markPending(iso) {
    state.pendingDate = iso;
    const status = document.getElementById("date-status");
    const inBundle = !!(state.snapshot.forecasts || {})[iso];
    if (iso === state.forecastDate) {
      status.textContent = `Showing ${iso}.`;
      status.className = "muted";
    } else if (inBundle) {
      // Bundled hits auto-load. (Handled by onForecastDatePick.)
      status.textContent = `Loading bundled ${iso}…`;
      status.className = "muted";
    } else {
      status.innerHTML =
        `<span class="pending-text">Selected ${escapeHtml(iso)} — ` +
        `click <strong>▶ Run</strong> to fetch from the API.</span>`;
    }
  }

  async function onForecastDatePick(newIso) {
    if (!newIso || !/^\d{4}-\d{2}-\d{2}$/.test(newIso)) return;
    state.pendingDate = newIso;
    const inBundle = !!(state.snapshot.forecasts || {})[newIso];
    if (inBundle) {
      // No network — load it immediately.
      await loadForecast(newIso);
    } else {
      markPending(newIso);
    }
  }

  async function onRunClick() {
    const iso = state.pendingDate || state.forecastDate;
    if (!iso) return;
    await loadForecast(iso);
  }

  // ------- the actual load -------

  async function loadForecast(newIso) {
    const status   = document.getElementById("date-status");
    const fcstInp  = document.getElementById("forecast-date-input");
    const plantInp = document.getElementById("planting-date-input");

    // Capture current state so we can roll back atomically on failure.
    const snap = {
      forecastDate: state.forecastDate,
      plantDate:    state.plantDate,
      fcstValue:    fcstInp.value,
      plantValue:   plantInp.value,
      plantMax:     plantInp.max,
    };
    function rollback() {
      state.forecastDate  = snap.forecastDate;
      state.plantDate     = snap.plantDate;
      fcstInp.value       = snap.fcstValue;
      plantInp.value      = snap.plantValue;
      plantInp.max        = snap.plantMax;
      setMeta();
    }

    try {
      const bundle = (state.snapshot.forecasts || {})[newIso];

      if (bundle) {
        state.forecastDate = newIso;
        updatePlantingMax();
        setMeta();
        const stations = bundle.map((s) => Object.assign({}, s));
        recomputeBiomass(stations);
        state.liveStations = stations;
        status.textContent = `Showing bundled forecast for ${newIso}.`;
        status.className = "muted";
        setDataSource("bundled");
        rerender();
        renderWeatherSection();
        return;
      }

      status.innerHTML =
        `<span class="pending-text">Fetching ${escapeHtml(newIso)} from API…</span>`;
      setDataSource("");

      const before = Date.now();
      let payload;
      try {
        payload = await ForecastAPI.fetchForecast(newIso, 1);
      } catch (err) {
        rollback();
        const available = (state.snapshot.available_dates || []).join(", ");
        status.innerHTML =
          `<span class="error-text">Could not load ${escapeHtml(newIso)} — ` +
          `${escapeHtml(err.message || String(err))}.</span><br>` +
          `<span class="muted">Past dates need the CORS proxy. ` +
          `Run <code>netlify dev</code> or use the Docker deploy. ` +
          `Bundled dates available offline: ${escapeHtml(available)}.</span>`;
        setDataSource("");
        return;
      }
      const elapsed = ((Date.now() - before) / 1000).toFixed(2);

      // ForecastAPI.lastSource is "proxy" | "direct" | "proxy-cache"
      // | "direct-cache". Pass it through unchanged so the badge can
      // distinguish a fresh API call from a cached one.
      const source = ForecastAPI.lastSource || "proxy";

      state.forecastDate = newIso;
      updatePlantingMax();
      setMeta();

      const rows = ForecastAPI.flattenForecast(payload, ForecastAPI.normalizeClass);
      const live = state.snapshot.stations.map((bundled) => {
        const r = rows.find((x) => x.id === bundled.id);
        if (!r) return Object.assign({}, bundled);
        const merged = Object.assign({}, bundled);
        Object.entries(r).forEach(([k, v]) => {
          if (!["id", "name", "lat", "lon", "city", "county", "region"].includes(k)) {
            merged[k] = v;
          }
        });
        return merged;
      });
      recomputeBiomass(live);
      state.liveStations = live;
      status.innerHTML =
        `<span class="success-text">Loaded ${escapeHtml(newIso)} in ${elapsed}s.</span>`;
      setDataSource(source);
      rerender();
      renderWeatherSection();
    } catch (err) {
      console.error("loadForecast failed:", err);
      rollback();
      status.innerHTML =
        `<span class="error-text">Unexpected error updating forecast: ` +
        `${escapeHtml(err.message || String(err))}.</span>`;
      setDataSource("");
    }
  }

  function onPlantingDateChange(newIso) {
    if (!newIso || !/^\d{4}-\d{2}-\d{2}$/.test(newIso)) return;
    const status = document.getElementById("date-status");
    try {
      if (newIso >= state.forecastDate) {
        status.innerHTML =
          `<span class="error-text">Planting date must be before the forecast date ` +
          `(${escapeHtml(state.forecastDate)}).</span>`;
        return;
      }
      state.plantDate = newIso;
      const live = state.liveStations.map((s) => Object.assign({}, s));
      recomputeBiomass(live);
      state.liveStations = live;
      if (state.currentModel.type === "biomass") rerender();
      status.textContent = `Biomass recomputed from planting ${newIso}.`;
      status.className = "muted";
    } catch (err) {
      console.error("onPlantingDateChange failed:", err);
      status.innerHTML =
        `<span class="error-text">Unexpected error updating planting date: ` +
        `${escapeHtml(err.message || String(err))}.</span>`;
    }
  }

  /* -------------------- weather section -------------------- */

  function populateWeatherStationSelect() {
    const sel = document.getElementById("weather-station");
    sel.innerHTML = "";
    state.snapshot.stations
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach((s) => {
        const opt = document.createElement("option");
        opt.value = s.name.toLowerCase();
        opt.textContent = `${s.name} (${s.id})`;
        sel.appendChild(opt);
      });
    sel.addEventListener("change", renderWeatherSection);
  }

  function renderWeatherSection() {
    const sel = document.getElementById("weather-station");
    const key = sel.value;
    if (!key) return;
    const station = state.snapshot.stations.find(
      (s) => s.name.toLowerCase() === key
    );
    const label = station ? `${station.name} (${station.id})` : key;
    const series = (state.snapshot.weather || {})[key];
    // Pass `key` so weather.js can fall back to /proxy/weather when
    // the bundle is empty or missing this station.
    WIWeather.renderWeatherChart("weather-chart", series, label, key);
  }

  /* -------------------- UI scaffolding -------------------- */

  function populateModelSelect() {
    const sel = document.getElementById("model-select");
    sel.innerHTML = "";
    state.snapshot.models.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.label;
      opt.textContent = m.label;
      sel.appendChild(opt);
    });
    sel.addEventListener("change", (e) => {
      state.currentModel = state.snapshot.models.find((m) => m.label === e.target.value);
      rerender();
    });
  }

  function setMeta() {
    const dateEl = document.getElementById("forecast-date");
    dateEl.textContent = "Forecast for " + state.forecastDate;
    const upd = document.getElementById("generated-at");
    upd.textContent = "Built " + new Date(state.snapshot.generated_at).toLocaleString();
  }

  function bindDatePickers() {
    const fcst = document.getElementById("forecast-date-input");
    const plant = document.getElementById("planting-date-input");
    const status = document.getElementById("date-status");

    // Forecast picker is open to the full upstream-API window. The
    // proxy hands every request through to connect.doit.wisc.edu, so
    // any date the API serves works here. Bundled dates (last N days)
    // load instantly from latest.json; anything else needs ▶ Run.
    const earliestSupported = "2023-01-01";
    const today = new Date().toISOString().slice(0, 10);
    fcst.value = state.forecastDate;
    fcst.min = earliestSupported;
    fcst.max = today;

    // Planting must be at or after the earliest supported date (so we
    // can integrate weather from then on) and strictly before the
    // forecast date.
    plant.value = state.plantDate;
    plant.min = earliestSupported;
    plant.max = state.forecastDate;

    const dates = state.snapshot.available_dates || [];
    const datesNote = dates.length
      ? `Bundled forecasts: ${dates[dates.length - 1]} → ${dates[0]}`
      : `Bundled forecast: ${state.forecastDate}`;
    status.textContent = datesNote;
    status.className = "muted";

    fcst.addEventListener("change", (e) => {
      if (e.target.value) onForecastDatePick(e.target.value);
    });
    plant.addEventListener("change", (e) => {
      if (e.target.value) onPlantingDateChange(e.target.value);
    });

    const runBtn = document.getElementById("run-btn");
    if (runBtn) runBtn.addEventListener("click", () => onRunClick());
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /* -------------------- boot -------------------- */

  async function boot() {
    try {
      const resp = await fetch(DATA_URL, { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      state.snapshot = await resp.json();
    } catch (err) {
      document.getElementById("metrics").innerHTML =
        `<div class="metric-tile" style="grid-column:1/-1;color:#9B0000">
           <div class="metric-label" style="color:#9B0000">Could not load data</div>
           <div class="metric-value">${escapeHtml(err.message || err)}</div>
           <p class="muted">
             Run <code>python build_site.py</code> from the project root.
           </p>
         </div>`;
      return;
    }

    state.forecastDate = state.snapshot.forecasting_date;
    state.plantDate = state.snapshot.plant_date;
    state.currentModel = state.snapshot.models[0];
    state.liveStations = state.snapshot.stations.map((s) => Object.assign({}, s));

    setMeta();
    populateModelSelect();
    populateWeatherStationSelect();
    bindDatePickers();

    state.map = WIMap.initMap("map");
    setDataSource("bundled");   // initial view comes from latest.json
    rerender();
    renderWeatherSection();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
