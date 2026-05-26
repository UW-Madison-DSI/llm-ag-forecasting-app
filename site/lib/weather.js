/* Weather chart helpers.

   Two rendering paths:
     • renderInlineWeather() — original single-station Tavg+precip dual-axis
       chart shown on the Disease Forecast tab. Falls back to /proxy/weather
       (legacy shape) when no series is bundled.
     • renderMultiWeather()  — new Weather Data tab: one line per station for
       a single field, fetched via /proxy/weather with stations=&fields=.
*/

(function (root) {
  "use strict";

  const STATION_PALETTE = [
    "#C5050C", "#1d4ed8", "#047857", "#b45309", "#6d28d9",
    "#0e7490", "#be185d", "#4d7c0f", "#7c2d12", "#0f766e",
    "#a16207", "#3730a3", "#9f1239", "#365314", "#1e3a8a",
  ];

  let inlineChart = null;
  let multiChart = null;

  function destroyInline() { if (inlineChart) { inlineChart.destroy(); inlineChart = null; } }
  function destroyMulti()  { if (multiChart)  { multiChart.destroy();  multiChart  = null; } }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function buildDateLabels(startIso, n) {
    const start = new Date(startIso + "T00:00:00Z");
    const labels = new Array(n);
    for (let i = 0; i < n; i++) {
      const d = new Date(start);
      d.setUTCDate(d.getUTCDate() + i);
      labels[i] = d.toISOString().slice(0, 10);
    }
    return labels;
  }

  /* -------------------- Disease-tab inline chart -------------------- */

  async function renderInlineWeather(canvasId, series, stationLabel, stationKey) {
    destroyInline();
    const el = document.getElementById(canvasId);
    if (!el) return;
    const empty = el.parentElement.querySelector(".weather-empty");

    // If no bundled series for this station, fall back to /proxy/weather.
    if ((!series || !series.tavg_f || !series.tavg_f.length) && stationKey) {
      empty.textContent = "Fetching weather…";
      empty.style.display = "block";
      const fetched = await root.ForecastAPI.fetchWeatherLegacy(stationKey);
      if (fetched) series = fetched;
    }

    if (!series || !series.tavg_f || !series.tavg_f.length) {
      empty.textContent = "No weather data available for this station.";
      empty.style.display = "block";
      return;
    }
    empty.style.display = "none";

    const labels = buildDateLabels(series.start, series.tavg_f.length);

    inlineChart = new Chart(el.getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Daily avg temp (°F)",
            data: series.tavg_f,
            borderColor: "#C5050C",
            backgroundColor: "rgba(197, 5, 12, 0.10)",
            yAxisID: "y",
            tension: 0.2, pointRadius: 0, borderWidth: 2, spanGaps: true,
          },
          {
            label: "Daily precip (in)",
            data: series.precip_in,
            type: "bar",
            backgroundColor: "rgba(37, 99, 235, 0.55)",
            borderColor: "rgba(37, 99, 235, 0.55)",
            yAxisID: "y1",
          },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          title: { display: true, text: stationLabel || "" },
          legend: { position: "top" },
          tooltip: { callbacks: { title: (items) => items[0].label } },
        },
        scales: {
          x: { ticks: { autoSkip: true, maxTicksLimit: 12 }, grid: { display: false } },
          y:  { type: "linear", position: "left",  title: { display: true, text: "°F" } },
          y1: { type: "linear", position: "right", title: { display: true, text: "in" },
                grid: { drawOnChartArea: false }, beginAtZero: true },
        },
      },
    });
  }

  /* -------------------- Weather Data tab: multi-station, one field -------------------- */

  /**
   * @param {Object} payload  Response from ForecastAPI.fetchWeather.
   * @param {string} field    Single field name to plot.
   * @param {Object} stationNames  {station_id_lower: "Display Name (id)"}.
   */
  function renderMultiWeather(payload, field, stationNames) {
    destroyMulti();
    const canvas = document.getElementById("weather-multi-chart");
    const empty = document.getElementById("weather-empty");
    if (!payload || !payload.dates || !payload.data) {
      empty.textContent = "No data returned.";
      empty.style.display = "block";
      document.getElementById("weather-table").innerHTML = "";
      return;
    }
    empty.style.display = "none";

    const dates = payload.dates;
    let colorIdx = 0;
    const datasets = [];
    let nonEmpty = 0;

    for (const sid of payload.stations) {
      const series = (payload.data[sid] || {})[field] || [];
      const hasValue = series.some((v) => v != null);
      if (hasValue) nonEmpty++;
      const color = STATION_PALETTE[colorIdx++ % STATION_PALETTE.length];
      const displayName = stationNames[sid] || sid;
      datasets.push({
        label: displayName,
        data: series,
        borderColor: color,
        backgroundColor: color,
        spanGaps: true, tension: 0.18, pointRadius: 0, borderWidth: 2,
      });
    }

    if (!nonEmpty) {
      empty.textContent = `No observations for "${field}" in this window.`;
      empty.style.display = "block";
      document.getElementById("weather-table").innerHTML = "";
      return;
    }

    const units = (payload.units || {})[field] || "";
    const yLabel = units ? `${field} (${units})` : field;

    multiChart = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: { labels: dates, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          title: { display: true, text: field + (units ? `  (${units})` : "") },
          legend: { position: "top" },
        },
        scales: {
          x: { ticks: { autoSkip: true, maxTicksLimit: 14 } },
          y: { title: { display: true, text: yLabel } },
        },
      },
    });

    renderMultiTable(payload, field, stationNames);
  }

  function renderMultiTable(payload, field, stationNames) {
    const dates = payload.dates;
    const stations = payload.stations;
    const headerCells = stations
      .map((sid) => `<th>${escapeHtml(stationNames[sid] || sid)}</th>`)
      .join("");
    const rows = dates.map((d, i) => {
      const cells = stations
        .map((sid) => {
          const v = ((payload.data[sid] || {})[field] || [])[i];
          return `<td>${v == null ? "" : Number(v).toFixed(2)}</td>`;
        })
        .join("");
      return `<tr><td>${escapeHtml(d)}</td>${cells}</tr>`;
    }).join("");
    document.getElementById("weather-table").innerHTML = `
      <thead><tr><th>Date</th>${headerCells}</tr></thead>
      <tbody>${rows}</tbody>`;
  }

  root.WIWeather = {
    renderInlineWeather,
    renderMultiWeather,
    // Back-compat alias for existing callers.
    renderWeatherChart: renderInlineWeather,
  };
})(window);
