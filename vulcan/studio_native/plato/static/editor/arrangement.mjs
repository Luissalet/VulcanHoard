/** Preserve a deliberate single chain; otherwise sort by visible footprint. */
export function arrangementOrder(layers, areaOf) {
  const ids = new Set(layers.map(layer => layer.id));
  const roots = layers.filter(layer => !ids.has(layer.parent_id));
  if (roots.length === 1) {
    const ordered = [];
    let current = roots[0];
    while (current && !ordered.includes(current)) {
      ordered.push(current);
      const children = layers.filter(layer => layer.parent_id === current.id);
      if (children.length > 1) break;
      current = children[0];
    }
    if (ordered.length === layers.length) return ordered;
  }
  return [...layers].sort((a, b) => areaOf(b) - areaOf(a));
}

function naturalParts(value) {
  return String(value || '').toLocaleLowerCase('en-US').match(/\d+|\D+/g) || [];
}

function compareNaturalDescending(a, b) {
  const aa = naturalParts(a);
  const bb = naturalParts(b);
  const n = Math.max(aa.length, bb.length);
  for (let i = 0; i < n; i++) {
    if (aa[i] === undefined) return 1;
    if (bb[i] === undefined) return -1;
    const an = /^\d+$/.test(aa[i]);
    const bn = /^\d+$/.test(bb[i]);
    if (an && bn) {
      const diff = Number(bb[i]) - Number(aa[i]);
      if (diff) return diff;
      if (aa[i].length !== bb[i].length) return bb[i].length - aa[i].length;
    } else if (an !== bn) {
      return an ? -1 : 1;
    } else if (aa[i] !== bb[i]) {
      return aa[i] < bb[i] ? 1 : -1;
    }
  }
  return String(b || '').localeCompare(String(a || ''), 'en-US');
}

/** Initial import order: national-number prefix descending, then natural Z-A. */
export function importedNameOrder(layers) {
  return [...layers].sort((a, b) => {
    const ap = /^\d+/.exec(String(a?.name || ''));
    const bp = /^\d+/.exec(String(b?.name || ''));
    if (ap && bp) {
      const diff = Number(bp[0]) - Number(ap[0]);
      if (diff) return diff;
    } else if (ap || bp) {
      return ap ? -1 : 1;
    }
    return compareNaturalDescending(a?.name, b?.name);
  });
}

/** Build one isolated imported batch into a deterministic parent chain. */
export async function mountImportedNameChain(layers, setParent) {
  const ordered = importedNameOrder(layers);
  for (const layer of [...ordered].reverse()) {
    if (layer.parent_id) await setParent(layer.id, null);
  }
  for (let i = 1; i < ordered.length; i++) {
    await setParent(ordered[i].id, ordered[i - 1].id);
  }
  return ordered;
}
