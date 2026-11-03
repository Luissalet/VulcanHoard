import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";

let cached = null; // one /api/slicers per page load; Ajustes refreshes it after saving
export function forgetSlicers() {
  cached = null;
}

/** "Abrir en laminador": launches the configured or detected slicer with these models (ids) or a whole folder. */
export default function SlicerButton({ modelIds, rootId, path, label = "Abrir en laminador" }) {
  const { act } = useApp();
  const [info, setInfo] = useState(null);
  const [slicer, setSlicer] = useState("");
  useEffect(() => {
    let live = true;
    (cached || (cached = api.slicers())).then((r) => {
      if (!live) return;
      setInfo(r);
      setSlicer(r.default || "");
    }).catch(() => live && setInfo({ slicers: [], default: null }));
    return () => { live = false; };
  }, []);
  if (!info) return null;
  const available = info.slicers.filter((s) => s.available);
  if (!available.length) {
    return <a className="btn btn-sm" href="#/ajustes" title="No se ha encontrado PrusaSlicer, Bambu Studio, OrcaSlicer ni Cura: indica la ruta en Ajustes">Configurar laminador</a>;
  }
  const open = () => act(() => api.openInSlicer({ model_ids: modelIds, root_id: rootId, path, slicer }),
    `Abriendo en ${available.find((s) => s.id === slicer)?.name || "el laminador"}…`);
  return (
    <span className="inline-flex items-center gap-1" data-slicer-button>
      <button type="button" className="btn btn-sm" onClick={open}>{label}</button>
      {available.length > 1 && (
        <select className="field" style={{ width: "auto", padding: "2px 6px", fontSize: 12 }} aria-label="Laminador" value={slicer} onChange={(e) => setSlicer(e.target.value)}>
          {available.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      )}
    </span>
  );
}
