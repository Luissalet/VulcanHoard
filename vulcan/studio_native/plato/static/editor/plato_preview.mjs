const PREVIEW_SIZE = 640;
const BACKGROUND = '#f4f0e8';
const GUIDE = '#77736d';
const COLOURS = ['#202020', '#d4553f', '#327a73', '#d29b38', '#6656a3'];

function isMarco(layer) {
  const id = String(layer?.id || '').toLocaleLowerCase();
  const name = String(layer?.name || '').trim().toLocaleLowerCase();
  return name === 'marco' || name.startsWith('marco ')
    || id === 'marco' || id.startsWith('marco_') || id.startsWith('layer_marco_');
}

function previewLayers(doc) {
  return Object.entries(doc?.layers || {})
    .map(([id, layer]) => ({ ...layer, id: layer?.id || id }))
    .filter(layer => !isMarco(layer))
    .filter(layer => layer.visible !== false && layer.hidden !== true)
    .sort((a, b) => (Number(a.stack_rank) || 0) - (Number(b.stack_rank) || 0));
}

function addRing(ctx, points, scale) {
  if (!Array.isArray(points) || points.length < 3) return false;
  ctx.moveTo(Number(points[0][0]) * scale, Number(points[0][1]) * scale);
  for (let i = 1; i < points.length; i += 1) {
    ctx.lineTo(Number(points[i][0]) * scale, Number(points[i][1]) * scale);
  }
  ctx.closePath();
  return true;
}

function canvasBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (blob) resolve(blob);
      else reject(new Error('No se pudo crear la imagen PNG.'));
    }, 'image/png');
  });
}

/** Render the current plate assembly from the server's manufacturing rings. */
export async function createPlatoPreview(
  doc,
  { baseUrl = '/api/v2', fetchImpl = fetch, documentApi = document } = {},
) {
  if (!doc?.id) throw new Error('El documento no tiene identificador.');
  const widthMm = Number(doc.canvas?.width_mm);
  const heightMm = Number(doc.canvas?.height_mm);
  if (!(widthMm > 0) || !(heightMm > 0)) throw new Error('El lienzo del documento no es válido.');

  const layers = previewLayers(doc);
  if (!layers.length) throw new Error('No hay capas visibles para generar la vista del plato.');
  const layerIds = layers.map(layer => layer.id);
  const response = await fetchImpl(
    `${baseUrl}/documents/${encodeURIComponent(doc.id)}/mesh-rings`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: 'normal', layer_ids: layerIds, include_subtree: false }),
    },
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error?.message || `HTTP ${response.status}`);

  for (const layer of layers) {
    const item = payload?.layers?.[layer.id];
    if (item?.error) throw new Error(item.error.message || `No se pudo dibujar la capa ${layer.name || layer.id}.`);
    if (!Array.isArray(item?.rings) || item.rings.length === 0) {
      throw new Error(`La capa ${layer.name || layer.id} no produjo geometría.`);
    }
  }

  const scale = PREVIEW_SIZE / Math.max(widthMm, heightMm);
  const canvas = documentApi.createElement('canvas');
  canvas.width = Math.max(1, Math.round(widthMm * scale));
  canvas.height = Math.max(1, Math.round(heightMm * scale));
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('El navegador no permite dibujar la vista del plato.');

  ctx.fillStyle = BACKGROUND;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = GUIDE;
  ctx.lineWidth = 8;
  ctx.strokeRect(3, 3, Math.max(0, canvas.width - 7), Math.max(0, canvas.height - 7));

  layers.forEach((layer, index) => {
    ctx.fillStyle = COLOURS[index % COLOURS.length];
    ctx.globalAlpha = 0.85;
    for (const polygon of payload.layers[layer.id].rings) {
      ctx.beginPath();
      const hasExterior = addRing(ctx, polygon?.exterior, scale);
      if (!hasExterior) throw new Error(`La capa ${layer.name || layer.id} contiene geometría inválida.`);
      for (const hole of polygon.holes || []) addRing(ctx, hole, scale);
      ctx.fill('evenodd');
    }
  });
  ctx.globalAlpha = 1;
  return canvasBlob(canvas);
}
