export const FORMAT_LABEL = { stl: "STL", obj: "OBJ", "3mf": "3MF" };

export const PHASE_LABEL = {
  queued: "En cola",
  scanning: "Explorando carpeta",
  parsing: "Leyendo modelos",
  done: "Al día",
  error: "Error",
  cancelled: "Cancelado",
};

export const UNITS_LABEL = {
  mm: "mm",
  inches: "¿pulgadas?",
  meters: "¿metros?",
  large: "¿demasiado grande?",
};

export const SORT_OPTIONS = [
  { value: "name", label: "Nombre A–Z" },
  { value: "-name", label: "Nombre Z–A" },
  { value: "-date", label: "Más recientes" },
  { value: "date", label: "Más antiguos" },
  { value: "-size", label: "Más grandes (bytes)" },
  { value: "size", label: "Más pequeños (bytes)" },
  { value: "-triangles", label: "Más triángulos" },
  { value: "triangles", label: "Menos triángulos" },
];

export function bytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function num(n, digits = 0) {
  if (n == null) return "—";
  return Number(n).toLocaleString("es-ES", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function mm(v) {
  if (v == null) return "—";
  return v >= 100 ? num(v, 0) : num(v, 1);
}

export function dims(model) {
  if (!model || !model.bbox) return "—";
  const [x, y, z] = model.bbox;
  return `${mm(x)} × ${mm(y)} × ${mm(z)} mm`;
}

export function when(epoch) {
  if (!epoch) return "—";
  const date = new Date(epoch * 1000);
  return date.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function day(epoch) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export function splitTags(text) {
  return text.split(/[,\n;]/).map((t) => t.trim().toLowerCase()).filter(Boolean);
}

/** Plain-text version of a listing, ready to paste into a marketplace form. */
export function listingText(listing, model) {
  if (!listing) return "";
  const parts = [];
  if (listing.title) parts.push(listing.title);
  if (listing.description) parts.push("", listing.description);
  if (model && model.bbox) parts.push("", `Medidas: ${dims(model)}`);
  if (listing.category) parts.push(`Categoría: ${listing.category}`);
  if (listing.tags && listing.tags.length) parts.push(`Etiquetas: ${listing.tags.join(", ")}`);
  if (listing.price_hint) parts.push(`Precio orientativo: ${listing.price_hint}`);
  return parts.join("\n");
}

export function duration(seconds) {
  if (seconds == null || !isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

export const THUMB_MODES = [
  { value: "all", label: "Todos los archivos" },
  { value: "top-level", label: "Solo carpeta raíz y primer nivel" },
  { value: "none", label: "Sin miniaturas" },
];
