"""Texts of destination inspection in Spanish (Spain) and English: key -> {es, en}, filled with str.format.

The same convention as ``hoard_link.paths`` (key -> {"es", "en"}). Only human-readable strings live here; machine fields
(status codes, rule ids, measurement ids, numbers) never change with the language. The language of a call is held in a
context variable set by the public entry points (``with language(lang):``), so the measurement and planning helpers
do not need a ``lang`` argument each. The REST routes default to Spanish (the app's UI); agent/MCP tools to English.
"""

from __future__ import annotations

import contextlib
import contextvars

LANGS = ("es", "en")
_current: contextvars.ContextVar[str] = contextvars.ContextVar("asset_texts_lang", default="en")

TEXTS: dict[str, dict[str, str]] = {
    # --- measurement sources ---
    "m.triangles": {"es": "carga nueva con trimesh, todas las instancias de la escena", "en": "trimesh fresh load, all scene instances"},
    "m.vertices_stored": {"es": "vértices tal como se guardan (partidos en costuras de UV/normales)", "en": "vertices as stored (split at UV/normal seams)"},
    "m.vertices_welded": {"es": "posiciones únicas tras soldar", "en": "unique positions after welding"},
    "m.open_edges": {"es": "aristas usadas por un solo triángulo (posiciones soldadas)", "en": "edges used by one triangle (welded positions)"},
    "m.nonmanifold_edges": {"es": "aristas usadas por más de dos triángulos (posiciones soldadas)", "en": "edges used by more than two triangles (welded positions)"},
    "m.watertight": {"es": "sin aristas abiertas ni no manifold", "en": "no open and no non-manifold edges"},
    "m.winding": {"es": "comprobación de orientación de trimesh", "en": "trimesh winding check"},
    "m.bodies": {"es": "componentes conexas de la malla soldada", "en": "connected components of the welded mesh"},
    "m.bodies_unavailable": {"es": "análisis de grafo no disponible", "en": "graph analysis unavailable"},
    "m.degenerate": {"es": "triángulos de área cero", "en": "triangles with zero area"},
    "m.bbox": {"es": "dimensiones alineadas con los ejes en unidades del archivo ({unit})", "en": "axis-aligned extents in file units ({unit})"},
    "m.bbox_mm": {"es": "dimensiones convertidas desde {unit}", "en": "extents converted from {unit}"},
    "m.bbox_mm_assumed": {"es": "dimensiones convertidas desde {unit} (supuesto)", "en": "extents converted from {unit} (assumed)"},
    "m.volume": {"es": "volumen encerrado por la malla cerrada", "en": "enclosed volume of the closed mesh"},
    "m.volume_open": {"es": "malla abierta: no encierra volumen", "en": "open mesh: no enclosed volume"},
    "m.unit_gltf": {"es": "glTF usa metros por especificación", "en": "glTF unit is metres by specification"},
    "m.unit_3mf": {"es": "unidad declarada en el archivo 3MF", "en": "unit declared in the 3MF file"},
    "m.unit_none_format": {"es": "{fmt} no declara unidades", "en": "{fmt} does not declare units"},
    "m.unit_none_file": {"es": "el archivo no declara unidad", "en": "the file declares no unit"},
    "m.unit_declared": {"es": "unidad declarada", "en": "declared unit"},
    "m.unit_assumed": {"es": "se suponen milímetros (el archivo no los declara)", "en": "assumed millimetres (not declared by the file)"},
    "m.assumed_flag": {"es": "verdadero si la unidad no estaba declarada", "en": "true when the unit was not declared"},
    "m.size_hint": {"es": "heurística sobre la dimensión mayor (los objetos imprimibles miden 5-500 mm)", "en": "heuristic on the largest extent (printable objects are 5-500 mm)"},
    "m.format": {"es": "extensión del archivo", "en": "file extension"},
    "m.bytes": {"es": "tamaño en disco", "en": "size on disk"},
    "m.stl_cannot": {"es": "STL no puede guardar {what}", "en": "STL cannot store {what}"},
    "w.uvs": {"es": "coordenadas UV", "en": "UVs"},
    "w.vertex_colors": {"es": "colores de vértice", "en": "vertex colours"},
    "w.materials": {"es": "materiales", "en": "materials"},
    "w.textures": {"es": "texturas", "en": "textures"},
    "w.animations": {"es": "animaciones", "en": "animations"},
    "m.stl_normals": {"es": "STL guarda una normal por triángulo; sin normales de vértice", "en": "STL stores one normal per triangle; no vertex normals"},
    "m.stl_scene": {"es": "STL no tiene estructura de escena", "en": "STL has no scene structure"},
    "m.obj_vt": {"es": "{n} líneas vt", "en": "{n} vt lines"},
    "m.obj_vn": {"es": "{n} líneas vn", "en": "{n} vn lines"},
    "m.obj_colors": {"es": "{n} líneas v con componentes de color", "en": "{n} v lines with colour components"},
    "m.obj_materials": {"es": "entradas newmtl en {libs}", "en": "newmtl entries in {libs}"},
    "m.obj_no_mtl": {"es": "ningún MTL", "en": "no MTL"},
    "m.obj_maps": {"es": "entradas map_* del MTL", "en": "map_* entries in the MTL"},
    "m.obj_sizes": {"es": "tamaño en píxeles de cada imagen de textura (null = falta o ilegible)", "en": "pixel size of each texture image (null = missing or unreadable)"},
    "m.obj_animations": {"es": "OBJ no puede guardar animaciones", "en": "OBJ cannot store animations"},
    "m.obj_scene": {"es": "OBJ tiene grupos pero no jerarquía de nodos", "en": "OBJ has groups but no node hierarchy"},
    "m.gltf_tris": {"es": "recuentos de los accessors glTF (índices / 3) por instancia de nodo", "en": "glTF accessor counts (indices / 3) per node instance"},
    "m.gltf_attr": {"es": "{attr} en {n}/{total} primitivas", "en": "{attr} in {n}/{total} primitives"},
    "m.gltf_materials": {"es": "materiales glTF", "en": "glTF materials"},
    "m.gltf_images": {"es": "imágenes glTF usadas por texturas", "en": "glTF images used by textures"},
    "m.gltf_sizes": {"es": "tamaño en píxeles decodificado de cada imagen (null = ilegible)", "en": "decoded pixel size of each image (null = unreadable)"},
    "m.gltf_animations": {"es": "animaciones glTF", "en": "glTF animations"},
    "m.gltf_skins": {"es": "skins glTF", "en": "glTF skins"},
    "m.gltf_nodes": {"es": "nodos glTF", "en": "glTF nodes"},
    "m.gltf_required": {"es": "extensionsRequired de glTF", "en": "glTF extensionsRequired"},
    "m.gltf_mismatch": {"es": "trimesh leyó {a} triángulos y los accessors glTF declaran {b}: revisa primitivas o extensiones no soportadas.",
                        "en": "trimesh read {a} triangles and the glTF accessors declare {b}: check unsupported primitives or extensions."},
    "m.3mf_not_read": {"es": "3MF puede llevar grupos de color y texturas; esta versión no los lee", "en": "3MF can carry colour groups and textures; this version does not read them"},
    "m.3mf_animations": {"es": "3MF no puede guardar animaciones", "en": "3MF cannot store animations"},
    "m.3mf_normals": {"es": "3MF no guarda normales", "en": "3MF stores no normals"},
    "m.3mf_items": {"es": "elementos / objetos de construcción leídos", "en": "build items / objects read"},
    "m.texture_max": {"es": "lado mayor de las imágenes de textura decodificadas", "en": "largest side of the decoded texture images"},
    "m.texture_sizes_unread": {"es": "tamaños de textura no leídos para este formato", "en": "texture sizes not read for this format"},
    "m.resource_bytes": {"es": "archivos externos referenciados (MTL, imágenes, buffers .bin)", "en": "external files referenced (MTL, images, .bin buffers)"},
    "m.total_bytes": {"es": "archivo más recursos externos", "en": "file plus external resources"},
    "m.no_such": {"es": "no existe esa medida", "en": "no such measurement"},
    "method": {"es": "Solo CPU: trimesh + numpy + lo que declara el propio archivo", "en": "CPU only: trimesh + numpy + the file's own declarations"},
    # --- record statuses inside reasons ---
    "st.not_available": {"es": "no disponible", "en": "not available"},
    "st.not_measured": {"es": "no medido", "en": "not measured"},
    "st.observed": {"es": "observado", "en": "observed"},
    # --- check reasons ---
    "r.status": {"es": "{key} está {status}: {source}", "en": "{key} is {status}: {source}"},
    "r.not_in_format": {"es": "no disponible en {fmt}: cuenta como ninguno", "en": "not available in {fmt}: counts as none"},
    "r.this_format": {"es": "este formato", "en": "this format"},
    "r.uv_unknown": {"es": "presencia de texturas o UV no medida en este formato", "en": "texture presence or UVs not measured for this format"},
    "r.unit_assumed": {"es": "el archivo no declara unidad; se supone {unit}", "en": "the file declares no unit; {unit} assumed"},
    "r.wall": {"es": "el grosor de pared no se mide en esta versión: usa la vista de paredes finas del laminador",
               "en": "wall thickness is not measured in this version: use the slicer's thin-wall preview"},
    # --- defects ---
    "d.max_triangles": {"es": "{obs} triángulos, presupuesto {lim}", "en": "{obs} triangles, budget {lim}"},
    "d.max_file_bytes": {"es": "{obs} bytes, límite {lim}", "en": "{obs} bytes, limit {lim}"},
    "d.max_textures": {"es": "{obs} texturas, límite {lim}", "en": "{obs} textures, limit {lim}"},
    "d.max_texture_px": {"es": "lado mayor de textura {obs} px, límite {lim} px", "en": "largest texture side {obs} px, limit {lim} px"},
    "d.require_uvs_if_textured": {"es": "con texturas pero sin coordenadas UV", "en": "textured but without UV coordinates"},
    "d.max_bodies": {"es": "{obs} cuerpos separados, límite {lim}", "en": "{obs} separate bodies, limit {lim}"},
    "d.require_watertight": {"es": "no está cerrada: {obs}", "en": "not closed: {obs}"},
    "d.max_extent_mm": {"es": "caja envolvente {obs} mm no cabe en {lim} mm", "en": "bounding box {obs} mm does not fit {lim} mm"},
    "d.units": {"es": "unidad {obs}, el perfil espera {lim}", "en": "unit {obs}, profile expects {lim}"},
    "d.accepted_formats": {"es": "formato {obs} no está entre {lim}", "en": "format {obs} not in {lim}"},
    "d.other": {"es": "{rule}: {obs} frente a {lim}", "en": "{rule}: {obs} vs {lim}"},
    "k.open_edges": {"es": "aristas abiertas", "en": "open edges"},
    "k.nonmanifold_edges": {"es": "aristas no manifold", "en": "non-manifold edges"},
    # --- losses ---
    "l.attributes.uvs": {"es": "coordenadas UV", "en": "UV coordinates"},
    "l.attributes.vertex_colors": {"es": "colores de vértice", "en": "vertex colours"},
    "l.attributes.materials": {"es": "materiales", "en": "materials"},
    "l.attributes.textures": {"es": "texturas", "en": "textures"},
    "l.attributes.animations": {"es": "animaciones", "en": "animations"},
    "l.attributes.scene_nodes": {"es": "estructura y nombres de los nodos de la escena", "en": "scene node structure and names"},
    "l.attributes.normals": {"es": "normales del autor", "en": "authored normals"},
    "l.texture_resolution": {"es": "resolución de las texturas", "en": "texture resolution"},
    "ln.recomputed": {"es": "se recalculan a partir de la nueva geometría", "en": "recomputed from the new geometry"},
    "ln.merged": {"es": "todas las piezas se funden en una sola malla", "en": "all parts are merged into one mesh"},
    "ln.unmeasured": {"es": "no medido en el origen; lo que tenga se perdería", "en": "not measured in the source; any it has would be dropped"},
    # --- plan ---
    "why.bytes_estimate": {"es": "max_file_bytes (estimación)", "en": "max_file_bytes (estimate)"},
    "why.gltf_metres": {"es": "glTF guarda metros", "en": "glTF stores metres"},
    "why.profile_units": {"es": "unidades del perfil", "en": "profile units"},
    "pres.geometry": {"es": "solo geometría", "en": "geometry only"},
    "pres.visuals": {"es": "geometría y lo visual que admite el escritor", "en": "geometry and visuals the writer supports"},
    "rec.simplify": {"es": "Simplificar de {a} a como mucho {b} triángulos con el motor LOD; el resultado se vuelve a medir.",
                     "en": "Simplify from {a} to at most {b} triangles with the LOD engine; the result is re-measured."},
    "rec.textures_dropped": {"es": "La simplificación anterior elimina las texturas; no hay nada que reducir.",
                             "en": "Textures are dropped by the simplification above; nothing to resize."},
    "rec.resize": {"es": "Reducir las imágenes de textura a {px} px por lado como máximo (Lanczos).",
                   "en": "Downscale texture images to at most {px} px per side (Lanczos)."},
    "blk.format": {"es": "El perfil escribe {fmt}, que él mismo no acepta: edita el perfil.",
                   "en": "The profile writes {fmt}, which it does not accept itself: edit the profile."},
    "rec.convert": {"es": "Exportar como {fmt}.", "en": "Export as {fmt}."},
    "rec.drop_textures": {"es": "La simplificación elimina todas las texturas.", "en": "The simplification drops every texture."},
    "blk.atlas": {"es": "Unir texturas en un atlas no está implementado: redúcelas en la herramienta de autoría.",
                  "en": "Merging textures into an atlas is not implemented: reduce them in the authoring tool."},
    "rec.atlas": {"es": "Hornea los materiales en menos texturas en la herramienta de autoría.", "en": "Bake the materials into fewer textures in the authoring tool."},
    "rec.uv_dropped": {"es": "La simplificación elimina las texturas, así que no hacen falta UV.",
                       "en": "The simplification drops the textures, so no UVs are needed."},
    "blk.uv": {"es": "Con texturas y sin UV: desplegar UV no está disponible en esta versión.",
               "en": "Textured without UVs: UV unwrapping is not available in this version."},
    "rec.unwrap": {"es": "Despliega las UV en la herramienta de autoría o quita las texturas.", "en": "Unwrap UVs in the authoring tool or drop the textures."},
    "blk.bodies": {"es": "Los cuerpos separados no se unen automáticamente (una unión booleana cambia el diseño).",
                   "en": "Separate bodies are not merged automatically (a boolean union changes the design)."},
    "rec.bodies": {"es": "Une las piezas en CAD o exporta cada cuerpo como archivo propio.", "en": "Union the parts in CAD or export each body as its own file."},
    "blk.watertight": {"es": "Las aristas abiertas o no manifold no se reparan aquí.", "en": "Open or non-manifold edges are not repaired here."},
    "rec.repair": {"es": "Cierra los agujeros en CAD, o usa el módulo de reparación de Daedalus (daedalus_model_repair) o la reparación del laminador, y vuelve a inspeccionar.",
                   "en": "Close the holes in CAD, or use the Daedalus repair module (daedalus_model_repair) or the slicer's repair, then inspect again."},
    "blk.extent": {"es": "Escalar o dividir cambia el diseño; no se hace automáticamente.", "en": "Scaling or splitting changes the design; not done automatically."},
    "rec.extent": {"es": "Reduce la escala del modelo o divídelo en piezas que quepan en el volumen de impresión.",
                   "en": "Scale the model down or split it into parts that fit the build volume."},
    "rec.rescale": {"es": "Reescalar de {a} a {b} al exportar.", "en": "Rescale from {a} to {b} on export."},
    "rec.verify_units": {"es": "El archivo no declara unidad; se supone {unit} (pista de tamaño: {hint}). Confírmalo antes de preparar.",
                         "en": "The file declares no unit; {unit} is assumed (size hint: {hint}). Confirm before preparing."},
    "rec.preserve": {"es": "Aún no hay una simplificación que conserve atributos (costuras UV); la variante quedaría sin texturas.",
                     "en": "An attribute-preserving simplification (UV seams kept) is not available yet; the variant would be untextured."},
    "rec.manual": {"es": "{rule} sin evaluar: {reason}.", "en": "{rule} not evaluated: {reason}."},
    # --- report ---
    "v.meets": {"es": "cumple todas las reglas medidas", "en": "meets every measured rule"},
    "v.not_eval": {"es": "; {n} regla(s) sin evaluar", "en": "; {n} rule(s) not evaluated"},
    "v.defects": {"es": "{n} defecto(s)", "en": "{n} defect(s)"},
    "next": {"es": "Este informe es un borrador y no cambia nada. asset_prepare ejecuta el plan en un archivo nuevo y lo verifica.",
             "en": "This report is a draft and changes nothing. asset_prepare runs the plan as a new file and verifies it."},
    # --- preparation ---
    "dev.note": {"es": "Estimación por muestreo: distancias desde un conjunto finito de muestras de superficie y vértices de ambas superficies. "
                       "Puede no ver un detalle estrecho entre muestras, así que no es una cota superior garantizada de la desviación.",
                 "en": "Sampled estimate: distances from a finite set of surface samples and vertices on both surfaces. It can miss a narrow "
                       "feature between samples, so it is not a guaranteed upper bound of the deviation."},
    "p.unchanged": {"es": "El archivo ya cumple todas las reglas medidas del perfil; no se ha escrito ninguna variante.",
                    "en": "The file already meets every measured rule of the profile; no variant was written."},
    "p.unchanged_ne": {"es": " {n} regla(s) no se han podido evaluar.", "en": " {n} rule(s) could not be evaluated."},
    "p.blocked": {"es": "No preparada: {reasons}", "en": "Not prepared: {reasons}"},
    "p.rejected": {"es": "No preparada: el plan perdería {labels}. Vuelve a llamar con accept_losses=true para aceptarlo, o edita el perfil para que no haga falta una operación con pérdidas.",
                   "en": "Not prepared: the plan would lose {labels}. Call again with accept_losses=true to accept that, or edit the profile so no lossy operation is needed."},
    "p.loader": {"es": "trimesh.load nuevo del archivo escrito", "en": "fresh trimesh.load of the written file"},
    "p.loader_gltf": {"es": " + recuentos de los accessors glTF", "en": " + glTF accessor counts"},
    "p.over_tolerance": {"es": "desviación muestreada {dev} mm por encima de la tolerancia {tol} mm", "en": "sampled deviation {dev} mm over the tolerance {tol} mm"},
    "p.counts_disagree": {"es": "el recuento de triángulos reimportado no coincide con los accessors del propio archivo",
                          "en": "the re-imported triangle count disagrees with the file's own accessors"},
    "p.discarded": {"es": "La variante se escribió, se volvió a medir y se descartó porque no cumple el perfil: {problems}.",
                    "en": "The variant was written, re-measured and discarded because it does not meet the profile: {problems}."},
    "p.variant_note": {"es": "Preparada para «{profile}» a partir del modelo #{id} (recibo {receipt}). El original no se toca.",
                       "en": "Prepared for '{profile}' from model #{id} (receipt {receipt}). The original is untouched."},
    "p.prepared": {"es": "Variante escrita y verificada al reimportarla: {n} regla(s) cumplen", "en": "Variant written and verified on re-import: {n} rule(s) pass"},
    "p.prepared_ne": {"es": ", {n} sin evaluar", "en": ", {n} not evaluated"},
    "p.prepared_dev": {"es": "; desviación muestreada máx. {dev} mm", "en": "; sampled deviation max {dev} mm"},
    "p.prepared_lost": {"es": "; perdido: {lost}", "en": "; lost: {lost}"},
    "p.original_changed": {"es": "El archivo original cambió durante la preparación; no se da nada por bueno.",
                           "en": "The original file changed during the preparation; nothing is trusted."},
    # --- errors ---
    "e.format": {"es": "Formato «{ext}» no admitido: usa {formats}.", "en": "Unsupported format '{ext}': use {formats}."},
    "e.not_glb": {"es": "No es un glTF binario (falta la cabecera glTF).", "en": "Not a binary glTF file (missing glTF header)."},
    "e.no_json": {"es": "El GLB no tiene bloque JSON.", "en": "The GLB has no JSON chunk."},
    "e.no_triangles": {"es": "No hay triángulos en el archivo.", "en": "No triangles found in the file."},
    "e.file_missing": {"es": "El archivo no existe: {path}", "en": "The file does not exist: {path}"},
    "e.too_big": {"es": "El archivo ocupa {mb} MB, por encima del límite de análisis de {limit} MB.",
                  "en": "The file is {mb} MB, over the {limit} MB analysis limit."},
    "e.model_missing": {"es": "El modelo {id} no existe.", "en": "Model {id} does not exist."},
    "e.model_file_missing": {"es": "El archivo del modelo {id} ya no está en el disco: {path}", "en": "The file of model {id} is no longer on disk: {path}"},
    "e.no_source": {"es": "Indica un id de modelo o la ruta absoluta de un archivo.", "en": "Give a model id or the absolute path of a file."},
    "e.relative": {"es": "La ruta debe ser absoluta.", "en": "The path must be absolute."},
    "e.prep_unknown": {"es": "Id de preparación desconocido.", "en": "Unknown preparation id."},
    "e.prep_missing": {"es": "La preparación {id} no existe.", "en": "Preparation {id} does not exist."},
    # --- profiles ---
    "t.web.name": {"es": "Configurador web", "en": "Web configurator"},
    "t.web.description": {"es": "Visor en tiempo real en navegador de escritorio y móvil: un GLB en metros, geometría y texturas ligeras.",
                          "en": "Real-time viewer on desktop and mobile browsers: one GLB in metres, light geometry and textures."},
    "t.fdm.name": {"es": "Impresión FDM", "en": "FDM print"},
    "t.fdm.description": {"es": "Impresora de filamento de sobremesa: sólido cerrado y manifold en milímetros que cabe en 256 mm.",
                          "en": "Desktop filament printer: closed manifold solid in millimetres that fits a 256 mm build volume."},
    "e.glb_metres": {"es": "Un perfil que escribe GLB debe usar metros: glTF guarda las posiciones en metros por especificación.",
                     "en": "A profile that writes GLB must use metres: glTF stores positions in metres by specification."},
    "e.formats": {"es": "Formato(s) desconocido(s) {bad}; usa {formats}.", "en": "Unknown format(s) {bad}; use {formats}."},
    "e.profile_missing": {"es": "El perfil de destino «{id}» no existe. Conocidos: {known}.", "en": "Target profile '{id}' does not exist. Known: {known}."},
    "e.clone_id": {"es": "Un clon necesita un id nuevo.", "en": "A clone needs a new id."},
    "e.profile_exists": {"es": "El perfil «{id}» ya existe; edítalo o elige otro id.", "en": "Profile '{id}' already exists; edit it or choose another id."},
    "e.profile_unknown": {"es": "El perfil de destino «{id}» no existe.", "en": "Target profile '{id}' does not exist."},
    "n.reset_nothing": {"es": "Perfil de serie sin cambios: no hay nada que restaurar.", "en": "Built-in profile without edits: nothing to reset."},
    "e.id_mismatch": {"es": "El id del cuerpo debe coincidir con el de la URL.", "en": "The id in the body must match the URL."},
    "e.no_output": {"es": "Esta preparación no tiene archivo de salida.", "en": "This preparation has no output file."},
}


def norm(lang: str | None, default: str = "en") -> str:
    text = (lang or "").strip().lower()[:2]
    return text if text in LANGS else default


def current() -> str:
    return _current.get()


@contextlib.contextmanager
def language(lang: str | None):
    """Use `lang` for every text produced inside the block (None keeps the current one)."""
    token = _current.set(norm(lang, _current.get())) if lang else None
    try:
        yield _current.get()
    finally:
        if token is not None:
            _current.reset(token)


def _(key: str, **values) -> str:
    entry = TEXTS[key]
    return entry[_current.get()].format(**values)


def defaults(key: str) -> set[str]:
    """Both language versions of a text (to recognise an untouched built-in name or description)."""
    return set(TEXTS[key].values())


def show(value) -> str:
    """Human rendering of a list/dict/bool inside a sentence (no Python reprs such as ['glb'])."""
    if isinstance(value, bool):
        return {"es": "sí" if value else "no", "en": "yes" if value else "no"}[_current.get()]
    if isinstance(value, (list, tuple)):
        return " × ".join(show(v) for v in value) if value and all(isinstance(v, (int, float)) for v in value) else ", ".join(show(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{_('k.' + k) if 'k.' + k in TEXTS else k} {show(v)}" for k, v in value.items())
    return str(value)
