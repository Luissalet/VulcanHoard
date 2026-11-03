// World-space extent of a GLB decoded the way a web viewer does it: three.js GLTFLoader + MeshoptDecoder (the modules
// the app ships), every node transform applied. Used by the tests and by QA to check the physical size of web GLBs.
// Usage: node scripts/glb_extent.mjs <file.glb>
// stdout: JSON {extent: [x, y, z], min, max, triangles, meshes, extensionsUsed, units: "m"} (glTF lengths are metres).
import { readFileSync } from "node:fs";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";

const file = process.argv[2];
const data = readFileSync(file);
const buffer = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
await MeshoptDecoder.ready;
const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
const gltf = await new Promise((resolve, reject) => loader.parse(buffer, "", resolve, reject));
const scene = gltf.scene;
scene.updateMatrixWorld(true);
const box = new THREE.Box3().setFromObject(scene, true);
let triangles = 0;
let meshes = 0;
scene.traverse((node) => {
  if (!node.isMesh) return;
  meshes += 1;
  const geometry = node.geometry;
  triangles += geometry.index ? geometry.index.count / 3 : geometry.attributes.position.count / 3;
});
const size = box.getSize(new THREE.Vector3());
process.stdout.write(JSON.stringify({ extent: size.toArray(), min: box.min.toArray(), max: box.max.toArray(), triangles, meshes,
  extensionsUsed: gltf.parser.json.extensionsUsed || [], units: "m" }));
