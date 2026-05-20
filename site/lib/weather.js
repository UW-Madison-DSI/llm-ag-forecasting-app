/* Weather time-series chart (dual axis: temperature line + precip bars).
   Uses Chart.js, loaded from CDN in index.html. */

(function (root) {
  "use strict";

  let chartInstance = null;

  function destroyChart() {
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }
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

  // Module-level cache so re-selecting a station re-uses the prior fetch.
  const proxyCache = new Map();   // station_id_lc → series

  async function fetchWeatherViaProxy(stationKey, days = 240) {
    if (proxyCache.has(stationKey)) return proxyCache.get(stationKey);
    try {
      const url = `/proxy/weather?station=${encodeURIComponent(stationKey)}&days=${days}`;
      const resp = await fetch(url, { headers: { Accept: "application/json" } });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const series = await resp.json();
      proxyCache.set(stationKey, series);
      return series;
    } catch (err) {
      console.warn("Weather proxy failed:", err);
      return null;
    }
  }

  async function renderWeatherChart(canvasId, series, stationLabel, stationKey) {
    destroyChart();
    const el = document.getElementById(canvasId);
    if (!el) return;
    const empty = el.parentElement.querySelector(".weather-empty");

    // If no bundled series for this station, fall back to /proxy/weather.
    if ((!series || !series.tavg_f || !series.tavg_f.length) && stationKey) {
      empty.textContent = "Fetching weather…";
      empty.style.display = "block";
      const fetched = await fetchWeatherViaProxy(stationKey);
      if (fetched) series = fetched;
    }

    if (!series || !series.tavg_f || !series.tavg_f.length) {
      empty.textContent = "No weather data available for this station.";
      empty.style.display = "block";
      return;
    }
    empty.style.display = "none";

    const labels = buildDateLabels(series.start, series.tavg_f.length);

    chartInstance = new Chart(el.getContext("2d"), {
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
            tension: 0.2,
            pointRadius: 0,
            borderWidth: 2,
            spanGaps: true,
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
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          title: { display: true, text: stationLabel || "" },
          legend: { position: "top" },
          tooltip: {
            callbacks: {
              title: (items) => items[0].label,
            },
          },
        },
        scales: {
          x: {
            ticks: { autoSkip: true, maxTicksLimit: 12 },
            grid: { display: false },
          },
          y: {
            type: "linear",
            position: "left",
            title: { display: true, text: "°F" },
          },
          y1: {
            type: "linear",
            position: "right",
            title: { display: true, text: "in" },
            grid: { drawOnChartArea: false },
            beginAtZero: true,
          },
        },
      },
    });
  }

  root.WIWeather = { renderWeatherChart };
})(window);
