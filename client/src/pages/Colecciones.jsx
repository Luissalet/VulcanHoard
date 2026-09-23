import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import ModelCard from "../components/ModelCard.jsx";
import { Empty, PageHeader } from "../components/ui.jsx";
import { num } from "../format.js";

function AlbumPanel({ album, onChange }) {
  const { act } = useApp();
  const [models, setModels] = useState(null);
  const [open, setOpen] = useState(false);
  const [addId, setAddId] = useState("");

  useEffect(() => {
    if (!open) return;
    api.models({ album: album.id, limit: 200 }).then((r) => setModels(r.models)).catch(() => {});
  }, [open, album.count, album.id]);

  const rename = (e) => {
    const name = e.target.value.trim();
    if (name && name !== album.name) act(() => api.updateAlbum(album.id, { name }), "Álbum renombrado.").then(onChange);
  };
  const add = (e) => {
    e.preventDefault();
    const id = Number(addId);
    if (!id) return;
    act(() => api.updateAlbum(album.id, { add: [id] }), "Modelo añadido al álbum.").then(() => { setAddId(""); onChange(); });
  };
  const remove = (id) => act(() => api.updateAlbum(album.id, { remove: [id] }), "Modelo quitado del álbum.").then(onChange);
  const destroy = () => {
    if (!window.confirm(`¿Eliminar el álbum «${album.name}»? Los modelos no se tocan.`)) return;
    act(() => api.removeAlbum(album.id), "Álbum eliminado.").then(onChange);
  };

  return (
    <section className="panel-white">
      <div className="flex flex-wrap items-center gap-3">
        <input className="field w-auto min-w-[200px] flex-1 font-semibold" defaultValue={album.name} onBlur={rename} aria-label="Nombre del álbum" />
        <span className="help num">{num(album.count)} modelos</span>
        <a className="btn btn-sm" href={`#/galeria?album=${album.id}`}>Ver en la galería</a>
        <button type="button" className="btn btn-sm" onClick={() => setOpen((v) => !v)}>{open ? "Ocultar" : "Mostrar"}</button>
        <button type="button" className="btn btn-sm btn-danger" onClick={destroy}>Eliminar</button>
      </div>
      {open && (
        <div className="mt-3">
          <form className="mb-3 flex gap-2" onSubmit={add}>
            <input className="field w-40" placeholder="Id del modelo" value={addId} onChange={(e) => setAddId(e.target.value)} inputMode="numeric" />
            <button type="submit" className="btn btn-sm">Añadir por id</button>
            <span className="help self-center">El id aparece en la página de cada modelo.</span>
          </form>
          {models && models.length === 0 && <p className="help">Álbum vacío.</p>}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {(models || []).map((m) => (
              <div key={m.id} className="relative">
                <ModelCard model={m} />
                <button type="button" className="btn btn-sm absolute right-2 top-2" onClick={() => remove(m.id)} aria-label={`Quitar ${m.name} del álbum`}>×</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export default function Colecciones() {
  const { status, act } = useApp();
  const [data, setData] = useState(null);
  const [name, setName] = useState("");
  const load = () => api.collections().then(setData).catch(() => {});
  useEffect(() => {
    load();
  }, [status?.counts?.models]);

  const create = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    const created = await act(() => api.createAlbum({ name: name.trim(), model_ids: [] }), "Álbum creado.");
    if (created) {
      setName("");
      load();
    }
  };

  return (
    <div>
      <PageHeader title="Colecciones" description="Las carpetas agrupan los modelos automáticamente; los álbumes son selecciones manuales (una campaña, un cliente, una serie)." />
      <section className="mb-6">
        <h2 className="mb-2 text-[16px] font-semibold">Álbumes</h2>
        <form className="panel mb-3 flex flex-wrap gap-2" onSubmit={create}>
          <input className="field flex-1" placeholder="Nombre del álbum nuevo" value={name} onChange={(e) => setName(e.target.value)} aria-label="Nombre del álbum" />
          <button type="submit" className="btn btn-primary">Crear álbum</button>
        </form>
        {data && data.albums.length === 0 && <Empty title="Sin álbumes">Crea uno y añade modelos por su id desde aquí o desde el asistente.</Empty>}
        <div className="space-y-3">{(data?.albums || []).map((a) => <AlbumPanel key={a.id} album={a} onChange={load} />)}</div>
      </section>
      <section>
        <h2 className="mb-2 text-[16px] font-semibold">Carpetas</h2>
        {data && data.folders.length === 0 && <p className="help">Todavía no hay modelos escaneados.</p>}
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {(data?.folders || []).map((f) => (
            <a key={f.name} className="card flex items-center justify-between px-4 py-3" href={`#/galeria?collection=${encodeURIComponent(f.name)}`}>
              <span className="truncate font-semibold">{f.name}</span>
              <span className="help num shrink-0">{num(f.count)}</span>
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}
