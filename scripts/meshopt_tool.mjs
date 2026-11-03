// Mesh simplification with meshoptimizer (MIT, by Arseny Kapoulkine) through its WebAssembly build.
// Called by vulcan/simplify.py: node meshopt_tool.mjs <in.bin> <out.bin> '<json options>'
// in.bin:  uint32 vertex_count, uint32 index_count, uint32 has_lock, float32 positions[vertex_count*3],
//          uint32 indices[index_count], (uint8 lock[vertex_count] when has_lock)
// out.bin: uint32 index_count, uint32 indices[index_count]
// stdout:  JSON {error, scale, indices}; `error` is absolute (mesh units, mm in Vulcan).
import { readFileSync, writeFileSync } from "node:fs";
import { MeshoptSimplifier } from "meshoptimizer";

const [input, output, raw] = process.argv.slice(2);
const options = JSON.parse(raw || "{}");
const buffer = readFileSync(input);
const header = new Uint32Array(buffer.buffer, buffer.byteOffset, 3);
const [vertexCount, indexCount, hasLock] = header;
let offset = buffer.byteOffset + 12;
const positions = new Float32Array(buffer.buffer.slice(offset, offset + vertexCount * 12));
offset += vertexCount * 12;
const indices = new Uint32Array(buffer.buffer.slice(offset, offset + indexCount * 4));
offset += indexCount * 4;
const lock = hasLock ? new Uint8Array(buffer.buffer.slice(offset, offset + vertexCount)) : null;

await MeshoptSimplifier.ready;
const scale = MeshoptSimplifier.getScale(positions, 3);
const target = Math.max(0, Math.floor((options.target_index_count ?? indexCount) / 3) * 3);
const limit = options.target_error_mm > 0 ? options.target_error_mm : scale * 10; // no limit: the ratio alone decides
let result;
let error;
if (options.mode === "sloppy") {
  [result, error] = MeshoptSimplifier.simplifySloppy(indices, positions, 3, lock, target, limit / scale);
  error *= scale; // sloppy reports a relative error
} else {
  const flags = ["ErrorAbsolute"];
  if (options.keep_borders) flags.push("LockBorder");
  if (options.prune) flags.push("Prune");
  [result, error] = MeshoptSimplifier.simplify(indices, positions, 3, target, limit, flags);
}
const out = new Uint8Array(4 + result.byteLength);
new Uint32Array(out.buffer, 0, 1)[0] = result.length;
out.set(new Uint8Array(result.buffer, result.byteOffset, result.byteLength), 4);
writeFileSync(output, out);
process.stdout.write(JSON.stringify({ error, scale, indices: result.length }));
