// Validates one .glb / .gltf with the official Khronos validator (npm gltf-validator) and prints ONE JSON object on stdout.
// Usage: node scripts/gltf_validate.mjs <file> [--max-issues N]
// Exit code 0 whenever a report was produced (even with errors in the asset); 2 when the file or the validator cannot run.
import { createRequire } from 'node:module';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const here = dirname(fileURLToPath(import.meta.url));
const SEVERITY = ['error', 'warning', 'info', 'hint'];

function fail(status, message) {
  process.stdout.write(JSON.stringify({ status, message }));
  process.exit(2);
}

const args = process.argv.slice(2);
const file = args.find((a) => !a.startsWith('--'));
const maxIdx = args.indexOf('--max-issues');
const maxIssues = maxIdx >= 0 ? Math.max(1, parseInt(args[maxIdx + 1], 10) || 200) : 200;
if (!file) fail('error', 'No file given');
const path = resolve(file);
if (!existsSync(path)) fail('error', `File not found: ${path}`);

let validator;
try {
  validator = require(resolve(here, '..', 'node_modules', 'gltf-validator'));
} catch (e) {
  fail('not_installed', 'The npm package gltf-validator is not installed (run npm install in the app folder)');
}

function jsonOf(bytes, isGlb) {
  if (!isGlb) return JSON.parse(Buffer.from(bytes).toString('utf8'));
  const buf = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (buf.length < 20 || buf.readUInt32LE(0) !== 0x46546c67) return null;
  const length = buf.readUInt32LE(12);
  if (buf.readUInt32LE(16) !== 0x4e4f534a || 20 + length > buf.length) return null;
  return JSON.parse(buf.subarray(20, 20 + length).toString('utf8'));
}

try {
  const bytes = new Uint8Array(readFileSync(path));
  const isGlb = bytes.length >= 4 && bytes[0] === 0x67 && bytes[1] === 0x6c && bytes[2] === 0x54 && bytes[3] === 0x46;
  const options = {
    uri: basename(path),
    maxIssues: maxIssues,
    externalResourceFunction: (uri) => {
      const target = resolve(dirname(path), decodeURIComponent(uri));
      if (!target.startsWith(dirname(path))) return Promise.reject(new Error('Resource outside the model folder'));
      return Promise.resolve(new Uint8Array(readFileSync(target)));
    },
  };
  const report = isGlb ? await validator.validateBytes(bytes, options)
    : await validator.validateString(Buffer.from(bytes).toString('utf8'), options);

  let doc = null;
  try { doc = jsonOf(bytes, isGlb); } catch (e) { doc = null; }
  const count = (key) => (doc && Array.isArray(doc[key]) ? doc[key].length : null);
  const issues = report.issues || {};
  const info = report.info || {};
  const messages = (issues.messages || []).map((m) => ({
    code: m.code,
    severity: SEVERITY[m.severity] || String(m.severity),
    pointer: m.pointer || '',
    message: m.message,
    ...(m.offset !== undefined ? { offset: m.offset } : {}),
  }));
  process.stdout.write(JSON.stringify({
    status: 'done',
    container: isGlb ? 'glb' : 'gltf',
    validator_version: report.validatorVersion,
    errors: issues.numErrors || 0,
    warnings: issues.numWarnings || 0,
    infos: issues.numInfos || 0,
    hints: issues.numHints || 0,
    truncated: !!issues.truncated,
    messages,
    info: {
      version: info.version || null,
      generator: info.generator || null,
      min_version: info.minVersion || null,
      meshes: count('meshes'),
      materials: info.materialCount ?? count('materials'),
      textures: count('textures'),
      images: count('images'),
      animations: info.animationCount ?? count('animations'),
      nodes: count('nodes'),
      skins: count('skins'),
      has_morph_targets: !!info.hasMorphTargets,
      draw_calls: info.drawCallCount ?? null,
      vertices: info.totalVertexCount ?? null,
      triangles: info.totalTriangleCount ?? null,
      extensions_used: info.extensionsUsed || [],
      extensions_required: info.extensionsRequired || [],
    },
  }));
} catch (e) {
  fail('error', String(e && e.message ? e.message : e));
}
