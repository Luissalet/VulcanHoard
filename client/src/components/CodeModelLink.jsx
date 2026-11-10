import React, { useEffect, useState } from "react";
import { api } from "../api.js";

/** On the model page: when the model was written as a sand3d program, a short note with a link to edit its code. */
export default function CodeModelLink({ model }) {
  const [info, setInfo] = useState(null);
  useEffect(() => {
    let alive = true;
    setInfo(null);
    if (!(model.tags || []).some((tag) => (tag?.name || tag) === "codigo")) return undefined; // only parts made with code carry the tag
    api.codeInfo(model.id).then((r) => { if (alive && r?.ok) setInfo(r); }).catch(() => {});
    return () => { alive = false; };
  }, [model.id, model.sha256]);
  if (!info) return null;
  return (
    <section className="panel-white" aria-labelledby="code-model-link">
      <h2 id="code-model-link" className="text-[16px] font-semibold">Modelado con código</h2>
      <p className="help mt-1">Esta pieza es un programa sand3d ({info.code.split("\n").length} líneas, {info.history.length} {info.history.length === 1 ? "versión" : "versiones"}). Puedes cambiar el código, pedir un cambio con palabras o regenerarla; el modelo conserva su id.</p>
      <a className="btn btn-sm mt-3" href={`#/codigo/${model.id}`}>Editar el código</a>
    </section>
  );
}
