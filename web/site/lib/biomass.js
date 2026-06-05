/* Cereal rye biomass NLS model — direct port of
   features/crereal_rye_biomass.py:predict_rye_biomass.

   Currently unused at runtime (the daily build pre-computes biomass
   server-side), but kept here so future iterations can let users
   change planting date / fall precip in the browser without a rebuild.
*/

(function (root) {
  "use strict";

  // Coefficients from the original NLS fit (R implementation).
  const COEF = {
    b0:   4.231e+02,   // intercept
    b_pd: -1.031e+00,  // slope on planting DOY
    b_pf: -2.878e-01,  // slope on fall precipitation (mm)
    k:     3.663e-03,  // logistic steepness
    x0:    1.049e+03,  // logistic midpoint (GDD)
  };

  function predictRyeBiomass(plantDoy, precipFallMm, gddTotal) {
    const num = COEF.b0 + COEF.b_pd * plantDoy + COEF.b_pf * precipFallMm;
    const den = 1 + Math.exp(-COEF.k * (gddTotal - COEF.x0));
    const raw = num / den;
    return raw * raw;
  }

  function fahrenheitToCelsius(f) { return (f - 32) * 5 / 9; }
  function inchesToMm(inches)     { return inches * 25.4; }

  // Simple-average GDD (base °C) for one day, vectorized over arrays.
  function dailyGddCelsius(tavgC, base) {
    base = base || 0;
    return Math.max(0, tavgC - base);
  }

  // Classify lb/ac into Low / Moderate / High using the same thresholds
  // as features/config.py:BIOMASS_THRESHOLDS.
  function classifyBiomass(value, lowMax, highMin) {
    if (value == null || Number.isNaN(value)) return "Unknown";
    if (value < lowMax) return "Low";
    if (value < highMin) return "Moderate";
    return "High";
  }

  function dayOfYear(date) {
    const start = Date.UTC(date.getUTCFullYear(), 0, 0);
    const here = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
    return Math.floor((here - start) / 86400000);
  }

  /**
   * Replay the biomass model for one station using a bundled daily
   * weather series. Returns {biomass, gddTotal, precipTotalMm} or null
   * if the planting/forecast window can't be evaluated.
   *
   * @param {{start:string, tavg_f:number[], precip_in:number[]}} series
   * @param {string} plantDateIso  "YYYY-MM-DD"
   * @param {string} forecastDateIso  "YYYY-MM-DD"
   * @param {number=} fallbackPrecipMm  Used if the series has no precip data.
   */
  function biomassFromWeatherSeries(series, plantDateIso, forecastDateIso, fallbackPrecipMm) {
    if (!series || !series.tavg_f || !series.start) return null;
    const startD = new Date(series.start + "T00:00:00Z");
    const plantD = new Date(plantDateIso + "T00:00:00Z");
    const fcstD  = new Date(forecastDateIso + "T00:00:00Z");

    const day = 86400000;
    const plantIdx = Math.max(0, Math.round((plantD - startD) / day));
    const fcstIdx  = Math.min(series.tavg_f.length - 1, Math.round((fcstD - startD) / day));
    if (plantIdx > fcstIdx) return null;

    let cumulGdd = 0;
    let cumulPrecipIn = 0;
    let precipObserved = false;
    for (let i = plantIdx; i <= fcstIdx; i++) {
      const tavgF = series.tavg_f[i];
      if (tavgF != null) {
        const tavgC = fahrenheitToCelsius(tavgF);
        cumulGdd += Math.max(0, tavgC);
      }
      const p = series.precip_in ? series.precip_in[i] : null;
      if (p != null) {
        cumulPrecipIn += p;
        if (p > 0) precipObserved = true;
      }
    }

    const cumulPrecipMm = precipObserved
      ? inchesToMm(cumulPrecipIn)
      : (fallbackPrecipMm != null ? fallbackPrecipMm : 0);

    const biomass = predictRyeBiomass(dayOfYear(plantD), cumulPrecipMm, cumulGdd);
    return { biomass, gddTotal: cumulGdd, precipTotalMm: cumulPrecipMm };
  }

  /**
   * Per-day biomass breakdown — same accumulation as
   * ``biomass_timeseries`` in features/crereal_rye_biomass.py.
   *
   * Returns an array of {date, tavg_f, tavg_c, gdd_day, gdd_total,
   * precip_in, precip_mm, precip_total_mm, biomass_pred} covering every
   * day from planting → forecast (inclusive). When ``series.precip_in``
   * is missing or all-zero AND ``fallbackPrecipMm`` is set, that flat
   * value is used for the model's precip term (matching the Streamlit
   * single-station tab).
   *
   * @param {{start:string, tavg_f:number[], precip_in:number[]}} series
   * @param {string} plantDateIso "YYYY-MM-DD"
   * @param {string} forecastDateIso "YYYY-MM-DD"
   * @param {number|null} fallbackPrecipMm
   * @returns {Array} per-day rows; empty array on bad inputs.
   */
  function biomassDailyRowsFromSeries(series, plantDateIso, forecastDateIso, fallbackPrecipMm) {
    if (!series || !series.tavg_f || !series.start) return [];
    const startD = new Date(series.start + "T00:00:00Z");
    const plantD = new Date(plantDateIso + "T00:00:00Z");
    const fcstD  = new Date(forecastDateIso + "T00:00:00Z");
    const dayMs = 86400000;

    const plantIdx = Math.max(0, Math.round((plantD - startD) / dayMs));
    const fcstIdx  = Math.min(series.tavg_f.length - 1, Math.round((fcstD - startD) / dayMs));
    if (plantIdx > fcstIdx) return [];

    const hasRealPrecip = !!(series.precip_in
      && series.precip_in.some((v) => v != null && v > 0));
    const plantDoy = dayOfYear(plantD);

    const rows = [];
    let cumulGdd = 0;
    let cumulPrecipMm = 0;
    for (let i = plantIdx; i <= fcstIdx; i++) {
      const dayTs = new Date(startD.getTime() + i * dayMs);
      const dateIso = dayTs.toISOString().slice(0, 10);
      const tavgF = series.tavg_f[i];
      const tavgC = tavgF == null ? null : fahrenheitToCelsius(tavgF);
      const gddDay = tavgC == null ? 0 : Math.max(0, tavgC);
      cumulGdd += gddDay;

      const precipIn = series.precip_in ? series.precip_in[i] : null;
      const precipMm = precipIn == null ? 0 : inchesToMm(precipIn);
      if (hasRealPrecip) {
        cumulPrecipMm += precipMm;
      }

      const precipForModel = hasRealPrecip
        ? cumulPrecipMm
        : (fallbackPrecipMm != null ? fallbackPrecipMm : 0);
      const biomass = predictRyeBiomass(plantDoy, precipForModel, cumulGdd);

      rows.push({
        date: dateIso,
        tavg_f: tavgF == null ? null : Number(tavgF.toFixed(2)),
        tavg_c: tavgC == null ? null : Number(tavgC.toFixed(2)),
        gdd_day: Number(gddDay.toFixed(2)),
        gdd_total: Number(cumulGdd.toFixed(2)),
        precip_in: precipIn == null ? null : Number(precipIn.toFixed(3)),
        precip_mm: Number(precipMm.toFixed(2)),
        precip_total_mm: Number(
          (hasRealPrecip ? cumulPrecipMm : (fallbackPrecipMm || 0)).toFixed(2)
        ),
        biomass_pred: Number(biomass.toFixed(2)),
      });
    }
    return rows;
  }

  root.Biomass = {
    predictRyeBiomass,
    fahrenheitToCelsius,
    inchesToMm,
    dailyGddCelsius,
    classifyBiomass,
    biomassFromWeatherSeries,
    biomassDailyRowsFromSeries,
    dayOfYear,
    COEF,
  };
})(window);
