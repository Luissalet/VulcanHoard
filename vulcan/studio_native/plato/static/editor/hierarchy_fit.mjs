// Interactive edits keep the chosen centre. Only look for another position
// when the requested clearance leaves no feasible size there.
export async function fitNestedPose(store, layerId, paddingMm, { mode = 'best', quality = 'full' } = {}) {
  const layer = store.layerById(layerId);
  if (!layer?.parent_id) return null;
  const modes = mode === 'at_position' ? ['at_position', 'best'] : [mode];
  for (const attempt of modes) {
    const response = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/fit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        layer_id: layerId, target: 'parent_shape', mode: attempt, quality,
        pose: layer.pose, padding_mm: paddingMm,
        angles_deg: [Number(layer.pose?.angle_deg) || 0],
        max_evaluations: 6000, seed: 42, request_seq: Date.now(),
      }),
    });
    const body = await response.json();
    const pose = body.pose_local || body.pose;
    if (response.ok && pose && ['tx', 'ty', 'scale', 'angle_deg'].every(key => Number.isFinite(pose[key])) && pose.scale > 0) return pose;
    if (attempt === 'at_position' && response.status === 422) continue;
    throw new Error(body?.error?.message || 'No se pudo calcular el encaje');
  }
}

// A reparented subtree already has an arrangement. Preserve it and only
// repair descendants whose clearance no longer fits after the parent scales.
export async function constrainDescendants(store, rootId, paddingMm, onChange = () => {}) {
  const descendants = store.subtreeIds(rootId).slice(1);
  let changed = 0;
  for (const id of descendants) {
    const layer = store.layerById(id);
    if (!layer?.parent_id || layer.locked) continue;
    const response = await fetch(`${store.baseUrl}/documents/${encodeURIComponent(store.doc.id)}/constrain`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ layer_id: id, pose: layer.pose, padding_mm: paddingMm, quality: 'preview' }),
    });
    const body = await response.json();
    const pose = body.pose_local || body.pose;
    if (!response.ok || !pose || !['tx', 'ty', 'scale', 'angle_deg'].every(key => Number.isFinite(pose[key])) || pose.scale <= 0) {
      throw new Error(body?.error?.message || `No se pudo comprobar ${layer.name || id}`);
    }
    // Avoid document writes, history snapshots and redraws for unchanged poses.
    if (['tx', 'ty', 'scale', 'angle_deg'].every(key => Math.abs(pose[key] - layer.pose[key]) < 1e-9)) continue;
    await store.commitCommand('set_pose', { layer_id: id, pose });
    changed += 1;
    onChange();
  }
  return changed;
}
