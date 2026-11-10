import React, { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { api } from "../api.js";
import { PageHeader } from "../components/ui.jsx";
import { WEB_GLB_TO_MM, dispose, load, prepare } from "../components/Viewer.jsx";
import { ImpostorField } from "../impostor.js";

const SPACING = 100; // mm between models on the table; every model is scaled to fit its square
const FILL = 0.42; // bounding-sphere radius as a share of the spacing
const COUNTS = [50, 100, 200, 500, 1000, 2000];
const SCENE_FORMATS = ["glb", "gltf", "fbx"];

const initial = (query) => ({
  count: Number(query?.get("n")) || 100,
  q: query?.get("q") || "",
  collection: query?.get("collection") || "",
  maxTriangles: Number(query?.get("maxtri")) || 2_000_000,
  impostors: query?.get("impostors") !== "0",
  near: Number(query?.get("near")) || 260,
  maxMeshes: Number(query?.get("meshes")) || 16,
  blend: query?.get("blend") !== "0",
});

function modelUrl(model) {
  const v = encodeURIComponent(model.sha256 || "");
  return SCENE_FORMATS.includes(model.format) ? ["glb", `/api/models/${model.id}/glb?v=${v}`] : [model.format, `/api/models/${model.id}/file?v=${v}`];
}

/** Mesa 3D: many library models on one table. Far (or numerous) models are drawn as octahedral impostors, two triangles each;
 * the ones near the camera swap to their real mesh. Without impostors every model is a real mesh (to compare the frame rate). */
export default function Mesa({ query }) {
  const host = useRef(null);
  const live = useRef({});
  const [settings, setSettings] = useState(() => initial(query));
  const [draft, setDraft] = useState(() => initial(query));
  const [data, setData] = useState(null);
  const [stats, setStats] = useState(null);
  const [batch, setBatch] = useState(null);
  const [spin, setSpin] = useState(query?.get("spin") === "1");
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0);
  const batchTimer = useRef(null);

  useEffect(() => {
    setError(null);
    api.impostorScene({ limit: settings.count, q: settings.q || undefined, collection: settings.collection || undefined, max_triangles: settings.maxTriangles })
      .then(setData).catch((failure) => setError(failure.message));
  }, [settings.count, settings.q, settings.collection, settings.maxTriangles, version]);
  useEffect(() => () => clearTimeout(batchTimer.current), []);
  useEffect(() => { if (live.current.controls) live.current.controls.autoRotate = spin; }, [spin]);
  useEffect(() => { live.current.near = settings.near; live.current.maxMeshes = settings.maxMeshes; live.current.field?.setBlend(settings.blend); },
    [settings.near, settings.maxMeshes, settings.blend]);

  useEffect(() => {
    if (!host.current || !data) return undefined;
    const container = host.current;
    const useImpostors = settings.impostors;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xfbf9f6);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a7f74, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(-1, -1.5, 2.5);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffe9d6, 0.5);
    fill.position.set(2, 1, 0.5);
    scene.add(fill);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    container.appendChild(renderer.domElement);
    const camera = new THREE.PerspectiveCamera(45, 16 / 9, 1, 1e6);
    camera.up.set(0, 0, 1);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI * 0.49; // stay above the table: the impostors only have views from above the horizon
    controls.autoRotate = spin;
    controls.autoRotateSpeed = 1.5;

    const models = data.models;
    const cols = Math.max(1, Math.ceil(Math.sqrt(models.length)));
    const rows = Math.max(1, Math.ceil(models.length / cols));
    const table = new THREE.GridHelper(Math.max(cols, rows) * SPACING, Math.max(cols, rows), 0xd9cfc7, 0xe9e2dc);
    table.rotation.x = Math.PI / 2;
    scene.add(table);
    const field = useImpostors ? new ImpostorField(scene, { grid: data.grid }) : null;
    const items = models.map((model, i) => {
      const slot = new THREE.Vector3(((i % cols) - (cols - 1) / 2) * SPACING, (Math.floor(i / cols) - (rows - 1) / 2) * SPACING, 0);
      const fallbackRadius = 0.5 * Math.hypot(model.bbox_x || 1, model.bbox_y || 1, model.bbox_z || 1);
      const radius = model.radius || fallbackRadius;
      const scale = (FILL * SPACING) / radius;
      const centre = new THREE.Vector3(slot.x, slot.y, radius * scale);
      const item = { model, slot, scale, centre, mesh: null, loading: false, distance: Infinity, impostor: false };
      if (field && model.ready) {
        field.add({ id: model.id, center: centre.toArray(), radius: radius * scale, url: model.color_url });
        item.impostor = true;
      }
      return item;
    });
    const span = Math.max(cols, rows) * SPACING;
    camera.position.set(span * 0.15, -span * 0.85, span * 0.55);
    camera.near = SPACING / 50;
    camera.far = span * 20;
    camera.updateProjectionMatrix();
    controls.target.set(0, 0, 0);
    controls.update();
    live.current = { ...live.current, controls, field, near: settings.near, maxMeshes: settings.maxMeshes };
    field?.setBlend(settings.blend);

    let disposed = false;
    const queue = [];
    let active = 0;
    const concurrency = useImpostors ? 2 : 4;
    const pump = () => {
      while (active < concurrency && queue.length) {
        const item = queue.shift();
        if (item.mesh || disposed) continue;
        active += 1;
        const [format, url] = modelUrl(item.model);
        load(format, url).then((object) => {
          if (disposed) { dispose(object); return; }
          prepare(object);
          let inner = object;
          if (SCENE_FORMATS.includes(item.model.format)) { // glTF: metres and Y up, shown in millimetres with Z up like the bake
            inner = new THREE.Group();
            inner.add(object);
            inner.scale.setScalar(WEB_GLB_TO_MM);
            inner.rotation.x = Math.PI / 2;
          }
          const pivot = new THREE.Group();
          pivot.add(inner);
          let centre = item.model.center ? new THREE.Vector3(...item.model.center) : null;
          let radius = item.model.radius;
          if (!centre) {
            const box = new THREE.Box3().setFromObject(inner);
            centre = box.getCenter(new THREE.Vector3());
            radius = box.getSize(new THREE.Vector3()).length() / 2 || 1;
            item.scale = (FILL * SPACING) / radius;
            item.centre.z = radius * item.scale;
          }
          inner.position.sub(centre);
          pivot.scale.setScalar(item.scale);
          pivot.position.copy(item.centre);
          pivot.visible = false;
          item.mesh = pivot;
          item.triangles = item.model.triangles;
          scene.add(pivot);
        }).catch(() => { item.failed = true; }).finally(() => { active -= 1; item.loading = false; pump(); });
      }
    };

    const resize = () => {
      const width = container.clientWidth || 960;
      const height = container.clientHeight || 540;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();

    const lod = () => {
      const near = live.current.near;
      const cap = live.current.maxMeshes;
      for (const item of items) item.distance = camera.position.distanceTo(item.centre);
      let wanted;
      if (!useImpostors) wanted = new Set(items);
      else { // the nearest impostors within reach swap to their mesh (up to the cap); a model without an impostor is always a mesh
        const close = items.filter((it) => it.impostor && it.distance < near).sort((a, b) => a.distance - b.distance).slice(0, Math.max(cap, 0));
        wanted = new Set([...close, ...items.filter((it) => !it.impostor)]);
      }
      for (const item of items) {
        const want = wanted.has(item);
        if (want && !item.mesh && !item.loading && !item.failed) { item.loading = true; queue.push(item); }
        const meshShown = want && !!item.mesh;
        if (item.mesh) item.mesh.visible = meshShown;
        if (field && item.impostor) field.setVisible(item.model.id, !meshShown);
      }
      queue.sort((a, b) => a.distance - b.distance);
      pump();
      if (useImpostors) { // drop far meshes beyond twice the cap so memory stays bounded while moving around
        const loaded = items.filter((it) => it.mesh && !it.mesh.visible).sort((a, b) => b.distance - a.distance);
        for (const item of loaded.slice(0, Math.max(0, loaded.length - cap))) { scene.remove(item.mesh); dispose(item.mesh); item.mesh = null; }
      }
    };

    let frames = 0;
    let frame = 0;
    let last = performance.now();
    const samples = [];
    renderer.setAnimationLoop(() => {
      controls.update();
      if (frame++ % 6 === 0) lod();
      renderer.render(scene, camera);
      frames += 1;
      const now = performance.now();
      if (now - last >= 1000) {
        const fps = (frames * 1000) / (now - last);
        samples.push(fps);
        if (samples.length > 30) samples.shift();
        frames = 0;
        last = now;
        const meshes = items.filter((it) => it.mesh?.visible).length;
        const pending = items.filter((it) => it.loading).length;
        const shown = { fps: Math.round(fps * 10) / 10, impostors: field ? field.drawn : 0, meshes, loading: pending, models: items.length,
          atlases: field ? field.loaded : 0, calls: renderer.info.render.calls, triangles: renderer.info.render.triangles,
          mode: useImpostors ? "impostors" : "meshes", settled: pending === 0 && queue.length === 0 && (!field || field.loaded + field.failed >= field.slots.size) };
        window.__mesa = { ...shown, samples: [...samples] };
        if (!disposed) setStats(shown);
      }
    });
    field?.load({ concurrency: 6 });

    const onDouble = (event) => {
      const rect = renderer.domElement.getBoundingClientRect();
      const pointer = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      const ray = new THREE.Raycaster();
      ray.setFromCamera(pointer, camera);
      const hit = new THREE.Vector3();
      if (!ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), hit)) return;
      let best = null;
      for (const item of items) { const d = Math.hypot(item.slot.x - hit.x, item.slot.y - hit.y); if (d < SPACING / 2 && (!best || d < best.d)) best = { d, item }; }
      if (best) window.location.hash = `#/modelo/${best.item.model.id}`;
    };
    renderer.domElement.addEventListener("dblclick", onDouble);

    return () => {
      disposed = true;
      renderer.setAnimationLoop(null);
      renderer.domElement.removeEventListener("dblclick", onDouble);
      observer.disconnect();
      controls.dispose();
      field?.dispose();
      for (const item of items) if (item.mesh) { scene.remove(item.mesh); dispose(item.mesh); }
      table.dispose?.();
      renderer.dispose();
      renderer.domElement.remove();
      live.current = { near: live.current.near, maxMeshes: live.current.maxMeshes };
      delete window.__mesa;
    };
  }, [data, settings.impostors]); // eslint-disable-line react-hooks/exhaustive-deps

  const missing = data ? data.models.filter((m) => !m.ready).map((m) => m.id) : [];
  const watchBatch = useCallback((jobId) => {
    clearTimeout(batchTimer.current);
    api.impostorBatchStatus(jobId).then((job) => {
      setBatch(job);
      if (job.status === "running") batchTimer.current = setTimeout(() => watchBatch(jobId), 1500);
      else setVersion((v) => v + 1);
    }).catch((failure) => setError(failure.message));
  }, []);
  const bake = async () => {
    setError(null);
    try { const job = await api.impostorBatch({ ids: missing }); setBatch(job); watchBatch(job.job_id); } catch (failure) { setError(failure.message); }
  };
  const apply = () => setSettings({ ...draft });

  return (
    <div className="space-y-4">
      <PageHeader title="Mesa 3D" description="Muchos modelos de la biblioteca a la vez, cada uno en su casilla y a la misma escala visual. Los lejanos se dibujan como impostores (una imagen elegida entre 64 vistas horneadas del modelo, dos triángulos) y los cercanos con su malla real. Doble clic sobre un modelo para abrir su ficha." />
      <section className="panel-white space-y-3" aria-label="Opciones de la mesa">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block"><span className="label">Modelos</span>
            <select className="field" value={draft.count} onChange={(e) => setDraft({ ...draft, count: Number(e.target.value) })}>
              {COUNTS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select></label>
          <label className="block"><span className="label">Nombre o ruta</span>
            <input className="field" value={draft.q} placeholder="todos" onChange={(e) => setDraft({ ...draft, q: e.target.value })} /></label>
          <label className="block"><span className="label">Colección</span>
            <input className="field" value={draft.collection} placeholder="todas" onChange={(e) => setDraft({ ...draft, collection: e.target.value })} /></label>
          <label className="block"><span className="label">Máximo de triángulos por modelo</span>
            <select className="field" value={draft.maxTriangles} onChange={(e) => setDraft({ ...draft, maxTriangles: Number(e.target.value) })}>
              {[[50_000, "50 000"], [200_000, "200 000"], [1_000_000, "1 millón"], [2_000_000, "2 millones"]].map(([v, t]) => <option key={v} value={v}>{t}</option>)}
            </select></label>
          <button type="button" className="btn btn-primary" onClick={apply}>Mostrar</button>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-[13px]">
          <label className="inline-flex items-center gap-2"><input type="checkbox" checked={settings.impostors} data-impostors-toggle
            onChange={(e) => { setSettings({ ...settings, impostors: e.target.checked }); setDraft({ ...draft, impostors: e.target.checked }); }} />Usar impostores para los lejanos</label>
          <label className="inline-flex items-center gap-2"><input type="checkbox" checked={settings.blend} disabled={!settings.impostors}
            onChange={(e) => setSettings({ ...settings, blend: e.target.checked })} />Mezclar las tres vistas vecinas</label>
          <label className="inline-flex items-center gap-2">Malla real a menos de
            <input type="range" min={100} max={1500} step={20} value={settings.near} disabled={!settings.impostors}
              onChange={(e) => setSettings({ ...settings, near: Number(e.target.value) })} /><span className="num">{settings.near} mm</span></label>
          <label className="inline-flex items-center gap-2">Máximo de mallas reales
            <input className="field num" style={{ width: 80 }} type="number" min={0} max={200} value={settings.maxMeshes} disabled={!settings.impostors}
              onChange={(e) => setSettings({ ...settings, maxMeshes: Math.max(0, Number(e.target.value) || 0) })} /></label>
          <button type="button" className="btn" onClick={() => setSpin(!spin)}>{spin ? "Parar el giro" : "Girar la cámara"}</button>
        </div>
        {data && (
          <p className="help" data-impostor-ready={data.ready} data-impostor-total={data.total}>
            {data.ready} de {data.total} modelos tienen impostor ({data.grid} × {data.grid} vistas).
            {missing.length > 0 && settings.impostors && " Los que no lo tienen se dibujan siempre con su malla."}
          </p>
        )}
        {missing.length > 0 && (
          <button type="button" className="btn" disabled={batch?.status === "running"} onClick={bake}>
            {batch?.status === "running" ? `Generando impostores… ${batch.done} de ${batch.total}` : `Generar los ${missing.length} impostores que faltan`}</button>
        )}
        {batch && batch.status !== "running" && <p className="help" role="status">Lote terminado: {batch.baked} generados, {batch.cached} ya estaban, {batch.failed.length} con error, {batch.seconds} s.</p>}
        {error && <p role="alert" className="text-[13px]" style={{ color: "var(--danger-ink)" }}>{error}</p>}
      </section>
      <div ref={host} className="viewer" style={{ height: "68vh", minHeight: 360 }} aria-label="Mesa 3D" data-mesa-mode={settings.impostors ? "impostors" : "meshes"}>
        <div className="hud">
          {stats ? `${stats.fps} fps · ${stats.impostors} impostores · ${stats.meshes} mallas reales${stats.loading ? ` · cargando ${stats.loading}` : ""} · ${stats.calls} llamadas de dibujo · ${stats.triangles.toLocaleString("es-ES")} triángulos`
            : "Preparando la mesa…"}
        </div>
      </div>
    </div>
  );
}
