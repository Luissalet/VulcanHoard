// Where each named CAD dimension is used, as the recipe stores it (see vulcan/cad.py design_intent).
export const CAD_NODE_NAMES = {
  box: "caja", rounded_box: "caja redondeada", cylinder: "cilindro", sphere: "esfera", hole: "agujero",
  union: "unión", intersection: "intersección", difference: "diferencia", translate: "traslación", rotate: "giro",
  scale: "escala", linear_pattern: "patrón lineal", polar_pattern: "patrón polar",
  extrude: "extrusión de boceto", revolve: "revolución de perfil",
};

const place = (use) => `${use.label || CAD_NODE_NAMES[use.type] || use.type} (${use.field})`;

export function cadUsage(param) {
  const direct = (param?.used_by || []).map(place);
  const indirect = (param?.indirect || []).map((use) => `${place(use)} a través de ${use.via}`);
  return { direct, indirect, drives: param?.drives || [], unused: !direct.length && !indirect.length };
}

export function cadUsageText(param) {
  const usage = cadUsage(param);
  if (usage.unused) return "Ninguna operación de la receta usa este valor.";
  const parts = [];
  if (usage.direct.length) parts.push(`Afecta a: ${usage.direct.join("; ")}.`);
  if (usage.indirect.length) parts.push(`También: ${usage.indirect.join("; ")}.`);
  if (usage.drives.length) parts.push(`Define: ${usage.drives.join(", ")}.`);
  return parts.join(" ");
}
