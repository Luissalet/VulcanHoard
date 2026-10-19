// Same local projection as the backend (gulliver_hoard/geo.py): metres per degree from the WGS84 radii at a latitude.
const A = 6378137.0;
const E2 = 6.69437999014e-3;

export function metersPerDegree(lat) {
  const phi = (lat * Math.PI) / 180;
  const s = Math.sin(phi);
  const w = Math.sqrt(1 - E2 * s * s);
  const n = A / w;
  const m = (A * (1 - E2)) / (w * w * w);
  const rad = Math.PI / 180;
  return [rad * n * Math.cos(phi), rad * m];
}

// The outline of the model on the ground as [[lon, lat], ...] (closed), following the backend's outline shapes.
export function areaOutline(v) {
  const [kx, ky] = metersPerDegree(v.lat);
  const toLL = ([x, y]) => [v.lon + x / kx, v.lat + y / ky];
  const w = v.width_m;
  const h = v.shape === "circle" || v.shape === "hexagon" ? v.width_m : v.height_m;
  let pts;
  if (v.shape === "circle") {
    const r = Math.min(w, h) / 2;
    pts = Array.from({ length: 73 }, (_, i) => [r * Math.cos((i * 5 * Math.PI) / 180), r * Math.sin((i * 5 * Math.PI) / 180)]);
  } else if (v.shape === "hexagon") {
    const r = Math.min(w, h) / 2;
    pts = Array.from({ length: 7 }, (_, i) => [r * Math.cos((i * 60 * Math.PI) / 180), r * Math.sin((i * 60 * Math.PI) / 180)]);
  } else if (v.shape === "rounded") {
    const r = Math.min(w, h) * 0.08;
    pts = [];
    const corners = [[w / 2 - r, h / 2 - r, 0], [-w / 2 + r, h / 2 - r, 90], [-w / 2 + r, -h / 2 + r, 180], [w / 2 - r, -h / 2 + r, 270]];
    for (const [cx, cy, a0] of corners) {
      for (let a = a0; a <= a0 + 90; a += 15) pts.push([cx + r * Math.cos((a * Math.PI) / 180), cy + r * Math.sin((a * Math.PI) / 180)]);
    }
    pts.push(pts[0]);
  } else {
    pts = [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2], [-w / 2, -h / 2]];
  }
  return pts.map(toLL);
}

// The seams between tiles as lines [[lon, lat], [lon, lat]].
export function tileLines(v) {
  const tx = Math.max(1, v.tiles_x || 1);
  const ty = Math.max(1, v.tiles_y || 1);
  const [kx, ky] = metersPerDegree(v.lat);
  const w = v.width_m;
  const h = v.shape === "circle" || v.shape === "hexagon" ? v.width_m : v.height_m;
  const lines = [];
  for (let c = 1; c < tx; c++) {
    const x = -w / 2 + (w * c) / tx;
    lines.push([[v.lon + x / kx, v.lat - h / 2 / ky], [v.lon + x / kx, v.lat + h / 2 / ky]]);
  }
  for (let r = 1; r < ty; r++) {
    const y = -h / 2 + (h * r) / ty;
    lines.push([[v.lon - w / 2 / kx, v.lat + y / ky], [v.lon + w / 2 / kx, v.lat + y / ky]]);
  }
  return lines;
}

// Ground size (rounded) that matches a bounding box [w, s, e, n].
export function sizeOfBox(box) {
  const lat = (box[1] + box[3]) / 2;
  const [kx, ky] = metersPerDegree(lat);
  const w = Math.max(200, Math.min(60000, (box[2] - box[0]) * kx));
  const h = Math.max(200, Math.min(60000, (box[3] - box[1]) * ky));
  const r = (x) => (x > 5000 ? Math.round(x / 500) * 500 : Math.round(x / 50) * 50);
  return { width_m: r(w), height_m: r(h), lon: (box[0] + box[2]) / 2, lat };
}
