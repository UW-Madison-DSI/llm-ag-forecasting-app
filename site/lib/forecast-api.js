/* Live client for the UW–Madison Ag Forecasting API.

   Wraps the backend /proxy/* endpoints (FastAPI in production, same
   shape as the upstream API). Direct upstream calls are kept as a Node
   fallback only — they almost never work in a browser due to CORS. */

(function (root) {
  "use strict";

  // /proxy/* routes are wired by nginx (Docker) or Netlify redirects.
  const PROXY_FORECAST   = "/proxy/forecast";
  const PROXY_MODEL_INFO = "/proxy/model_info";
  const PROXY_BIOMASS    = "/proxy/biomass";
  const PROXY_WEATHER    = "/proxy/weather";
  const PROXY_HEALTH     = "/proxy/health";

  const DIRECT_FORECAST_URL =
    "https://connect.doit.wisc.edu/ag_forecasting_api/v2/ag_models_wrappers/wisconet_g";

  // In-memory cache: key → { payload, source, expires }.
  // 1-hour TTL matches the proxy's Cache-Control max-age.
  const CACHE_TTL_MS = 60 * 60 * 1000;
  const cache = new Map();

  function cacheKey(prefix, parts) {
    return prefix + ":" + parts.join("|");
  }
  function getCached(key) {
    const hit = cache.get(key);
    if (hit && hit.expires > Date.now()) return hit;
    if (hit) cache.delete(key);
    return null;
  }
  function putCached(key, payload, source) {
    cache.set(key, { payload, source, expires: Date.now() + CACHE_TTL_MS });
    if (cache.size > 80) {
      const firstKey = cache.keys().next().value;
      cache.delete(firstKey);
    }
  }

  /* -------------------- Forecast -------------------- */

  async function fetchForecast(dateIso, riskDays) {
    const rd = Number.isInteger(riskDays) && riskDays >= 1 && riskDays <= 7
      ? riskDays : 1;
    const key = cacheKey("forecast", [dateIso, rd]);
    const cached = getCached(key);
    if (cached) {
      root.ForecastAPI.lastSource = cached.source + "-cache";
      return cached.payload;
    }

    root.ForecastAPI.lastSource = null;
    // 1. Try the same-origin proxy first.
    try {
      const url =
        `${PROXY_FORECAST}?forecasting_date=${encodeURIComponent(dateIso)}` +
        `&risk_days=${rd}`;
      const resp = await fetch(url, { headers: { Accept: "application/json" } });
      if (resp.ok) {
        const payload = await resp.json();
        putCached(key, payload, "proxy");
        root.ForecastAPI.lastSource = "proxy";
        return payload;
      }
      if (resp.status !== 404) throw new Error(`Proxy ${resp.status}`);
    } catch (err) {
      console.warn("Proxy unavailable, trying direct API:", err.message || err);
    }

    // 2. Direct upstream — Node tests, occasionally CORS-permitted browsers.
    const directUrl =
      `${DIRECT_FORECAST_URL}?forecasting_date=${encodeURIComponent(dateIso)}` +
      `&risk_days=${rd}`;
    const resp = await fetch(directUrl, { headers: { Accept: "application/json" } });
    if (!resp.ok) throw new Error(`Forecast API ${resp.status}`);
    const payload = await resp.json();
    putCached(key, payload, "direct");
    root.ForecastAPI.lastSource = "direct";
    return payload;
  }

  /* -------------------- Model info -------------------- */

  async function fetchModelInfo(modelName) {
    if (!modelName) return null;
    const key = cacheKey("model_info", [modelName]);
    const cached = getCached(key);
    if (cached) return cached.payload;
    try {
      const url = `${PROXY_MODEL_INFO}?model_name=${encodeURIComponent(modelName)}`;
      const resp = await fetch(url, { headers: { Accept: "application/json" } });
      if (!resp.ok) return null;
      const payload = await resp.json();
      putCached(key, payload, "proxy");
      return payload;
    } catch (err) {
      console.warn("Model info fetch failed:", err);
      return null;
    }
  }

  /* -------------------- Biomass -------------------- */

  async function fetchBiomass(forecastingDate, plantDate, fallPrecipMm) {
    const fp = Number.isFinite(fallPrecipMm) ? fallPrecipMm : 200.0;
    const key = cacheKey("biomass", [forecastingDate, plantDate, fp]);
    const cached = getCached(key);
    if (cached) return cached.payload;
    const url =
      `${PROXY_BIOMASS}?forecasting_date=${encodeURIComponent(forecastingDate)}` +
      `&plant_date=${encodeURIComponent(plantDate)}` +
      `&fall_precip_mm=${fp}`;
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Biomass ${resp.status}: ${text.slice(0, 180)}`);
    }
    const payload = await resp.json();
    putCached(key, payload, "proxy");
    return payload;
  }

  /* -------------------- Weather (multi-station, multi-field) -------------------- */

  /**
   * @param {string[]} stations  Lowercase wiscopy ids.
   * @param {string[]} fields    Wisconet field names.
   * @param {string} startIso    YYYY-MM-DD.
   * @param {string} endIso      YYYY-MM-DD.
   */
  async function fetchWeather(stations, fields, startIso, endIso) {
    if (!stations || !stations.length) throw new Error("No stations selected.");
    if (!fields || !fields.length)     throw new Error("No fields selected.");
    const stationsParam = stations.map((s) => s.toLowerCase()).join(",");
    const fieldsParam = fields.join(",");
    const key = cacheKey("weather", [stationsParam, fieldsParam, startIso, endIso]);
    const cached = getCached(key);
    if (cached) return cached.payload;

    const url =
      `${PROXY_WEATHER}?stations=${encodeURIComponent(stationsParam)}` +
      `&fields=${encodeURIComponent(fieldsParam)}` +
      `&start_date=${encodeURIComponent(startIso)}` +
      `&end_date=${encodeURIComponent(endIso)}`;
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Weather ${resp.status}: ${text.slice(0, 180)}`);
    }
    const payload = await resp.json();
    putCached(key, payload, "proxy");
    return payload;
  }

  /* -------------------- Single-station weather (legacy shape) -------------------- */

  async function fetchWeatherLegacy(stationKey, days) {
    const d = Number.isFinite(days) ? days : 240;
    const key = cacheKey("weather-legacy", [stationKey, d]);
    const cached = getCached(key);
    if (cached) return cached.payload;
    const url = `${PROXY_WEATHER}?station=${encodeURIComponent(stationKey)}&days=${d}`;
    try {
      const resp = await fetch(url, { headers: { Accept: "application/json" } });
      if (!resp.ok) return null;
      const payload = await resp.json();
      putCached(key, payload, "proxy");
      return payload;
    } catch (err) {
      console.warn("Weather proxy failed:", err);
      return null;
    }
  }

  /* -------------------- Health -------------------- */

  async function fetchHealth() {
    try {
      const resp = await fetch(PROXY_HEALTH, { headers: { Accept: "application/json" } });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (err) {
      return null;
    }
  }

  /* -------------------- Helpers -------------------- */

  function clearCache() { cache.clear(); }

  /**
   * Flatten the FeatureCollection into rows. When riskDays>1 each station
   * has multiple timeseries entries — we keep them all (one row per
   * (station, date)), unlike the original single-day flattener.
   */
  function flattenForecast(payload, normalizeClass) {
    const rows = [];
    for (const feature of payload.features || []) {
      const station = feature.station || {};
      const coords = station.coordinates || {};
      const base = {
        id: String(station.station_id),
        name: String(station.station_name),
        lat: coords.latitude,
        lon: coords.longitude,
        city: station.city,
        county: station.county,
        region: station.region,
      };
      for (const ts of feature.timeseries || []) {
        const row = Object.assign({}, base, {
          date: ts.date || ts.forecasting_date || null,
          forecasting_date: ts.forecasting_date || ts.date || null,
        });
        for (const item of ts.data || []) {
          const key = item.fieldname;
          const val = item.value;
          if (key && key.endsWith("_class")) {
            row[key] = normalizeClass(val);
          } else {
            row[key] = val;
          }
        }
        rows.push(row);
      }
    }
    return rows;
  }

  /** Latest timeseries entry per station — the Disease tab's view. */
  function latestRowPerStation(rows) {
    const byStation = new Map();
    for (const r of rows) {
      const prev = byStation.get(r.id);
      if (!prev || (r.date || "") > (prev.date || "")) byStation.set(r.id, r);
    }
    return Array.from(byStation.values());
  }

  function normalizeClass(value) {
    if (value == null) return "Unknown";
    // The upstream API often prefixes class names with a sort key —
    // "1.Low", "2.Moderate", "3.High". Strip it so the value matches
    // CLASS_COLORS / CLASS_ORDER keys.
    let t = String(value).trim().replace(/^\s*\d+\s*[.:)\-]\s*/, "");
    if (!t) return "Unknown";
    return t.replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase());
  }

  root.ForecastAPI = {
    fetchForecast,
    fetchModelInfo,
    fetchBiomass,
    fetchWeather,
    fetchWeatherLegacy,
    fetchHealth,
    flattenForecast,
    latestRowPerStation,
    normalizeClass,
    clearCache,
    lastSource: null,
  };
})(window);
