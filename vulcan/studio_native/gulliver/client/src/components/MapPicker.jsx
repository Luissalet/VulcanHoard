import React, { useEffect, useRef } from "react";
import L from "leaflet";
import { areaOutline, metersPerDegree, tileLines } from "../geo.js";

// Base maps: the OSM map, a topographic map, and aerial imagery (IGN PNOA in Spain, Esri elsewhere). Display only.
const LAYERS = {
  osm: { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", maxZoom: 19, attribution: "© OpenStreetMap" },
  topo: { url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", maxZoom: 17, attribution: "© OpenTopoMap (CC-BY-SA), © OpenStreetMap" },
  pnoa: { url: "https://tms-pnoa-ma.idee.es/1.0.0/pnoa-ma/{z}/{x}/{y}.jpeg", tms: true, maxZoom: 20, attribution: "PNOA © IGN España" },
  esri: { url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", maxZoom: 19, attribution: "Imagery © Esri" },
};

function handleIcon(cls, label) {
  return L.divIcon({ className: `gm-handle ${cls}`, iconSize: [18, 18], iconAnchor: [9, 9], html: label ? `<span>${label}</span>` : "" });
}

/**
 * The area picker. `value` = {lon, lat, width_m, height_m, shape, tiles_x, tiles_y}; `onChange(patch)` gets new centre / size.
 * Drag the centre handle to move, the corner handle to resize, click the map to recentre. `flyTo` = [w, s, e, n] fits the view.
 */
export default function MapPicker({ value, onChange, flyTo, base = "osm", labels, readOnly = false, pickMode = false, onPick, highlight = null, route = null }) {
  const holder = useRef(null);
  const state = useRef({});
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  // a one-shot click to place something (the highlighted place) instead of recentring the area
  const pickRef = useRef({ armed: pickMode, onPick });
  pickRef.current = { armed: pickMode, onPick };

  // create the map once
  useEffect(() => {
    const map = L.map(holder.current, { zoomControl: true, attributionControl: true, worldCopyJump: true });
    map.setView([value.lat, value.lon], 14);
    // everything outside the area is dimmed (a world ring with the area as its hole), the area gets a strong outline
    const shade = L.polygon([], { stroke: false, fillColor: "#05080a", fillOpacity: 0.42, interactive: false }).addTo(map);
    const outline = L.polygon([], { color: "#1c3d1a", weight: 5, opacity: 0.85, fill: false, interactive: false }).addTo(map);
    const outlineTop = L.polygon([], { color: "#9be38f", weight: 2.2, fill: false, interactive: false }).addTo(map);
    const frame = L.polygon([], { color: "#ffffff", weight: 1, dashArray: "4 4", fill: false, interactive: false, opacity: 0.55 }).addTo(map);
    const grid = L.layerGroup().addTo(map);
    const centre = L.marker([value.lat, value.lon], { draggable: !readOnly, icon: handleIcon("gm-centre"), keyboard: true, title: labels?.move || "" }).addTo(map);
    const corner = L.marker([value.lat, value.lon], { draggable: !readOnly, icon: handleIcon("gm-corner"), keyboard: true, title: labels?.resize || "" }).addTo(map);
    const routeLayer = L.layerGroup().addTo(map);
    const mark = L.marker([value.lat, value.lon], { icon: L.divIcon({ className: "gm-handle gm-highlight", iconSize: [20, 20], iconAnchor: [10, 10], html: "<span></span>" }),
      interactive: false, keyboard: false, zIndexOffset: 500 });
    state.current = { map, outline, outlineTop, shade, frame, grid, centre, corner, value, tile: null, routeLayer, mark };

    centre.on("drag", (e) => {
      const { lat, lng } = e.target.getLatLng();
      state.current.value = { ...state.current.value, lon: lng, lat };
      draw();
    });
    centre.on("dragend", (e) => {
      const { lat, lng } = e.target.getLatLng();
      onChangeRef.current({ lon: round(lng), lat: round(lat) });
    });
    const resize = (e, commit) => {
      const v = state.current.value;
      const { lat, lng } = e.target.getLatLng();
      const [kx, ky] = metersPerDegree(v.lat);
      let w = Math.max(50, Math.abs(lng - v.lon) * kx * 2);
      let h = Math.max(50, Math.abs(lat - v.lat) * ky * 2);
      if (v.shape === "circle" || v.shape === "hexagon") { w = Math.max(w, h); h = w; }
      w = Math.round(w / 10) * 10;
      h = Math.round(h / 10) * 10;
      state.current.value = { ...v, width_m: w, height_m: h };
      draw(!commit);
      if (commit) onChangeRef.current({ width_m: w, height_m: h });
    };
    corner.on("drag", (e) => resize(e, false));
    corner.on("dragend", (e) => resize(e, true));
    map.on("click", (e) => {
      if (readOnly) return;
      if (pickRef.current.armed) {
        pickRef.current.onPick?.({ lon: round(e.latlng.lng), lat: round(e.latlng.lat) });
        return;
      }
      onChangeRef.current({ lon: round(e.latlng.lng), lat: round(e.latlng.lat) });
    });
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(holder.current);
    return () => { observer.disconnect(); map.remove(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // base layer
  useEffect(() => {
    const { map } = state.current;
    if (!map) return undefined;
    const def = LAYERS[base] || LAYERS.osm;
    const layer = L.tileLayer(def.url, { maxZoom: def.maxZoom, tms: !!def.tms, attribution: def.attribution, crossOrigin: true });
    layer.addTo(map);
    layer.bringToBack();
    return () => { map.removeLayer(layer); };
  }, [base]);

  // the highlighted place (gold marker)
  useEffect(() => {
    const { map, mark } = state.current;
    if (!map || !mark) return;
    if (highlight && Number.isFinite(highlight.lon) && Number.isFinite(highlight.lat)) {
      mark.setLatLng([highlight.lat, highlight.lon]);
      if (!map.hasLayer(mark)) mark.addTo(map);
    } else if (map.hasLayer(mark)) {
      map.removeLayer(mark);
    }
  }, [highlight?.lon, highlight?.lat]);

  // the route, drawn as a line (already thinned by the caller)
  useEffect(() => {
    const { routeLayer } = state.current;
    if (!routeLayer) return;
    routeLayer.clearLayers();
    for (const line of route || []) {
      if (line.length < 2) continue;
      L.polyline(line.map(([lon, lat]) => [lat, lon]), { color: "#d2462d", weight: 3.5, opacity: 0.9, interactive: false }).addTo(routeLayer);
    }
  }, [route]);

  // crosshair while a click is armed
  useEffect(() => {
    holder.current?.classList.toggle("gm-picking", !!pickMode);
  }, [pickMode]);

  // follow the value
  useEffect(() => {
    state.current.value = value;
    draw();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.lon, value.lat, value.width_m, value.height_m, value.shape, value.tiles_x, value.tiles_y, value.frame_m]);

  useEffect(() => {
    const { map } = state.current;
    if (map && flyTo && flyTo.length === 4) {
      map.fitBounds([[flyTo[1], flyTo[0]], [flyTo[3], flyTo[2]]], { padding: [40, 40], maxZoom: 17 });
    }
  }, [flyTo]);

  function draw(skipCorner = false) {
    const s = state.current;
    if (!s.map) return;
    const v = s.value;
    const ring = areaOutline(v);
    const latlngs = ring.map(([lon, lat]) => [lat, lon]);
    s.outline.setLatLngs(latlngs);
    s.outlineTop.setLatLngs(latlngs);
    s.shade.setLatLngs([[[-89, -360], [-89, 360], [89, 360], [89, -360]], latlngs]);
    const [kx, ky] = metersPerDegree(v.lat);
    const dlon = v.width_m / 2 / kx;
    const dlat = v.height_m / 2 / ky;
    s.frame.setLatLngs([[v.lat - dlat, v.lon - dlon], [v.lat - dlat, v.lon + dlon], [v.lat + dlat, v.lon + dlon], [v.lat + dlat, v.lon - dlon]]);
    s.frame.setStyle({ opacity: v.shape === "rect" ? 0 : 0.55 });
    s.grid.clearLayers();
    for (const line of tileLines(v)) {
      L.polyline(line.map(([lon, lat]) => [lat, lon]), { color: "#9be38f", weight: 1.6, dashArray: "6 5", interactive: false }).addTo(s.grid);
    }
    s.centre.setLatLng([v.lat, v.lon]);
    if (!skipCorner) s.corner.setLatLng([v.lat + dlat, v.lon + dlon]);
  }

  return <div ref={holder} className="gm-map" role="application" aria-label={labels?.map || "map"} />;
}

function round(x) {
  return Math.round(x * 1e6) / 1e6;
}
