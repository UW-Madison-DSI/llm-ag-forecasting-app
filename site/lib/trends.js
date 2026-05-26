/* Risk Trends rendering — mirrors the Streamlit "Risk Trends" tab.

   Inputs:
     • the full multi-day forecast payload (riskDays>1) for the current
       (date, model) selection
     • the user's selected stations (subset of payload)

   Outputs:
     • a line chart of risk value over forecasting_date, one line per station
     • a stacked bar chart of class counts per forecasting_date
*/

(function (root) {
  "use strict";

  // Distinct, accessible-ish color cycle for the per-station lines.
  // Chart.js default palette is too pale on a white background.
  const STATION_PALETTE = [
    "#C5050C", "#1d4ed8", "#047857", "#b45309", "#6d28d9",
    "#0e7490", "#be185d", "#4d7c0f", "#7c2d12", "#0f766e",
    "#a16207", "#3730a3", "#9f1239", "#365314", "#1e3a8a",
  ];

  let lineChart = null;
  let barChart = null;

  function destroy() {
    if (lineChart) { lineChart.destroy(); lineChart = null; }
    if (barChart)  { barChart.destroy();  barChart = null; }
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /**
   * @param {Array} rows           Output of ForecastAPI.flattenForecast (multi-day).
   * @param {Object} model         Disease model entry (has risk_field, class_field).
   * @param {string[]} stationIds  Selected station_ids.
   * @param {Object} classColors   {High, Moderate, Low, ...} → hex
   */
  function render(rows, model, stationIds, classColors) {
    destroy();
    const empty = document.getElementById("trends-empty");
    if (!rows || !rows.length || !stationIds || !stationIds.length) {
      empty.style.display = "block";
      empty.textContent = "Pick at least one station above and run a forecast.";
      document.getElementById("trends-table").innerHTML = "";
      return;
    }
    empty.style.display = "none";

    const riskField  = model.risk_field;
    const classField = model.class_field;

    const subset = rows.filter((r) => stationIds.includes(r.id));
    if (!subset.length) {
      empty.style.display = "block";
      empty.textContent = "No timeseries data for the selected stations.";
      document.getElementById("trends-table").innerHTML = "";
      return;
    }

    // Collect every distinct forecasting_date, sorted.
    const dateSet = new Set();
    subset.forEach((r) => { if (r.date) dateSet.add(r.date); });
    const dates = Array.from(dateSet).sort();

    // Build one line dataset per station, aligned to the `dates` axis.
    const byStation = new Map();
    subset.forEach((r) => {
      if (!byStation.has(r.id)) {
        byStation.set(r.id, { name: r.name, byDate: new Map() });
      }
      const v = r[riskField];
      const numeric = (v == null || v === -1) ? null : Number(v);
      byStation.get(r.id).byDate.set(r.date, numeric);
    });

    const lineDatasets = [];
    let colorIdx = 0;
    for (const sid of stationIds) {
      const s = byStation.get(sid);
      if (!s) continue;
      const color = STATION_PALETTE[colorIdx++ % STATION_PALETTE.length];
      lineDatasets.push({
        label: s.name,
        data: dates.map((d) => s.byDate.has(d) ? s.byDate.get(d) : null),
        borderColor: color,
        backgroundColor: color,
        spanGaps: false,
        tension: 0.18,
        pointRadius: 3,
        borderWidth: 2,
      });
    }

    const lineCtx = document.getElementById("trends-line-chart").getContext("2d");
    lineChart = new Chart(lineCtx, {
      type: "line",
      data: { labels: dates, datasets: lineDatasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          title: { display: true, text: `${model.label} — risk over time` },
          legend: { position: "top" },
        },
        scales: {
          x: {
            title: { display: true, text: "Forecasting date" },
            ticks: { autoSkip: true, maxTicksLimit: 14 },
          },
          y: {
            title: { display: true, text: "Risk value" },
            beginAtZero: true,
          },
        },
      },
    });

    // Stacked bar — class counts per date across the selected stations.
    const classNames = ["High", "Moderate", "Low", "No Risk", "Inactive", "Unknown"];
    const counts = {};
    classNames.forEach((c) => { counts[c] = dates.map(() => 0); });

    subset.forEach((r) => {
      const cls = r[classField] || "Unknown";
      const bucket = classNames.includes(cls) ? cls : "Unknown";
      const idx = dates.indexOf(r.date);
      if (idx >= 0) counts[bucket][idx] += 1;
    });

    const presentClasses = classNames.filter((c) => counts[c].some((n) => n > 0));
    const barDatasets = presentClasses.map((c) => ({
      label: c,
      data: counts[c],
      backgroundColor: classColors[c] || "#95a5a6",
      stack: "risk",
    }));

    const barCtx = document.getElementById("trends-bar-chart").getContext("2d");
    barChart = new Chart(barCtx, {
      type: "bar",
      data: { labels: dates, datasets: barDatasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          title: { display: true, text: "Risk class distribution per day" },
          legend: { position: "top" },
        },
        scales: {
          x: { stacked: true, title: { display: true, text: "Forecasting date" } },
          y: {
            stacked: true,
            beginAtZero: true,
            title: { display: true, text: "Stations" },
            ticks: { precision: 0 },
          },
        },
      },
    });

    renderTable(subset, riskField, classField);
  }

  function renderTable(rows, riskField, classField) {
    const sorted = rows.slice().sort((a, b) => {
      if (a.name !== b.name) return a.name.localeCompare(b.name);
      return (a.date || "").localeCompare(b.date || "");
    });
    const trs = sorted.map((r) => {
      const v = r[riskField];
      const valueStr = (v == null || v === -1) ? "n/a" : Number(v).toFixed(2);
      return `<tr>
          <td>${escapeHtml(r.name)} <small style="color:#6B7280">(${escapeHtml(r.id)})</small></td>
          <td>${escapeHtml(r.date || "")}</td>
          <td>${valueStr}</td>
          <td>${escapeHtml(r[classField] || "Unknown")}</td>
        </tr>`;
    }).join("");
    document.getElementById("trends-table").innerHTML = `
      <thead>
        <tr>
          <th>Station</th>
          <th>Forecasting date</th>
          <th>Risk value</th>
          <th>Class</th>
        </tr>
      </thead>
      <tbody>${trs}</tbody>`;
  }

  root.WITrends = { render, destroy };
})(window);
