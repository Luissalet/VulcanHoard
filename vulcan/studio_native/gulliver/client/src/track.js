// Routes the user already has (GPX, KML, GeoJSON text), read in the browser only to show them on the map and to frame the
// area around them. The server does the real parsing and validation (gulliver_hoard/track.py); this mirrors what it reads:
// GPX track segments and routes, KML line strings and gx:Track, GeoJSON (Multi)LineString.
import { metersPerDegree } from "./geo.js";

export const MAX_TRACK_BYTES = 8_000_000;
const MIN_SIZE_M = 300;
const DRAW_POINTS = 2500;

const num = (x) => (x === null || x === undefined || x === "" ? NaN : Number(x));
const valid = (lon, lat) => Number.isFinite(lon) && Number.isFinite(lat) && lon >= -180 && lon <= 180 && lat >= -90 && lat <= 90;

function dedupe(points) {
  const out = [];
  for (const p of points) {
    if (!valid(p[0], p[1])) continue;
    const q = out[out.length - 1];
    if (q && q[0] === p[0] && q[1] === p[1]) continue;
    out.push(p);
  }
  return out;
}

const local = (el) => String(el.localName || el.nodeName || "").toLowerCase().replace(/^.*:/, "");

function fromXml(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) return null;
  const lines = [];
  const children = (el) => Array.from(el.children || []);
  for (const el of Array.from(doc.getElementsByTagName("*"))) {
    const name = local(el);
    if (name === "trkseg" || name === "rte") {
      const want = name === "trkseg" ? "trkpt" : "rtept";
      lines.push(children(el).filter((c) => local(c) === want).map((c) => [num(c.getAttribute("lon")), num(c.getAttribute("lat"))]));
    } else if (name === "linestring") {
      for (const c of children(el)) {
        if (local(c) !== "coordinates") continue;
        lines.push((c.textContent || "").split(/\s+/).filter(Boolean).map((tok) => {
          const [lon, lat] = tok.split(",");
          return [num(lon), num(lat)];
        }));
      }
    } else if (name === "track") {
      lines.push(children(el).filter((c) => local(c) === "coord").map((c) => {
        const [lon, lat] = (c.textContent || "").trim().split(/\s+/);
        return [num(lon), num(lat)];
      }));
    }
  }
  return lines;
}

function fromGeoJson(obj, out) {
  if (Array.isArray(obj)) { obj.forEach((o) => fromGeoJson(o, out)); return; }
  if (!obj || typeof obj !== "object") return;
  const coords = (c) => (Array.isArray(c) ? c.filter((p) => Array.isArray(p) && p.length >= 2).map((p) => [num(p[0]), num(p[1])]) : []);
  switch (obj.type) {
    case "FeatureCollection": fromGeoJson(obj.features, out); break;
    case "Feature": fromGeoJson(obj.geometry, out); break;
    case "GeometryCollection": fromGeoJson(obj.geometries, out); break;
    case "LineString": out.push(coords(obj.coordinates)); break;
    case "MultiLineString": (obj.coordinates || []).forEach((part) => out.push(coords(part))); break;
    default: break;
  }
}

/** Lines found in a track text as [[lon, lat], ...] lists, or null when the text cannot be read. */
export function parseTrack(text) {
  const body = String(text || "").replace(/^﻿/, "").trim();
  if (!body) return null;
  let raw = null;
  try {
    if (body.startsWith("<")) raw = fromXml(body);
    else if (body.startsWith("{") || body.startsWith("[")) { raw = []; fromGeoJson(JSON.parse(body), raw); }
  } catch {
    return null;
  }
  if (!raw) return null;
  const lines = raw.map(dedupe).filter((l) => l.length >= 2);
  return lines.length ? lines : null;
}

/** What the form shows about a track: point count, bounding box [w, s, e, n] and lines thinned for drawing. */
export function summariseTrack(text) {
  const lines = parseTrack(text);
  if (!lines) return null;
  let points = 0;
  let w = Infinity; let s = Infinity; let e = -Infinity; let n = -Infinity;
  for (const line of lines) {
    for (const [lon, lat] of line) {
      points += 1;
      if (lon < w) w = lon;
      if (lon > e) e = lon;
      if (lat < s) s = lat;
      if (lat > n) n = lat;
    }
  }
  const step = Math.max(1, Math.ceil(points / DRAW_POINTS));
  const draw = lines.map((line) => line.filter((_, i) => i % step === 0 || i === line.length - 1));
  return { lines: lines.length, points, bbox: [w, s, e, n], draw };
}

/** The centre and ground size that frame a bounding box with a margin (x1.3, never under 300 m, square for circle/hexagon). */
export function frameBox(bbox, shape = "rect") {
  const [w, s, e, n] = bbox;
  const lon = (w + e) / 2;
  const lat = (s + n) / 2;
  const [kx, ky] = metersPerDegree(lat);
  const round10 = (x) => Math.min(200000, Math.max(MIN_SIZE_M, Math.ceil((x * 1.3) / 10) * 10));
  let width = round10((e - w) * kx);
  let height = round10((n - s) * ky);
  if (shape === "circle" || shape === "hexagon") { width = Math.max(width, height); height = width; }
  return { lon: Math.round(lon * 1e6) / 1e6, lat: Math.round(lat * 1e6) / 1e6, width_m: width, height_m: height };
}
