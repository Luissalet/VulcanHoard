import React, { Suspense, lazy, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import AnimationPanel from "../components/AnimationPanel.jsx";

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
    const r = await act(() => api.animExample(), "Ejemplo inventado escrito en la carpeta de datos.");
    if (r) open(r.path);
  };
  const glb = /\.(glb|fbx)$/i.test(path); // an FBX previews through its Blender conversion
  const shown = preview || (glb ? api.animSourceUrl(path) : null);
  const pseudo = { id: `anim:${path}:${preview || ""}`, format: "glb", sha256: "" };
  return (
    <div className="mx-auto max-w-[1200px]" data-page-animar>
      <h1 className="text-[22px] font-semibold">Animaciones</h1>
      <p className="help mt-1">Personajes y objetos con esqueleto, clips y morph targets (GLB, glTF o FBX; un FBX se convierte con Blender en segundo plano y la conversión se guarda). Mira cada clip en el visor con su barra de tiempo, comprueba el esqueleto, los bucles y la raíz frente a un perfil de destino, prepara una variante verificada, expórtala a FBX o pasa sus animaciones a otro esqueleto. El archivo original no se toca.</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input className="field" style={{ flex: "1 1 420px" }} placeholder="Ruta absoluta del GLB, glTF o FBX" value={text} onChange={(e) => setText(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && open(text)} aria-label="Ruta del archivo animado" data-anim-path />
        <button type="button" className="btn btn-sm btn-primary" disabled={!text.trim()} onClick={() => open(text)} data-anim-open>Abrir</button>
        <button type="button" className="btn btn-sm" onClick={example} data-anim-example>Usar un ejemplo inventado</button>
      </div>
      {path && (
        <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]">
          <div className="min-w-0">
            {shown ? (
              <Suspense fallback={<div className="viewer"><div className="hud">Cargando visor…</div></div>}>
                <Viewer model={pseudo} glbUrl={shown} onCloseGlb={() => setPreview(null)} glbNote={preview ? "Variante preparada (GLB en metros, mostrado en mm)" : "Archivo original (GLB en metros, mostrado en mm)"} />
              </Suspense>
            ) : <p className="help">La vista previa lee archivos .glb y .fbx; un .gltf se puede inspeccionar y preparar, y la variante preparada (GLB) sí se ve aquí.</p>}
            {preview && <p className="help mt-1 text-[12px]" data-anim-showing-prepared>Viendo la variante preparada · <button type="button" className="btn-link" onClick={() => setPreview(null)}>volver al original</button></p>}
          </div>
          <div className="min-w-0">
            <AnimationPanel key={path} path={path} onPreview={setPreview} />
          </div>
        </div>
      )}
    </div>
  );
}
