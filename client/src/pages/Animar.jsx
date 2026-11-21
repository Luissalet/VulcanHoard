import React, { Suspense, lazy, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import AnimationPanel from "../components/AnimationPanel.jsx";
import { tr } from "../components/animationI18n.js";

const Viewer = lazy(() => import("../components/Viewer.jsx"));
const KEY = "vulcan.animar.path";

function remembered() {
  try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; }
}

/** Animated assets (rigged GLB/glTF): open a file, preview its clips, inspect against an animated profile and prepare a variant. */
export default function Animar() {
  const { act } = useApp();
  const [text, setText] = useState(remembered);
  const [path, setPath] = useState(remembered);
  const [preview, setPreview] = useState(null);
  const open = (value) => {
    const clean = value.trim().replace(/^"|"$/g, "");
    setText(clean); setPath(clean); setPreview(null);
    try { localStorage.setItem(KEY, clean); } catch (e) { /* private window: the path is simply not remembered */ }
  };
  const example = async () => {
    const r = await act(() => api.animExample(), tr("Ejemplo inventado escrito en la carpeta de datos.", "Example saved in the data folder."));
    if (r) open(r.path);
  };
  const glb = /\.(glb|fbx)$/i.test(path); // an FBX previews through its Blender conversion
  const shown = preview || (glb ? api.animSourceUrl(path) : null);
  const pseudo = { id: `anim:${path}:${preview || ""}`, format: "glb", sha256: "" };
  return (
    <div className="mx-auto max-w-[1200px]" data-page-animar>
      <h1 className="text-[22px] font-semibold">{tr("Animaciones", "Animations")}</h1>
      <p className="help mt-1">{tr(
        "Personajes y objetos con esqueleto, clips y morph targets (GLB, glTF o FBX; un FBX se convierte con Blender en segundo plano y la conversión se guarda). Mira cada clip en el visor con su barra de tiempo, comprueba el esqueleto, los bucles y la raíz frente a un perfil de destino, prepara una variante verificada, expórtala a FBX o pasa sus animaciones a otro esqueleto. El archivo original no se toca.",
        "Open characters and objects with skeletons, clips and morph targets in GLB, glTF or FBX. Blender converts and caches FBX files in the background. Use the viewer timeline to play each clip, check the skeleton, loops and root motion against a delivery profile, prepare a verified variant, export to FBX or retarget the animations to another skeleton. The original file is preserved."
      )}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input className="field" style={{ flex: "1 1 420px" }} placeholder={tr("Ruta absoluta del GLB, glTF o FBX", "Absolute path to the GLB, glTF or FBX")} value={text} onChange={(e) => setText(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && open(text)} aria-label={tr("Ruta del archivo animado", "Animated file path")} data-anim-path />
        <button type="button" className="btn btn-sm btn-primary" disabled={!text.trim()} onClick={() => open(text)} data-anim-open>{tr("Abrir", "Open")}</button>
        <button type="button" className="btn btn-sm" onClick={example} data-anim-example>{tr("Usar un ejemplo inventado", "Use an example")}</button>
      </div>
      {path && (
        <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]">
          <div className="min-w-0">
            {shown ? (
              <Suspense fallback={<div className="viewer"><div className="hud">{tr("Cargando visor…", "Loading viewer…")}</div></div>}>
                <Viewer model={pseudo} glbUrl={shown} onCloseGlb={() => setPreview(null)} glbNote={preview
                  ? tr("Variante preparada (GLB en metros, mostrado en mm)", "Prepared variant (GLB in metres, shown in mm)")
                  : tr("Archivo original (GLB en metros, mostrado en mm)", "Original file (GLB in metres, shown in mm)")} />
              </Suspense>
            ) : <p className="help">{tr("La vista previa lee archivos .glb y .fbx; un .gltf se puede inspeccionar y preparar, y la variante preparada (GLB) sí se ve aquí.",
              "Preview supports .glb and .fbx files. You can inspect and prepare a .gltf file, then preview its prepared GLB.")}</p>}
            {preview && <p className="help mt-1 text-[12px]" data-anim-showing-prepared>{tr("Viendo la variante preparada", "Viewing prepared variant")} · <button type="button" className="btn-link" onClick={() => setPreview(null)}>{tr("volver al original", "back to the original")}</button></p>}
          </div>
          <div className="min-w-0">
            <AnimationPanel key={path} path={path} onPreview={setPreview} />
          </div>
        </div>
      )}
    </div>
  );
}
