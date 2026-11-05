// Independent reimport of an animated GLB the way a web runtime does it: three.js GLTFLoader + AnimationMixer.
// Every clip is played once to its end (LoopOnce, clamped) and the local pose of each animated node is reported,
// keyed by glTF node index, so Vulcan can compare it with its own evaluation of the file.
// Textures are removed from the JSON before parsing (Node has no image decoder); geometry, skins and clips are untouched.
// Usage: node scripts/anim_reimport.mjs <file.glb>
// stdout: JSON {clips: [{name, duration, tracks, plays, final: {nodeIndex: {translation, rotation, scale}}}], skinnedMeshes, bones}
import { readFileSync } from "node:fs";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

function withoutTextures(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== 0x46546c67) throw new Error("not a GLB");
  let offset = 12, json = null, bin = null;
  while (offset + 8 <= bytes.byteLength) {
    const length = view.getUint32(offset, true), type = view.getUint32(offset + 4, true);
    const chunk = bytes.subarray(offset + 8, offset + 8 + length);
    if (type === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(chunk));
    else if (type === 0x004e4942) bin = chunk;
    offset += 8 + length;
  }
  const strip = (obj) => { for (const key of Object.keys(obj || {})) { if (key.endsWith("Texture")) delete obj[key]; else if (obj[key] && typeof obj[key] === "object") strip(obj[key]); } };
  (json.materials || []).forEach(strip);
  delete json.images; delete json.textures; delete json.samplers;
  const text = new TextEncoder().encode(JSON.stringify(json));
  const pad = (n) => (4 - (n % 4)) % 4;
  const jsonLength = text.length + pad(text.length), binLength = bin ? bin.length + pad(bin.length) : 0;
  const total = 12 + 8 + jsonLength + (bin ? 8 + binLength : 0);
  const out = new Uint8Array(total), dv = new DataView(out.buffer);
  dv.setUint32(0, 0x46546c67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
  dv.setUint32(12, jsonLength, true); dv.setUint32(16, 0x4e4f534a, true);
  out.set(text, 20); out.fill(0x20, 20 + text.length, 20 + jsonLength);
  if (bin) { const at = 20 + jsonLength; dv.setUint32(at, binLength, true); dv.setUint32(at + 4, 0x004e4942, true); out.set(bin, at + 8); }
  return out.buffer;
}

const file = process.argv[2];
const gltf = await new Promise((resolve, reject) => new GLTFLoader().parse(withoutTextures(readFileSync(file)), "", resolve, reject));
const scene = gltf.scene;
const indexOf = new Map();
for (const [object, entry] of gltf.parser.associations) if (object && object.isObject3D && entry && entry.nodes !== undefined) indexOf.set(object, entry.nodes);
const rest = [];
scene.traverse((o) => rest.push([o, o.position.clone(), o.quaternion.clone(), o.scale.clone()]));
const restore = () => rest.forEach(([o, p, q, s]) => { o.position.copy(p); o.quaternion.copy(q); o.scale.copy(s); });
let skinnedMeshes = 0, bones = 0;
scene.traverse((o) => { if (o.isSkinnedMesh) skinnedMeshes += 1; if (o.isBone) bones += 1; });
const pose = (o) => ({ translation: o.position.toArray(), rotation: o.quaternion.toArray(), scale: o.scale.toArray() });
const clips = [];
for (const clip of gltf.animations) {
  restore();
  const mixer = new THREE.AnimationMixer(scene);
  const action = mixer.clipAction(clip);
  action.setLoop(THREE.LoopOnce, 1);
  action.clampWhenFinished = true;
  action.play();
  const targets = new Set();
  for (const track of clip.tracks) {
    const name = THREE.PropertyBinding.parseTrackName(track.name).nodeName;
    const object = scene.getObjectByName(name);
    if (object) targets.add(object);
  }
  const moved = (a, b) => a.some((p, i) => ["translation", "rotation", "scale"].some((k) => p[k].some((v, j) => Math.abs(v - b[i][k][j]) > 1e-7)));
  mixer.setTime(0);
  const start = [...targets].map((o) => pose(o));
  let plays = false;
  for (const f of [0.17, 0.25, 0.5, 0.75, 0.9]) { // the clip moves something at some point while playing
    mixer.setTime(clip.duration * f);
    if (moved(start, [...targets].map((o) => pose(o)))) plays = true;
  }
  mixer.setTime(clip.duration + 1e-6); // LoopOnce + clampWhenFinished: the last frame
  const final = {};
  for (const o of targets) if (indexOf.has(o)) final[String(indexOf.get(o))] = pose(o);
  if (moved(start, [...targets].map((o) => pose(o)))) plays = true;
  clips.push({ name: clip.name, duration: clip.duration, tracks: clip.tracks.length, plays, final });
  mixer.stopAllAction();
  mixer.uncacheRoot(scene);
}
process.stdout.write(JSON.stringify({ clips, skinnedMeshes, bones }));
