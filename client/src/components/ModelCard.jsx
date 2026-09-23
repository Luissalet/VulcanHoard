import React from "react";
import { api } from "../api.js";
import { FormatChip } from "./ui.jsx";
import { dims, num } from "../format.js";

export function Thumb({ model, className = "" }) {
  const url = api.thumbUrl(model);
  return (
    <div className={`thumb ${className}`}>
      {url ? <img src={url} alt="" loading="lazy" decoding="async" /> : <span className="ph">{model.status === "ok" ? "sin miniatura" : model.status === "skipped" ? "demasiado grande" : "error"}</span>}
    </div>
  );
}

export default function ModelCard({ model }) {
  const warn = model.status !== "ok" || model.units_guess !== "mm" || model.watertight === false;
  return (
    <a className="card" href={`#/modelo/${model.id}`} title={model.rel_path}>
      <Thumb model={model} />
      <div className="px-3 py-2.5">
        <div className="truncate text-[13px] font-semibold" title={model.name}>{model.name}</div>
        <div className="help num truncate text-[12px]">{dims(model)}</div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <FormatChip format={model.format} />
          {model.triangles != null && <span className="chip num">{num(model.triangles)} tri</span>}
          {model.has_listing && <span className="chip chip-ok">ficha</span>}
          {model.dupe_of && <span className="chip chip-warn">duplicado</span>}
          {warn && model.watertight === false && <span className="chip chip-warn">abierto</span>}
          {model.units_guess !== "mm" && <span className="chip chip-warn">unidades</span>}
          {model.status === "error" && <span className="chip chip-danger">error</span>}
        </div>
      </div>
    </a>
  );
}
