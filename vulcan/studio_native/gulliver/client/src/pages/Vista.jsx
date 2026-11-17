import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { Busy, Icon, ICONS, useBusy, useStored } from "../components/ui.jsx";
import { useContextProvider, usePoll } from "../components/hooks.js";
import { areaOutline, metersPerDegree, sizeOfBox } from "../geo.js";
import { num } from "../format.js";

maplibregl.setWorkerUrl(workerUrl);    // the worker is a file of the app's own assets, not looked up next to the library

const CROP_KEYS = ["lon", "lat", "width_m", "height_m", "shape"];
const START = { lon: -3.7038, lat: 40.4168, width_m: 1000, height_m: 1000 };
const LAYER_IDS = ["esri", "osm", "topo", "s2cloudless", "viirs", "modis"];
const FLOOR_COLORS = { corridor: "#c9b27a", walkway: "#c9b27a", area: "#d9c7a0", room: "#7fb3d5", stairs: "#b07fd5", elevator: "#b07fd5", escalator: "#b07fd5" };
const MAX_VIEW_BUILDINGS_M = 5000;
const EMPTY = { type: "FeatureCollection", features: [] };

const round6 = (x) => Math.round(x * 1e6) / 1e6;
const cropOf = (v) => Object.fromEntries(CROP_KEYS.map((k) => [k, v[k]]));

function cropFromQuery(query) {
  const lon = Number(query.get("lon")), lat = Number(query.get("lat"));
  if (!query.get("lon") || !query.get("lat") || !Number.isFinite(lon) || !Number.isFinite(lat)) return null;
  const crop = { lon, lat };
  for (const k of ["width_m", "height_m"]) if (Number.isFinite(Number(query.get(k))) && query.get(k)) crop[k] = Number(query.get(k));
  if (query.get("shape")) crop.shape = query.get("shape");
  return crop;
}

const fromServer = (r) => ({ id: r.view_id, ...r.crop, layer: r.layer.id, date: r.layer.date || "", tiles: r.layer.tiles, layerName: r.layer.name,
  attribution: r.layer.attribution, maxZoom: r.layer.max_zoom, exag: r.terrain_exag, indoor: r.indoor, updated: r.updated, name: r.name || "",
  check: r.model_check });

function lengthText(m, lang) {
  return m >= 10000 ? `${num(m / 1000, 1, lang)} km` : m >= 1000 ? `${num(m / 1000, 2, lang)} km` : `${num(m, 1, lang)} m`;
}
function areaText(m2, lang) {
  return m2 >= 1e6 ? `${num(m2 / 1e6, 3, lang)} km²` : m2 >= 1e4 ? `${num(m2 / 1e4, 2, lang)} ha` : `${num(m2, 0, lang)} m²`;
}

// the crop as a box [west, south, east, north]
function cropBox(v) {
  const [kx, ky] = metersPerDegree(v.lat);
  const h = v.shape === "circle" || v.shape === "hexagon" ? v.width_m : v.height_m;
  return [v.lon - v.width_m / 2 / kx, v.lat - h / 2 / ky, v.lon + v.width_m / 2 / kx, v.lat + h / 2 / ky];
}

function insideExpr(box) {
  return ["all", [">=", ["get", "cx"], box[0]], ["<=", ["get", "cx"], box[2]], [">=", ["get", "cy"], box[1]], ["<=", ["get", "cy"], box[3]]];
}

function cropGeo(v) {
  const ring = areaOutline(v);
  // everything around the crop is dimmed: a frame a few crop sizes wide with the crop as its hole
  const pad = Math.min(5, Math.max(0.5, (Math.max(v.width_m, v.height_m) / 111320) * 40));
  const [w, s, e, n] = [Math.max(-180, v.lon - pad), Math.max(-85, v.lat - pad), Math.min(180, v.lon + pad), Math.min(85, v.lat + pad)];
  return {
    line: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring } }] },
    shade: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon",
      coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]], [...ring].reverse()] } }] },
  };
}

function floorLabel(level, t) {
  if (level.name && !/^z?-?\d+(\.\d+)?$/.test(level.name)) return level.name;
  const n = level.ordinal;
  if (n === 0) return t("v_floor_ground");
  return n < 0 ? t("v_floor_basement", { n: Math.abs(n) }) : t("v_floor_n", { n });
}

function handleElement(cls) {
  const el = document.createElement("div");
  el.className = `gm-handle ${cls}`;
  el.style.width = el.style.height = "18px";
  return el;
}

function NumberInput({ id, label, value, onChange, step, min, max, unit }) {
  return (
    <label className="block" htmlFor={id}>
      <span className="label">{label}</span>
      <div className="unit-field">
        <input id={id} className="field num" type="number" inputMode="decimal" step={step} min={min} max={max} value={value ?? ""}
          onChange={(e) => e.target.value !== "" && onChange(Number(e.target.value))} />
        {unit && <span className="unit">{unit}</span>}
      </div>
    </label>
  );
}

export default function Vista({ query }) {
  const { t, lang, notify, toastError, version } = useApp();
  const holder = useRef(null);
  const mapRef = useRef(null);
  const markers = useRef({});
  const dragging = useRef(false);
  const inflight = useRef(0);
  const seq = useRef(0);
  const pending = useRef({});
  const saveTimer = useRef(null);
  const [last] = useStored("gulliver.lastArea", START);
  const [v, setV] = useState(null);
  const vRef = useRef(null);
  vRef.current = v;
  const [ready, setReady] = useState(false);
  const [layers, setLayers] = useState(null);
  const [tool, setTool] = useState("orbit");
  const toolRef = useRef(tool);
  toolRef.current = tool;
  const [globe, setGlobe] = useState(false);
  const [buildings, setBuildings] = useState({ state: "idle" });
  const [floors, setFloors] = useState({ levels: [], count: 0, source: "" });
  const [level, setLevel] = useState(null);
  const [points, setPoints] = useState([]);
  const [closed, setClosed] = useState(false);
  const [measured, setMeasured] = useState(null);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState([]);
  const [webglFailed, setWebglFailed] = useState(false);
  const [indoorTick, setIndoorTick] = useState(0);
  const [busy, run] = useBusy();

  // -- the view: from the server, from the form's crop, or a new one --------------------------------------------------------
  useEffect(() => {
    let alive = true;
    (async () => {
      const [cat] = await Promise.all([fetch("/api/area/layers").then((r) => r.json())]);
      if (!alive) return;
      setLayers(cat);
      const id = query.get("view") || "";
      const crop = cropFromQuery(query);
      let r;
      if (crop) r = await api.call("area_view", { view_id: id || undefined, ...crop });
      else {
        try { r = await api.call("area_view_get", { view_id: id }); }
        catch { r = await api.call("area_view", { ...START, ...(last || {}) }); }
      }
      if (alive) { setV(fromServer(r)); if (id !== r.view_id) history.replaceState(null, "", `#/vista?view=${r.view_id}`); }
    })().catch((e) => toastError(e));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // changes go to the server a moment after the last one (the layer at once: it brings the tile address back)
  const flush = useCallback(async () => {
    const patch = pending.current;
    pending.current = {};
    clearTimeout(saveTimer.current);
    const cur = vRef.current;
    if (!cur || !Object.keys(patch).length) return;
    inflight.current += 1;
    const mine = ++seq.current;
    try {
      const r = await api.call("area_view", { view_id: cur.id, ...patch });
      if (mine !== seq.current) return;                // a later change already answered
      const fresh = fromServer(r);
      setV((c) => ({ ...c, tiles: fresh.tiles, layer: fresh.layer, date: fresh.date, layerName: fresh.layerName, attribution: fresh.attribution,
        maxZoom: fresh.maxZoom, check: fresh.check, updated: fresh.updated }));
    } catch (e) { toastError(e); } finally { inflight.current -= 1; }
  }, [toastError]);
  const change = useCallback((patch, now = false) => {
    setV((c) => ({ ...c, ...patch }));
    const server = { ...patch };
    if ("exag" in server) { server.terrain_exag = server.exag; delete server.exag; }
    delete server.tiles;
    pending.current = { ...pending.current, ...server };
    clearTimeout(saveTimer.current);
    if (now) flush(); else saveTimer.current = setTimeout(flush, 450);
  }, [flush]);

  // the assistant (or another tab) changed the view: follow it
  const follow = useCallback(async () => {
    const cur = vRef.current;
    if (!cur || dragging.current || inflight.current > 0 || Object.keys(pending.current).length) return;
    const r = await api.call("area_view_get", { view_id: cur.id }).catch(() => null);
    if (r && r.updated > (cur.updated || 0) + 0.001) {
      const next = fromServer(r);
      setV(next);
      if (CROP_KEYS.some((k) => next[k] !== cur[k])) mapRef.current?.fitBounds(cropBox(next), { padding: 60, maxZoom: 17, duration: 800 });
    }
  }, []);
  usePoll(follow, 4000, !!v);
  useEffect(() => { if (version) follow(); }, [version, follow]);

  useContextProvider(() => v && ({ kind: "area-view", object_id: v.id, dirty: false,
    fields: { ...cropOf(v), layer: v.layer, date: v.date, terrain_exag: v.exag, floor: level, measure_points: points.length, measure_m: measured?.length_m } }));

  // -- the map ------------------------------------------------------------------------------------------------------------------
  useEffect(() => {
    if (!v || mapRef.current) return undefined;
    let map;
    try {
      map = new maplibregl.Map({ container: holder.current, center: [v.lon, v.lat], zoom: 14, pitch: 55, bearing: -20, maxPitch: 85, attributionControl: false,
        style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#0d110c" } }] }, canvasContextAttributes: { antialias: true } });
    } catch { setWebglFailed(true); return undefined; }
    mapRef.current = map;
    window.__gulliverMap = map;
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    map.on("load", () => {
      const terrain = { type: "raster-dem", tiles: [layers?.terrain?.tiles || "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
        encoding: "terrarium", tileSize: 256, maxzoom: 15, attribution: t("v_attribution_terrain") };
      map.addSource("dem", terrain);
      map.addSource("dem-shade", terrain);
      map.addLayer({ id: "hillshade", type: "hillshade", source: "dem-shade", paint: { "hillshade-exaggeration": 0.3, "hillshade-shadow-color": "#0a0d08" } });
      map.addSource("crop-shade", { type: "geojson", data: EMPTY });
      map.addSource("crop-line", { type: "geojson", data: EMPTY });
      map.addSource("buildings", { type: "geojson", data: EMPTY });
      map.addSource("indoor", { type: "geojson", data: EMPTY });
      map.addSource("measure", { type: "geojson", data: EMPTY });
      map.addLayer({ id: "crop-shade", type: "fill", source: "crop-shade", paint: { "fill-color": "#05080a", "fill-opacity": 0.38 } });
      map.addLayer({ id: "crop-line-casing", type: "line", source: "crop-line", paint: { "line-color": "#1c3d1a", "line-width": 6, "line-opacity": 0.85 } });
      map.addLayer({ id: "crop-line", type: "line", source: "crop-line", paint: { "line-color": "#9be38f", "line-width": 2.2 } });
      map.addLayer({ id: "buildings-3d", type: "fill-extrusion", source: "buildings", paint: {
        "fill-extrusion-height": ["get", "height"], "fill-extrusion-base": ["min", ["get", "min_height"], ["get", "height"]],
        "fill-extrusion-color": "#e4dccb", "fill-extrusion-opacity": 0.96 } });
      map.addLayer({ id: "indoor-3d", type: "fill-extrusion", source: "indoor", filter: ["==", ["get", "level"], "__none__"], paint: {
        "fill-extrusion-height": ["get", "top"], "fill-extrusion-base": ["get", "base"], "fill-extrusion-color": "#7fb3d5", "fill-extrusion-opacity": 0.96 } });
      map.addLayer({ id: "measure-line", type: "line", source: "measure", filter: ["==", ["geometry-type"], "LineString"],
        paint: { "line-color": "#f2c46b", "line-width": 3 } });
      map.addLayer({ id: "measure-points", type: "circle", source: "measure", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-color": "#f2c46b", "circle-stroke-width": 2.5 } });
      const c = vRef.current;
      map.fitBounds(cropBox(c), { padding: 80, maxZoom: 17, duration: 0, pitch: 55, bearing: -20 });
      setReady(true);
    });
    map.on("click", (e) => {
      if (toolRef.current === "centre") {
        change({ lon: round6(e.lngLat.lng), lat: round6(e.lngLat.lat) });
      } else if (toolRef.current === "measure") {
        setPoints((p) => (p.length >= 500 ? p : [...p, [round6(e.lngLat.lng), round6(e.lngLat.lat)]]));
      }
    });
    return () => { map.remove(); mapRef.current = null; window.__gulliverMap = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [!!v]);

  // picture layer by date (the server turned the layer and its date into a tile address)
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !v?.tiles) return;
    if (map.getLayer("imagery")) map.removeLayer("imagery");
    if (map.getSource("imagery")) map.removeSource("imagery");
    map.addSource("imagery", { type: "raster", tiles: [v.tiles], tileSize: 256, maxzoom: v.maxZoom || 18, attribution: v.attribution || "" });
    map.addLayer({ id: "imagery", type: "raster", source: "imagery" }, "hillshade");
  }, [ready, v?.tiles]);

  // relief
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || v?.exag === undefined) return;
    if (v.exag > 0) map.setTerrain({ source: "dem", exaggeration: v.exag }); else map.setTerrain(null);
  }, [ready, v?.exag]);

  // the Earth curved or flat
  useEffect(() => {
    const map = mapRef.current;
    if (ready && map) map.setProjection({ type: globe ? "globe" : "mercator" });
  }, [ready, globe]);

  // the crop: outline, shade and the two handles
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !v) return;
    const geo = cropGeo(v);
    map.getSource("crop-shade").setData(geo.shade);
    map.getSource("crop-line").setData(geo.line);
    map.setPaintProperty("buildings-3d", "fill-extrusion-color", ["case", insideExpr(cropBox(v)), "#e4dccb", "#8a8780"]);
    const [kx, ky] = metersPerDegree(v.lat);
    const h = v.shape === "circle" || v.shape === "hexagon" ? v.width_m : v.height_m;
    const corner = [v.lon + v.width_m / 2 / kx, v.lat + h / 2 / ky];
    if (!markers.current.centre) {
      const centre = new maplibregl.Marker({ element: handleElement("gm-centre"), draggable: true }).setLngLat([v.lon, v.lat]).addTo(map);
      const cornerMarker = new maplibregl.Marker({ element: handleElement("gm-corner"), draggable: true }).setLngLat(corner).addTo(map);
      centre.on("dragstart", () => { dragging.current = true; });
      centre.on("drag", () => { const p = centre.getLngLat(); setV((c) => ({ ...c, lon: round6(p.lng), lat: round6(p.lat) })); });
      centre.on("dragend", () => { dragging.current = false; const p = centre.getLngLat(); change({ lon: round6(p.lng), lat: round6(p.lat) }); });
      const resize = (commit) => {
        const c = vRef.current;
        const p = cornerMarker.getLngLat();
        const [ax, ay] = metersPerDegree(c.lat);
        let w = Math.max(50, Math.abs(p.lng - c.lon) * ax * 2), hh = Math.max(50, Math.abs(p.lat - c.lat) * ay * 2);
        if (c.shape === "circle" || c.shape === "hexagon") { w = Math.max(w, hh); hh = w; }
        w = Math.round(w / 10) * 10; hh = Math.round(hh / 10) * 10;
        if (commit) change({ width_m: w, height_m: hh }); else setV((cur) => ({ ...cur, width_m: w, height_m: hh }));
      };
      cornerMarker.on("dragstart", () => { dragging.current = true; });
      cornerMarker.on("drag", () => resize(false));
      cornerMarker.on("dragend", () => { dragging.current = false; resize(true); });
      markers.current = { centre, corner: cornerMarker };
    } else if (!dragging.current) {
      markers.current.centre.setLngLat([v.lon, v.lat]);
      markers.current.corner.setLngLat(corner);
    }
  }, [ready, v?.lon, v?.lat, v?.width_m, v?.height_m, v?.shape]);

  // buildings around the crop (the server keeps what it fetched)
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !v) return undefined;
    const ctl = new AbortController();
    const h = v.shape === "circle" || v.shape === "hexagon" ? v.width_m : v.height_m;
    const timer = setTimeout(async () => {
      if (Math.max(v.width_m, h) > MAX_VIEW_BUILDINGS_M) {
        map.getSource("buildings").setData(EMPTY);
        setBuildings({ state: "skipped" });
        return;
      }
      setBuildings({ state: "loading" });
      try {
        const q = new URLSearchParams({ lon: v.lon, lat: v.lat, width_m: v.width_m, height_m: v.height_m, shape: v.shape });
        const r = await fetch(`/api/area/buildings?${q}`, { signal: ctl.signal });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || r.status);
        map.getSource("buildings").setData(data);
        setBuildings({ state: "ready", count: data.count, source: data.source, attribution: data.attribution });
      } catch (e) { if (e.name !== "AbortError") setBuildings({ state: "error", message: e.message }); }
    }, 700);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [ready, v?.lon, v?.lat, v?.width_m, v?.height_m, v?.shape]);

  // the floors of the interior data
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !v) return undefined;
    const ctl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const q = new URLSearchParams({ view_id: v.id, lon: v.lon, lat: v.lat, width_m: v.width_m, height_m: v.height_m });
        const r = await fetch(`/api/area/indoor?${q}`, { signal: ctl.signal });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || r.status);
        map.getSource("indoor").setData(data);
        setFloors({ levels: data.levels || [], count: data.features.length, source: data.source });
        setLevel((cur) => (cur !== null && (data.levels || []).some((l) => l.id === cur) ? cur : null));
      } catch (e) {
        if (e.name !== "AbortError") { setFloors({ levels: [], count: 0, source: "", error: e.message }); }
      }
    }, 900);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [ready, v?.lon, v?.lat, v?.width_m, v?.height_m, v?.indoor, indoorTick]);

  // one floor chosen: it and the ones below it, the rest of the buildings fade
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const chosen = floors.levels.find((l) => l.id === level);
    if (!chosen) {
      map.setFilter("indoor-3d", ["==", ["get", "level"], "__none__"]);
      map.setPaintProperty("buildings-3d", "fill-extrusion-opacity", 0.96);
      return;
    }
    map.setFilter("indoor-3d", ["<=", ["get", "ordinal"], chosen.ordinal]);
    const colour = ["match", ["get", "category"], ...Object.entries(FLOOR_COLORS).flat(), "#9fb7c8"];
    map.setPaintProperty("indoor-3d", "fill-extrusion-color", ["case", ["==", ["get", "ordinal"], chosen.ordinal], colour, "#58697a"]);
    map.setPaintProperty("buildings-3d", "fill-extrusion-opacity", 0.16);
  }, [ready, level, floors.levels]);

  // measuring: the server computes it on the ellipsoid and sends back the curved path
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return undefined;
    const dots = points.map((p) => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p } }));
    if (points.length < 2) {
      map.getSource("measure").setData({ type: "FeatureCollection", features: dots });
      setMeasured(null);
      return undefined;
    }
    let alive = true;
    const timer = setTimeout(async () => {
      try {
        const r = await api.call("area_measure", { points, closed: closed && points.length >= 3 });
        if (!alive) return;
        setMeasured(r);
        map.getSource("measure").setData({ type: "FeatureCollection", features: [...dots,
          { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: r.curve } }] });
      } catch (e) { if (alive) toastError(e); }
    }, 120);
    return () => { alive = false; clearTimeout(timer); };
  }, [ready, points, closed]);
  useEffect(() => { if (points.length < 3) setClosed(false); }, [points.length]);

  useEffect(() => {
    const canvas = mapRef.current?.getCanvas();
    if (canvas) canvas.style.cursor = tool === "orbit" ? "" : "crosshair";
  }, [tool, ready]);

  // -- actions ------------------------------------------------------------------------------------------------------------------
  const doSearch = async (e) => {
    e?.preventDefault();
    if (!search.trim()) return;
    const r = await run("search", () => api.call("place_search", { query: search.trim() }));
    if (r) setResults(r.results || []);
  };
  const pick = (hit) => {
    const size = sizeOfBox(hit.bbox);
    const k = Math.min(1, 3000 / Math.max(size.width_m, size.height_m));
    const w = Math.max(200, Math.round((size.width_m * k) / 50) * 50), h = Math.max(200, Math.round((size.height_m * k) / 50) * 50);
    const next = { lon: round6(hit.lon), lat: round6(hit.lat), width_m: w, height_m: v.shape === "circle" || v.shape === "hexagon" ? w : h };
    change(next, true);
    mapRef.current?.fitBounds(cropBox({ ...v, ...next }), { padding: 80, maxZoom: 17, duration: 1200 });
    setResults([]);
  };
  const fit = () => mapRef.current?.fitBounds(cropBox(v), { padding: 80, maxZoom: 17, duration: 800 });
  const pitchTo = (pitch) => mapRef.current?.easeTo({ pitch, bearing: pitch === 0 ? 0 : mapRef.current.getBearing(), duration: 600 });
  const setSize = (patch) => change(v.shape === "circle" || v.shape === "hexagon" ? { width_m: patch.width_m ?? patch.height_m, height_m: patch.width_m ?? patch.height_m } : patch);
  const setShape = (shape) => change(shape === "circle" || shape === "hexagon" ? { shape, height_m: v.width_m } : { shape });
  const pickLayer = (id) => {
    const info = layers.layers.find((l) => l.id === id);
    change({ layer: id, date: info?.default_date || "" }, true);
  };
  const toModel = async () => {
    await flush();
    const crop = cropOf(vRef.current);
    const q = new URLSearchParams({ view: v.id, ...Object.fromEntries(Object.entries(crop).map(([k, val]) => [k, String(val)])) });
    window.location.hash = `#/nueva?${q}`;
  };
  const buildNow = async () => {
    await flush();
    const settings = await run("now", () => api.call("settings_get"));
    if (!settings) return;
    const made = await run("now", () => api.call("model_create", { view_id: v.id, name: v.name || undefined, preset: settings.default_preset || "city", params: {}, build: true }));
    if (made) { notify(t("v_made")); window.location.hash = `#/m/${made.id}`; }
  };
  const upload = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    await flush();
    try {
      const r = await fetch(`/api/area/indoor/upload?${new URLSearchParams({ view_id: v.id, name: file.name })}`, { method: "POST", body: file });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || r.status);
      setV((c) => ({ ...c, indoor: "file", updated: Date.now() / 1000 }));
      setIndoorTick((n) => n + 1);
      notify(t("v_floors_uploaded"));
    } catch (err) { notify(`${t("v_floors_error")}: ${err.message}`, "error"); }
  };

  const layerInfo = useMemo(() => layers?.layers.find((l) => l.id === v?.layer), [layers, v?.layer]);
  if (!v) return <div className="p-6"><span className="spinner" /> {t("v_loading_view")}</div>;
  const square = v.shape === "circle" || v.shape === "hexagon";
  const check = v.check;
  const floorsSorted = [...floors.levels].sort((a, b) => b.ordinal - a.ordinal);

  return (
    <div className="new-layout" data-testid="vista">
      <div className="new-map">
        <form className="map-search" onSubmit={doSearch} role="search">
          <input className="field" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("v_search")} aria-label={t("v_search")} />
          <Busy busy={busy.search} className="btn btn-primary" type="submit" onClick={doSearch} aria-label={t("v_search_go")}><Icon d={ICONS.search} size={15} /></Busy>
          {results.length > 0 && (
            <ul className="map-results" role="listbox">
              {results.map((r, i) => <li key={i}><button type="button" onClick={() => pick(r)}><b>{r.name.split(",")[0]}</b><span className="help">{r.name}</span></button></li>)}
            </ul>
          )}
        </form>
        <div className="map-layers seg" role="group" aria-label="tool">
          {["orbit", "centre", "measure"].map((k) => <button key={k} type="button" aria-pressed={tool === k} onClick={() => setTool(k)} data-testid={`tool-${k}`}>{t(`v_tool_${k}`)}</button>)}
        </div>
        <div className="vista-bar">
          <button type="button" className="btn btn-sm btn-icon" onClick={fit} aria-label={t("v_fit")} title={t("v_fit")}><Icon d={ICONS.target} size={15} /></button>
          <button type="button" className="btn btn-sm" onClick={() => pitchTo(0)} title={t("v_top")}>{t("v_top")}</button>
          <button type="button" className="btn btn-sm" onClick={() => pitchTo(65)} title={t("v_tilt")}>{t("v_tilt")}</button>
          <button type="button" className="btn btn-sm" aria-pressed={globe} onClick={() => setGlobe(!globe)} data-testid="globe">{globe ? t("v_flat") : t("v_globe")}</button>
        </div>
        <div ref={holder} className="gm-map" role="application" aria-label={t("v_map")} />
        {webglFailed && <div className="map-hint help" role="alert">{t("v_no_webgl")}</div>}
        <div className="map-hint help">{t(`v_hint_${tool}`)}</div>
      </div>

      <div className="new-panel">
        <div className="space-y-3">
          <h1>{t("v_title")}</h1>
          {v.name && <div className="help trunc" title={v.name}>{v.name}</div>}
        </div>

        <details className="cfg-group" open>
          <summary><Icon d={ICONS.target} size={15} /> <span>{t("v_crop")}</span><Icon d={ICONS.chevronDown} size={14} className="chev" /></summary>
          <div className="cfg-body">
            <div className="help">{t("v_crop_help")}</div>
            <div className="grid grid-cols-2 gap-3">
              <NumberInput id="v-w" label={square ? t("v_diameter") : t("v_width")} unit="m" step={50} min={50} value={v.width_m} onChange={(x) => setSize({ width_m: x })} />
              {!square && <NumberInput id="v-h" label={t("v_height")} unit="m" step={50} min={50} value={v.height_m} onChange={(x) => setSize({ height_m: x })} />}
              <NumberInput id="v-lat" label={t("lat")} step={0.0001} value={v.lat} onChange={(x) => change({ lat: x })} />
              <NumberInput id="v-lon" label={t("lon")} step={0.0001} value={v.lon} onChange={(x) => change({ lon: x })} />
            </div>
            <div className="seg w-full" role="group">
              {["rect", "rounded", "circle", "hexagon"].map((k) => <button key={k} type="button" className="flex-1" aria-pressed={v.shape === k} onClick={() => setShape(k)}>{t(`shape_${k}`)}</button>)}
            </div>
            {check && <div className="help" data-testid="model-check">{t("v_summary", { scale: check.scale, w: check.model_mm[0], h: check.model_mm[1] })}
              {!check.fits_bed && check.suggested_tiles && <div>{t("v_not_fit", { x: check.suggested_tiles[0], y: check.suggested_tiles[1] })}</div>}</div>}
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-primary flex-1" onClick={toModel} data-testid="to-model"><Icon d={ICONS.cube} size={14} /> {t("v_make")}</button>
              <Busy busy={busy.now} className="btn" onClick={buildNow}><Icon d={ICONS.play} size={14} /> {t("v_make_now")}</Busy>
            </div>
          </div>
        </details>

        <details className="cfg-group" open>
          <summary><Icon d={ICONS.layers} size={15} /> <span>{t("v_imagery")}</span><Icon d={ICONS.chevronDown} size={14} className="chev" /></summary>
          <div className="cfg-body">
            <label className="block">
              <span className="label">{t("v_layer")}</span>
              <select className="field" value={v.layer} onChange={(e) => pickLayer(e.target.value)} data-testid="layer">
                {LAYER_IDS.map((k) => <option key={k} value={k}>{t(`v_layer_${k}`)}</option>)}
              </select>
            </label>
            {layerInfo?.dated === "year" && (
              <label className="block">
                <span className="label">{t("v_year")}</span>
                <select className="field" value={v.date} onChange={(e) => change({ date: e.target.value }, true)} data-testid="date">
                  {layerInfo.years.map((y) => <option key={y} value={String(y)}>{y}</option>)}
                </select>
              </label>
            )}
            {layerInfo?.dated === "day" && (
              <label className="block">
                <span className="label">{t("v_date")}</span>
                <input className="field" type="date" min={layerInfo.min_date} max={layerInfo.max_date} value={v.date}
                  onChange={(e) => e.target.value && change({ date: e.target.value }, true)} data-testid="date" />
              </label>
            )}
            {layerInfo?.dated ? <div className="help">{t("v_date_help")}</div> : null}
            <div className="help">{v.attribution}</div>
          </div>
        </details>

        <details className="cfg-group" open>
          <summary><Icon d={ICONS.cube} size={15} /> <span>{t("v_relief")}</span><Icon d={ICONS.chevronDown} size={14} className="chev" /></summary>
          <div className="cfg-body">
            <label className="block range-field" htmlFor="v-exag">
              <span className="label range-label"><span>{t("v_exag")}</span><b className="num">{num(v.exag, 1, lang)}×</b></span>
              <input id="v-exag" type="range" min="0" max="5" step="0.1" value={v.exag} onChange={(e) => change({ exag: Number(e.target.value) })} />
            </label>
            <div className="help" data-testid="buildings-status">
              {buildings.state === "loading" && t("v_buildings_loading")}
              {buildings.state === "ready" && (buildings.count ? t("v_buildings_count", { n: num(buildings.count, 0, lang), source: t(`v_source_${buildings.source}`) }) : t("v_buildings_none"))}
              {buildings.state === "skipped" && t("v_buildings_none")}
              {buildings.state === "error" && `${t("v_buildings_error")}: ${buildings.message}`}
            </div>
          </div>
        </details>

        <details className="cfg-group" open={tool === "measure" || points.length > 0}>
          <summary><Icon d={ICONS.ruler} size={15} /> <span>{t("v_measure")}</span><Icon d={ICONS.chevronDown} size={14} className="chev" /></summary>
          <div className="cfg-body">
            <div className="help">{t("v_measure_help")}</div>
            {!points.length ? <div className="help">{t("v_measure_empty")}</div> : (
              <div data-testid="measure">
                <div>{t("v_measure_points", { n: points.length })}</div>
                {measured && <div><b>{t("v_measure_length")}:</b> <span data-testid="measure-length">{lengthText(measured.length_m, lang)}</span></div>}
                {measured?.area_m2 !== undefined && <div><b>{t("v_measure_area")}:</b> <span data-testid="measure-area">{areaText(measured.area_m2, lang)}</span> · {t("v_measure_perimeter")} {lengthText(measured.perimeter_m, lang)}</div>}
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-sm" disabled={points.length < 3} onClick={() => setClosed(!closed)}>{closed ? t("v_measure_open") : t("v_measure_close")}</button>
              <button type="button" className="btn btn-sm" disabled={!points.length} onClick={() => setPoints((p) => p.slice(0, -1))}>{t("v_measure_undo")}</button>
              <button type="button" className="btn btn-sm" disabled={!points.length} onClick={() => { setPoints([]); setClosed(false); }}>{t("v_measure_clear")}</button>
            </div>
          </div>
        </details>

        <details className="cfg-group" open={floors.levels.length > 0}>
          <summary><Icon d={ICONS.layers} size={15} /> <span>{t("v_floors")}</span><Icon d={ICONS.chevronDown} size={14} className="chev" /></summary>
          <div className="cfg-body">
            <div className="help">{t("v_floors_help")}</div>
            {floors.levels.length ? (
              <>
                <div className="help" data-testid="floors-count">{t("v_floors_count", { n: floors.count, m: floors.levels.length })}</div>
                <div className="seg w-full" role="group" style={{ flexWrap: "wrap" }}>
                  <button type="button" className="flex-1" aria-pressed={level === null} onClick={() => setLevel(null)}>{t("v_floors_off")}</button>
                  {floorsSorted.map((l) => <button key={l.id} type="button" className="flex-1" aria-pressed={level === l.id} onClick={() => setLevel(l.id)} data-testid={`floor-${l.id}`}>{floorLabel(l, t)}</button>)}
                </div>
              </>
            ) : <div className="help">{floors.error ? `${t("v_floors_error")}: ${floors.error}` : t("v_floors_none")}</div>}
            <input type="file" className="field" accept=".zip,.gml,.xml,.igml,.geojson" onChange={upload} aria-label={t("v_floors_file")} />
            {v.indoor !== "auto" && <button type="button" className="btn btn-sm" onClick={() => change({ indoor: "auto" }, true)}>{t("v_floors_osm")}</button>}
          </div>
        </details>
      </div>
    </div>
  );
}
