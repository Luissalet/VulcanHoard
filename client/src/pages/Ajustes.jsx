import React from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { PageHeader } from "../components/ui.jsx";
import { bytes, when } from "../format.js";

function Row({ name, value, help }) {
  return (
    <div className="row">
      <div className="min-w-0 flex-1">
        <div className="font-mono text-[12px]">{name}</div>
        {help && <div className="help">{help}</div>}
      </div>
      <div className="text-[13px]">{value}</div>
    </div>
  );
}

export default function Ajustes() {
  const { status, act } = useApp();
  if (!status) return <p className="help">Cargando…</p>;
  const run = (action, message) => act(() => api.maintenance(action), message);

  return (
    <div>
      <PageHeader title="Ajustes" description="La configuración se lee de variables de entorno al arrancar; aquí ves los valores en uso y puedes lanzar tareas de mantenimiento." />

      <section className="panel-white">
        <h2 className="text-[16px] font-semibold">Mantenimiento</h2>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className="btn" onClick={() => run("rescan-all", "Todas las carpetas en cola.")}>Reescanear todas las carpetas</button>
          <button type="button" className="btn" onClick={() => run("rebuild-fts", "Índice de búsqueda reconstruido.")}>Reconstruir el índice de búsqueda</button>
          <button type="button" className="btn" onClick={() => run("refresh-dupes", "Duplicados recalculados.")}>Recalcular duplicados</button>
        </div>
        <p className="help mt-2">Reescanear no vuelve a leer los archivos que no han cambiado; si borras la carpeta de miniaturas, se regeneran en el siguiente escaneo.</p>
      </section>

      <section className="panel-white mt-4">
        <h2 className="text-[16px] font-semibold">Valores en uso</h2>
        <div className="mt-2">
          <Row name="VULCAN_DATA_DIR" value={<code>{status.data_dir}</code>} help="Base de datos, token del asistente y miniaturas." />
          <Row name="VULCAN_THUMBS" value={status.thumbnails ? "1 (se generan)" : "0 (desactivadas)"} help={`Miniaturas en ${status.thumbs_dir} · ${bytes(status.thumbs_bytes)}`} />
          <Row name="VULCAN_MAX_FILE_MB" value={`${status.max_file_mb} MB`} help="Los archivos más grandes se listan pero no se analizan." />
          <Row name="VULCAN_WATCH" value={status.watching.length ? `vigilando ${status.watching.length} carpeta(s)` : "sin carpetas vigiladas"} help={status.watch_error ? `Aviso: ${status.watch_error}` : "Se activa por carpeta en Carpetas."} />
          <Row name="VULCAN_ALLOWED_HOSTS" value="ver README" help="Nombres de host adicionales para entrar desde el móvil a través de un túnel." />
        </div>
      </section>

      <section className="panel-white mt-4 text-[13px]">
        <h2 className="text-[16px] font-semibold">Asistente (MCP)</h2>
        <p className="help mt-2">
          Mientras esta aplicación está en marcha, el asistente se conecta con <code>python mcp_server.py</code>, que usa el token de <code>{status.data_dir}/mcp-token</code> y reenvía cada llamada a la API local. Nunca abre la base de datos. Faustus detecta la aplicación por <code>/api/health</code>.
        </p>
        <p className="help mt-2">Lo que puede hacer: buscar modelos, leer medidas y triángulos, escribir la ficha para la tienda, etiquetar, anotar, listar duplicados, añadir carpetas y reescanear. Nunca inventa características que no estén en los datos.</p>
      </section>

      <section className="panel-white mt-4 text-[13px]">
        <h2 className="text-[16px] font-semibold">Estado</h2>
        <p className="help mt-2">Versión {status.version} · en marcha desde {when(status.started_at)} · índice {bytes(status.db_bytes)} · libres {bytes(status.disk_free_bytes)}</p>
        {status.worker.last_error && <p className="mt-2" style={{ color: "var(--danger-ink)" }}>Último error del escáner: {status.worker.last_error}</p>}
      </section>
    </div>
  );
}
