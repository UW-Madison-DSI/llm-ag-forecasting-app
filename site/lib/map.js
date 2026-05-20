/* Leaflet map setup + marker rendering for the static dashboard.
   OpenStreetMap basemap + plain colored circles sized by risk class
   (Low/Moderate/High). No icon overlay — color is the only signal. */

(function (root) {
  "use strict";

  const WI_CENTER = [44.6, -89.7];
  const WI_ZOOM = 6.5;

  function initMap(elementId) {
    const map = L.map(elementId, { zoomControl: true, scrollWheelZoom: true })
      .setView(WI_CENTER, WI_ZOOM);

    // OpenStreetMap classic — vivid colors (roads, water, parks,
    // towns) so the risk markers really pop against a real basemap.
    L.tileLayer(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        subdomains: "abc",
        maxZoom: 19,
      }
    ).addTo(map);

    return map;
  }

  // Single layer group we replace on every model switch — keeps GC
  // from collecting orphan markers and avoids stacking.
  let markersLayer = null;

  // Plain colored circle markers, sized by risk severity so high-risk
  // sites also read bigger. No emoji overlay — the color is the signal.
  const SIZE_BY_CLASS = {
    "High":     26,
    "Moderate": 22,
     // anything lower (Low / No Risk / Inactive / Unknown) → 18
  };

  function markerSize(riskClass) {
    return SIZE_BY_CLASS[riskClass] || 18;
  }

  function makeIcon(color, riskClass) {
    const size = markerSize(riskClass);
    return L.divIcon({
      className: "station-marker",
      html:
        `<div style="
           background:${color};
           border:2.5px solid white;
           border-radius:50%;
           width:${size}px; height:${size}px;
           box-shadow:0 2px 5px rgba(0,0,0,0.45);">
         </div>`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -(size / 2)],
    });
  }

  function popupHtml(station, model) {
    const valueLabel =
      model.type === "biomass" ? "Predicted biomass" : "Risk";
    const valueText =
      station._value == null || station._value === -1
        ? "n/a"
        : model.type === "biomass"
        ? `${Math.round(station._value).toLocaleString()} ${model.unit || "lb/ac"}`
        : station._value.toFixed(2);

    return `
      <div class="popup-name">${escapeHtml(station.name)}
        <small style="color:#6B7280">(${escapeHtml(station.id)})</small>
      </div>
      <div class="popup-meta">
        ${escapeHtml(station.city || "")},
        ${escapeHtml(station.county || "")}
        — ${escapeHtml(station.region || "")}
      </div>
      <div class="popup-class">
        ${escapeHtml(station._class)}
      </div>
      <div>${valueLabel}: <strong>${valueText}</strong></div>
    `;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderStations(map, stations, model, colors) {
    if (markersLayer) {
      markersLayer.remove();
      markersLayer = null;
    }
    markersLayer = L.layerGroup();

    stations.forEach((s) => {
      if (s.lat == null || s.lon == null) return;
      const color = colors[s._class] || colors.Unknown || "#bdc3c7";
      const marker = L.marker([s.lat, s.lon], { icon: makeIcon(color, s._class) });
      marker.bindPopup(popupHtml(s, model));
      markersLayer.addLayer(marker);
    });

    markersLayer.addTo(map);
  }

  root.WIMap = { initMap, renderStations };
})(window);
