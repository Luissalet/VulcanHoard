// Octahedral impostors for three.js: each far model is two triangles that show the baked view nearest the camera, blended with the
// two neighbouring views (the triangle of the hemi-octahedral grid that holds the camera direction). The atlas layout and the camera
// basis of every view are the ones vulcan/impostor.py bakes: cell (i, j), row j from the top, direction decoded from
// u = -1 + 2 i / (N - 1), v = -1 + 2 j / (N - 1); right = normalize(Z x d) (X when d is vertical), up = d x right.
// Per fragment the camera ray is intersected with each view's plane (through the bounding-sphere centre, facing that view), so the
// three samples line up even though each view was drawn with its own orientation.
// Atlases go into texture arrays (one layer per model, `perBatch` layers per array) and every batch is one instanced draw call.
import * as THREE from "three";

const VERTEX = /* glsl */ `
attribute vec3 iCenter;
attribute float iRadius;
attribute float iLayer;
attribute float iVisible;
uniform float uGrid;
varying vec3 vWorld;
varying vec3 vCenter;
varying float vRadius;
varying float vLayer;
varying vec4 vFrameA;
varying vec2 vFrameC;
varying vec3 vWeights;

vec2 encodeHemi(vec3 d) {
  d.z = max(d.z, 0.0);
  d /= max(abs(d.x) + abs(d.y) + d.z, 1e-6);
  return vec2(d.x + d.y, d.x - d.y);
}

void main() {
  if (iVisible < 0.5) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); return; }
  vec3 toCamera = normalize(cameraPosition - iCenter);
  vec3 right = cross(vec3(0.0, 0.0, 1.0), toCamera);
  float length0 = length(right);
  right = length0 > 1e-4 ? right / length0 : vec3(1.0, 0.0, 0.0);
  vec3 up = cross(toCamera, right);
  vec3 world = iCenter + (right * position.x + up * position.y) * iRadius * 1.08;
  vWorld = world;
  vCenter = iCenter;
  vRadius = iRadius;
  vLayer = iLayer;
  vec2 g = (encodeHemi(toCamera) * 0.5 + 0.5) * (uGrid - 1.0);
  vec2 cell = clamp(floor(g), vec2(0.0), vec2(uGrid - 2.0));
  vec2 f = clamp(g - cell, 0.0, 1.0);
  if (f.x + f.y < 1.0) {
    vFrameA = vec4(cell, cell + vec2(1.0, 0.0));
    vFrameC = cell + vec2(0.0, 1.0);
    vWeights = vec3(1.0 - f.x - f.y, f.x, f.y);
  } else {
    vFrameA = vec4(cell + vec2(1.0, 1.0), cell + vec2(0.0, 1.0));
    vFrameC = cell + vec2(1.0, 0.0);
    vWeights = vec3(f.x + f.y - 1.0, 1.0 - f.x, 1.0 - f.y);
  }
  gl_Position = projectionMatrix * viewMatrix * vec4(world, 1.0);
}
`;

const FRAGMENT = /* glsl */ `
precision highp float;
precision highp sampler2DArray;
uniform sampler2DArray uAtlas;
uniform float uGrid;
uniform float uBlend;
varying vec3 vWorld;
varying vec3 vCenter;
varying float vRadius;
varying float vLayer;
varying vec4 vFrameA;
varying vec2 vFrameC;
varying vec3 vWeights;

vec4 sampleFrame(vec2 frame, vec3 origin, vec3 ray) {
  vec2 uv = -1.0 + 2.0 * frame / (uGrid - 1.0);
  vec3 d = vec3((uv.x + uv.y) * 0.5, (uv.x - uv.y) * 0.5, 0.0);
  d.z = 1.0 - abs(d.x) - abs(d.y);
  d = normalize(d);
  vec3 right = cross(vec3(0.0, 0.0, 1.0), d);
  float length0 = length(right);
  right = length0 > 1e-4 ? right / length0 : vec3(1.0, 0.0, 0.0);
  vec3 up = normalize(cross(d, right));
  float denom = dot(ray, d);
  if (abs(denom) < 1e-5) return vec4(0.0);
  vec3 q = origin + ray * (dot(vCenter - origin, d) / denom) - vCenter;
  vec2 local = vec2(dot(q, right), dot(q, up)) / (2.0 * vRadius) + 0.5;
  if (local.x < 0.0 || local.y < 0.0 || local.x > 1.0 || local.y > 1.0) return vec4(0.0);
  vec2 atlas = vec2((frame.x + local.x) / uGrid, (frame.y + 1.0 - local.y) / uGrid);
  vec4 s = texture(uAtlas, vec3(atlas, vLayer));
  return vec4(s.rgb * s.a, s.a);
}

void main() {
  vec3 ray = normalize(vWorld - cameraPosition);
  vec3 w = uBlend > 0.5 ? vWeights : vec3(1.0, 0.0, 0.0);
  vec4 c = sampleFrame(vFrameA.xy, cameraPosition, ray) * w.x;
  if (w.y > 0.001) c += sampleFrame(vFrameA.zw, cameraPosition, ray) * w.y;
  if (w.z > 0.001) c += sampleFrame(vFrameC, cameraPosition, ray) * w.z;
  if (c.a < 0.35) discard; // below 0.5 so the blend of three views does not erode thin silhouettes
  gl_FragColor = vec4(c.rgb / c.a, 1.0);
}
`;

const QUAD = new Float32Array([-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0]);
const INDEX = [0, 1, 2, 0, 2, 3];

function makeBatch(grid, layerSize, capacity) {
  const data = new Uint8Array(layerSize * layerSize * 4 * capacity);
  const texture = new THREE.DataArrayTexture(data, layerSize, layerSize, capacity);
  texture.format = THREE.RGBAFormat;
  texture.type = THREE.UnsignedByteType;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  const geometry = new THREE.InstancedBufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(QUAD, 3));
  geometry.setIndex(INDEX);
  const attr = (size) => new THREE.InstancedBufferAttribute(new Float32Array(capacity * size), size).setUsage(THREE.DynamicDrawUsage);
  geometry.setAttribute("iCenter", attr(3));
  geometry.setAttribute("iRadius", attr(1));
  geometry.setAttribute("iLayer", attr(1));
  geometry.setAttribute("iVisible", attr(1));
  geometry.instanceCount = 0;
  const material = new THREE.ShaderMaterial({
    vertexShader: VERTEX, fragmentShader: FRAGMENT,
    uniforms: { uAtlas: { value: texture }, uGrid: { value: grid }, uBlend: { value: 1 } },
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.frustumCulled = false;
  mesh.name = "impostor-batch";
  return { data, texture, geometry, material, mesh, count: 0, dirty: false };
}

/** A set of impostors sharing one atlas layout. add() places them, load() fetches the atlases, setVisible() swaps one for its mesh. */
export class ImpostorField {
  constructor(scene, { grid = 8, layerSize = 256, perBatch = 64 } = {}) {
    this.scene = scene;
    this.grid = grid;
    this.layerSize = layerSize;
    this.perBatch = perBatch;
    this.batches = [];
    this.slots = new Map(); // id -> { batch, index, url, loaded }
    this.canvas = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(layerSize, layerSize) : Object.assign(document.createElement("canvas"), { width: layerSize, height: layerSize });
    this.context = this.canvas.getContext("2d", { willReadFrequently: true });
    this.loaded = 0;
    this.failed = 0;
  }

  add({ id, center, radius, url }) {
    let batch = this.batches[this.batches.length - 1];
    if (!batch || batch.count >= this.perBatch) {
      batch = makeBatch(this.grid, this.layerSize, this.perBatch);
      this.batches.push(batch);
      this.scene.add(batch.mesh);
    }
    const index = batch.count++;
    const g = batch.geometry;
    g.attributes.iCenter.setXYZ(index, center[0], center[1], center[2]);
    g.attributes.iRadius.setX(index, radius);
    g.attributes.iLayer.setX(index, index);
    g.attributes.iVisible.setX(index, 0); // shown once its atlas is in
    g.instanceCount = batch.count;
    for (const name of ["iCenter", "iRadius", "iLayer", "iVisible"]) g.attributes[name].needsUpdate = true;
    this.slots.set(id, { batch, index, url, loaded: false, wanted: true });
  }

  setVisible(id, visible) {
    const slot = this.slots.get(id);
    if (!slot) return;
    slot.wanted = visible;
    const value = visible && slot.loaded ? 1 : 0;
    const attribute = slot.batch.geometry.attributes.iVisible;
    if (attribute.getX(slot.index) !== value) { attribute.setX(slot.index, value); attribute.needsUpdate = true; }
  }

  isLoaded(id) { return !!this.slots.get(id)?.loaded; }

  setBlend(on) { for (const b of this.batches) b.material.uniforms.uBlend.value = on ? 1 : 0; }

  get drawn() {
    let n = 0;
    for (const b of this.batches) { const a = b.geometry.attributes.iVisible; for (let i = 0; i < b.count; i += 1) n += a.getX(i) > 0.5 ? 1 : 0; }
    return n;
  }

  async loadOne(id, slot) {
    const response = await fetch(slot.url);
    if (!response.ok) throw new Error(`atlas ${id}: ${response.status}`);
    const bitmap = await createImageBitmap(await response.blob());
    const size = this.layerSize;
    this.context.clearRect(0, 0, size, size);
    this.context.drawImage(bitmap, 0, 0, size, size);
    bitmap.close?.();
    const pixels = this.context.getImageData(0, 0, size, size).data;
    slot.batch.data.set(pixels, slot.index * size * size * 4);
    slot.batch.dirty = true;
    slot.loaded = true;
    this.setVisible(id, slot.wanted);
  }

  /** Fetch every atlas (a few at a time); textures are uploaded in groups so the GPU is not fed one layer per frame. */
  async load({ concurrency = 6, onProgress } = {}) {
    const queue = [...this.slots.entries()].filter(([, slot]) => !slot.loaded);
    let next = 0;
    const flush = () => { for (const b of this.batches) if (b.dirty) { b.texture.needsUpdate = true; b.dirty = false; } };
    const timer = setInterval(flush, 400);
    const worker = async () => {
      while (next < queue.length && !this.disposed) {
        const [id, slot] = queue[next++];
        try { await this.loadOne(id, slot); this.loaded += 1; } catch { this.failed += 1; }
        onProgress?.(this.loaded, this.slots.size);
      }
    };
    try { await Promise.all(Array.from({ length: concurrency }, worker)); } finally { clearInterval(timer); flush(); }
  }

  dispose() {
    this.disposed = true;
    for (const b of this.batches) {
      this.scene.remove(b.mesh);
      b.geometry.dispose();
      b.material.dispose();
      b.texture.dispose();
    }
    this.batches = [];
    this.slots.clear();
  }
}
