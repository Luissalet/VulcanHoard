import { useEffect, useRef, useState } from 'react';
import {
  Cube,
  FolderOpen,
  UploadSimple,
  ArrowRight,
  Stack,
  Sparkle,
  DownloadSimple,
  Check,
  Plus,
  ArrowCounterClockwise,
  ArrowsOut,
  WarningCircle,
  Eye,
  GridFour,
  Waveform,
  FloppyDisk,
  CaretRight,
  SpinnerGap,
  SidebarSimple,
  LinkSimple,
} from '@phosphor-icons/react';
import Viewport from './Viewport';
import CommandEditor from './CommandEditor';
import EvaluationPanel from './EvaluationPanel';
import EvaluationViewport from './EvaluationViewport';
import ProjectControls from './ProjectControls';
import OperationsPanel from './OperationsPanel';
import ReferencePosePanel from './ReferencePosePanel';
import SceneReading from './SceneReading';
import type { ViewportHandle } from './Viewport';
import { api, imageUrl, PALETTE } from './types';
import { changedRegionFields, onAgentChange, rebaseProject, reportView } from './hoard-copilot';
import type { Asset, ImageMode, Project, Region, Relation, Surface } from './types';

type Summary = { id: string; name: string; updated: number; textures: number };
type Job = { id: string; status: string; message: string };

function UVCanvas({
  project,
  asset,
  mode,
  selected,
  wire,
  lines,
  onPick,
}: {
  project: Project;
  asset: Asset;
  mode: ImageMode;
  selected: number | null;
  wire: boolean;
  lines: number[][];
  onPick: (x: number, y: number) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    const im = new Image();
    im.src = imageUrl(project, asset, asset.regions.length ? mode : 'original');
    im.onload = async () => {
      if (!alive || !canvas.current) return;
      setError('');
      const c = canvas.current;
      c.width = im.width;
      c.height = im.height;
      const ctx = c.getContext('2d')!;
      ctx.drawImage(im, 0, 0);
      if (selected !== null && asset.regions.length) {
        try {
          const response = await fetch(
            `/api/projects/${project.id}/assets/${asset.id}/mask?v=${asset.version}`,
          );
          if (!response.ok) throw new Error();
          const mask = new Int16Array(await response.arrayBuffer());
          if (!alive) return;
          const overlay = document.createElement('canvas');
          overlay.width = asset.workWidth;
          overlay.height = asset.workHeight;
          const oc = overlay.getContext('2d')!,
            pixels = oc.createImageData(overlay.width, overlay.height);
          for (let i = 0; i < mask.length; i++) {
            if (mask[i] === selected) {
              pixels.data.set([255, 255, 255, 75], i * 4);
            } else if (mask[i] >= 0) {
              pixels.data.set([15, 20, 19, 120], i * 4);
            }
          }
          oc.putImageData(pixels, 0, 0);
          ctx.drawImage(overlay, 0, 0, c.width, c.height);
        } catch {
          /* The texture itself remains available if a mask is temporarily missing. */
        }
      }
      if (wire) {
        ctx.strokeStyle = 'rgba(255,255,255,.42)';
        ctx.lineWidth = 0.6;
        ctx.beginPath();
        for (const t of lines) {
          ctx.moveTo(t[0] * c.width, (1 - t[1]) * c.height);
          ctx.lineTo(t[2] * c.width, (1 - t[3]) * c.height);
          ctx.lineTo(t[4] * c.width, (1 - t[5]) * c.height);
          ctx.closePath();
        }
        ctx.stroke();
      }
    };
    im.onerror = () => {
      if (alive) setError('No se pudo cargar la textura. Vuelve a seleccionarla.');
    };
    return () => {
      alive = false;
    };
  }, [project.id, asset, mode, selected, wire, lines]);
  return (
    <div className="uv-canvas-wrap">
      {error ? (
        <p role="alert">{error}</p>
      ) : (
        <canvas
          ref={canvas}
          aria-label={`Textura UV ${asset.name}. Selecciona también las zonas desde el panel lateral.`}
          onClick={(e) => {
            const b = e.currentTarget.getBoundingClientRect();
            onPick((e.clientX - b.left) / b.width, (e.clientY - b.top) / b.height);
          }}
        />
      )}
    </div>
  );
}

export default function App() {
  const [control, setControl] = useState(false);
  const [posePanel, setPosePanel] = useState(false);
  const [poseVisited, setPoseVisited] = useState(false);
  const [project, setProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<Summary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [surface, setSurface] = useState<Surface>('model');
  const [mode, setMode] = useState<ImageMode>('original');
  const [projectFilter, setProjectFilter] = useState('');
  const [portablePath, setPortablePath] = useState('');
  const [portableForm, setPortableForm] = useState(false);
  const [portableSave, setPortableSave] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const [status, setStatus] = useState('Modelo + texturas UV');
  const [models, setModels] = useState<{ name: string; vision: boolean }[]>([]);
  const [model, setModel] = useState('');
  const [online, setOnline] = useState(false);
  const [clusters, setClusters] = useState(6);
  const [resolution, setResolution] = useState(768);
  const [strength, setStrength] = useState(1);
  const [wire, setWire] = useState(false);
  const [tab, setTab] = useState<'regions' | 'reasoning'>('regions');
  const [relations, setRelations] = useState<Relation[]>([]);
  const [rail, setRail] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [uvLines, setUvLines] = useState<number[][]>([]);
  const input = useRef<HTMLInputElement>(null);
  const blendInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const viewer = useRef<ViewportHandle>(null);
  const editor = useRef<HTMLElement>(null);
  const latest = useRef(project);
  const savedProject = useRef<Project | null>(null);
  latest.current = project;
  const active = project?.assets.find((a) => a.id === activeId) ?? project?.assets[0] ?? null;
  const region = active?.regions.find((r) => r.id === selected) ?? null;
  const count = project?.assets.reduce((n, a) => n + a.regions.length, 0) ?? 0;
  const ready = project?.assets.filter((a) => a.regions.length).length ?? 0;
  const pendingEdit = useRef<Promise<unknown>>(Promise.resolve());
  const working = !!busy || (!!job && ['running', 'queued'].includes(job.status));
  // The Faustus copilot asks what is open: report the real ids, and refresh after an agent turn.
  useEffect(() => {
    reportView({
      projectId: project?.id ?? null,
      assetId: active?.id ?? null,
      regionId: region?.id ?? null,
      surface,
      mode,
      tab,
      panel: posePanel ? 'pose' : control ? 'control' : 'workspace',
      regionDraft: region
        ? { name: region.name, height: region.height, displacementColor: region.displacementColor ?? null }
        : null,
      regionChanges: region ? changedRegionFields(savedProject.current?.assets.find((a) => a.id === active?.id)?.regions.find((r) => r.id === region.id), region) : {},
    });
  }, [project?.id, active?.id, region, surface, mode, tab, posePanel, control]);
  useEffect(
    () =>
      onAgentChange(() => {
        const reload = async () => {
          try {
            await pendingEdit.current.catch(() => {});
            const id = latest.current?.id;
            await refreshProjects();
            if (id) {
              const fresh = await api<Project>(`/projects/${id}`);
              if (latest.current?.id === id) accept(fresh);
            }
          } catch {
            /* the next action reloads */
          }
        };
        void reload();
      }),
    [],
  );
  function accept(p: Project) {
    if (!latest.current || latest.current.id !== p.id)
      setSurface(p.evaluation ? 'figure' : p.models.length ? 'model' : 'uv');
    const merged = rebaseProject(savedProject.current, p, latest.current);
    savedProject.current = p;
    latest.current = merged;
    setProject(merged);
    localStorage.setItem('relief-project', p.id);
    const url = new URL(location.href);
    url.searchParams.set('project', p.id);
    history.replaceState(null, '', url);
    setActiveId((id) => (p.assets.some((a) => a.id === id) ? id : (p.assets[0]?.id ?? null)));
  }
  async function refreshProjects() {
    setProjects(await api<Summary[]>('/projects'));
  }
  async function refreshModels() {
    const data = await api<{ online: boolean; models: { name: string; vision: boolean }[] }>(
      '/models',
    );
    setModels(data.models);
    setOnline(data.online);
    if (data.online) {
      const visual = data.models.filter((m) => m.vision);
      setModel(
        (current) =>
          [current, localStorage.getItem('relief-model'), 'qwen3.8:27b-q8_0', visual[0]?.name].find(
            (name) => visual.some((m) => m.name === name),
          ) || '',
      );
    }
  }
  useEffect(() => {
    refreshProjects().catch((e) => setError(e.message));
    refreshModels().catch(() => {});
    const id =
      new URLSearchParams(location.search).get('project') || localStorage.getItem('relief-project');
    if (id)
      api<Project>(`/projects/${id}`)
        .then(accept)
        .catch(() => localStorage.removeItem('relief-project'));
  }, []);
  useEffect(() => {
    if (model) localStorage.setItem('relief-model', model);
  }, [model]);
  useEffect(() => {
    setRelations(project?.proposal?.relations ?? []);
  }, [project?.proposal]);
  useEffect(() => {
    if (project?.id)
      api<Job | null>(`/projects/${project.id}/analysis-status`)
        .then(setJob)
        .catch(() => {});
  }, [project?.id]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(''), 5000);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    setUvLines(viewer.current?.uvLines(active?.id ?? '') ?? []);
  }, [active?.id, status]);
  useEffect(() => {
    if (selected !== null)
      editor.current?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  }, [selected, activeId]);
  useEffect(() => {
    if (!job || !['running', 'queued'].includes(job.status)) return;
    let alive = true;
    const t = setInterval(async () => {
      try {
        const data = await api<Job>(`/jobs/${job.id}`);
        if (!alive) return;
        setJob(data);
        if (data.status === 'done') {
          const p = await api<Project>(`/projects/${latest.current!.id}`);
          accept(p);
          setTab('reasoning');
          if (p.evaluation) setSurface('figure');
          setToast(data.message);
        }
        if (data.status === 'error') setError(data.message);
      } catch (e) {
        if (alive) {
          setError((e as Error).message);
          setJob(null);
        }
      }
    }, 2000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [job?.id, job?.status]);
  async function run(message: string, fn: () => Promise<void>) {
    setError('');
    setBusy(message);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  }
  async function importFiles(files: File[]) {
    const blends = files.filter((f) => /\.blend$/i.test(f.name));
    if (blends.length > 1) {
      setError('Elige un solo .blend. Se abrirá la escena con sus texturas aplicadas.');
      return;
    }
    const supported = blends.length
      ? blends
      : files.filter(
          (f) =>
            /\.(png|jpe?g|webp|tga|bmp|dae|glb|blend)$/i.test(f.name) &&
            !/(^|\/)upscaled_chain\/|(^|\/)baked\//i.test(f.webkitRelativePath),
        );
    if (!supported.length) {
      setError(
        'Importa DAE o GLB junto con sus texturas PNG, JPG, WebP o TGA. Para evaluar con Figure Tools, añade también una copia .blend con texturas empaquetadas o rutas absolutas.',
      );
      return;
    }
    await run(
      blends.length
        ? 'Abriendo escena Blender y texturas aplicadas…'
        : `Importando ${supported.length} archivos…`,
      async () => {
        let p = latest.current;
        if (!p) {
          const name =
            supported[0].webkitRelativePath.split('/')[0] ||
            supported[0].name.replace(/\.[^.]+$/, '');
          p = await api<Project>('/projects', { method: 'POST', body: JSON.stringify({ name }) });
        }
        const form = new FormData();
        supported.forEach((f) => form.append('files', f));
        const result = await api<{ project: Project; errors: string[] }>(
          `/projects/${p.id}/import`,
          {
            method: 'POST',
            body: form,
          },
        );
        accept(result.project);
        if (result.errors.length) setError(result.errors.join(' · '));
        setSurface(result.project.models.length ? 'model' : 'uv');
        setMode('original');
        setToast(
          blends.length
            ? `Escena Blender abierta · ${result.project.assets.length} texturas a resolución original`
            : `${result.project.assets.length} texturas en el proyecto`,
        );
        await refreshProjects();
      },
    );
  }
  async function newProject() {
    await run('Creando proyecto vacío…', async () => {
      await pendingEdit.current;
      const p = await api<Project>('/projects', {
        method: 'POST',
        body: JSON.stringify({ name: 'Sin título' }),
      });
      accept(p);
      setActiveId(null);
      setSelected(null);
      setJob(null);
      setRelations([]);
      setUvLines([]);
      setMode('original');
      setSurface('model');
      setTab('regions');
      setWire(false);
      setStrength(1);
      setProjectFilter('');
      setStatus('Abre un proyecto Blender para empezar.');
      setToast('Proyecto vacío. El anterior sigue en Proyectos guardados.');
      await refreshProjects();
    });
  }
  async function openPortable(path?: string, folder = false) {
    if (!path && !window.sculptorsHoardDesktop?.pickPortable) {
      setPortableSave(false);
      setPortableForm(true);
      return;
    }
    await run('Abriendo proyecto portable…', async () => {
      const selectedPath = path || (await window.sculptorsHoardDesktop!.pickPortable!(folder));
      if (!selectedPath) return;
      await pendingEdit.current;
      const result = await api<Project>('/projects/import-portable', {
        method: 'POST',
        body: JSON.stringify({ path: selectedPath }),
      });
      accept(result);
      setSelected(null);
      setJob(null);
      setRelations([]);
      setUvLines([]);
      setMode('color');
      setTab('regions');
      setPortableForm(false);
      setProjectFilter('');
      setToast('Proyecto abierto para revisar. El original se conserva.');
      await refreshProjects();
    });
  }
  async function savePortable(path?: string) {
    if (!project) return;
    if (!path && !window.sculptorsHoardDesktop?.savePortable) {
      setPortableSave(true);
      setPortablePath('');
      setPortableForm(true);
      return;
    }
    await run('Guardando proyecto .gepettos…', async () => {
      const selectedPath = path || (await window.sculptorsHoardDesktop!.savePortable!());
      if (!selectedPath) return;
      await pendingEdit.current;
      await api(`/projects/${project.id}/export-portable`, {
        method: 'POST',
        body: JSON.stringify({ path: selectedPath }),
      });
      setPortableForm(false);
      setToast('Archivo .gepettos guardado. Listo para abrir y revisar.');
    });
  }
  async function openBlender() {
    if (!window.sculptorsHoardDesktop) {
      blendInput.current?.click();
      return;
    }
    await run('Abriendo escena Blender y texturas aplicadas…', async () => {
      const path = await window.sculptorsHoardDesktop!.pickBlend();
      if (!path) return;
      let p = latest.current;
      if (!p)
        p = await api<Project>('/projects', {
          method: 'POST',
          body: JSON.stringify({
            name: path
              .replaceAll('\\', '/')
              .split('/')
              .pop()!
              .replace(/\.blend$/i, ''),
          }),
        });
      const result = await api<Project>(`/projects/${p.id}/import-blender-path`, {
        method: 'POST',
        body: JSON.stringify({ path }),
      });
      accept(result);
      setSurface('model');
      setMode('original');
      setToast(`Escena Blender abierta · ${result.assets.length} texturas a resolución original`);
      await refreshProjects();
    });
  }
  async function prepareAll() {
    if (!project) return;
    await run('Preparando regiones…', async () => {
      let p = project;
      for (let i = 0; i < p.assets.length; i++) {
        const a = p.assets[i];
        if (a.regions.length) continue;
        setBusy(`Preparando ${i + 1}/${p.assets.length} · ${a.name}`);
        p = await api<Project>(`/projects/${p.id}/assets/${a.id}/segment`, {
          method: 'POST',
          body: JSON.stringify({ clusters, resolution }),
        });
        accept(p);
      }
      setMode('ids');
      setTab('regions');
      setToast('Regiones listas. Ahora la IA puede relacionarlas con el modelo.');
    });
  }
  async function prepareActive() {
    if (!project || !active) return;
    await run('Detectando regiones…', async () => {
      accept(
        await api<Project>(`/projects/${project.id}/assets/${active.id}/segment`, {
          method: 'POST',
          body: JSON.stringify({ clusters, resolution }),
        }),
      );
      setSelected(null);
      setMode('ids');
    });
  }
  async function pick(aid: string, u: number, v: number) {
    const p = latest.current;
    if (!p) return;
    const a = p.assets.find((a) => a.id === aid);
    if (!a) return;
    setActiveId(aid);
    setTab('regions');
    if (!a.regions.length) {
      setSelected(null);
      return;
    }
    try {
      const r = await fetch(`/api/projects/${p.id}/assets/${aid}/mask?v=${a.version}`);
      if (!r.ok) throw new Error('No se pudo leer la máscara.');
      const data = new Int16Array(await r.arrayBuffer());
      const x = Math.min(a.workWidth - 1, Math.max(0, Math.floor(u * a.workWidth))),
        y = Math.min(a.workHeight - 1, Math.max(0, Math.floor(v * a.workHeight)));
      const id = data[y * a.workWidth + x];
      setSelected(id >= 0 ? id : null);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function updateRegion(patch: Partial<Region>) {
    if (!project || !active || !region) return;
    setProject({
      ...project,
      assets: project.assets.map((a) =>
        a.id === active.id
          ? { ...a, regions: a.regions.map((r) => (r.id === region.id ? { ...r, ...patch } : r)) }
          : a,
      ),
    });
  }
  async function saveRegion() {
    const p = latest.current;
    if (!p || !active) return;
    const a = p.assets.find((a) => a.id === active.id)!;
    const original = savedProject.current?.assets.find((item) => item.id === a.id);
    const patches = a.regions.map((item) => ({ id: item.id, fields: changedRegionFields(original?.regions.find((r) => r.id === item.id), item) })).filter((item) => Object.keys(item.fields).length);
    if (!patches.length) return;
    pendingEdit.current = api<Project>(`/projects/${p.id}/assets/${a.id}/regions`, {
      method: 'PATCH',
      body: JSON.stringify({ patches }),
    }).then((fresh) => {
      if (latest.current?.id !== p.id) return;
      savedProject.current = p;
      accept(fresh);
    });
    await run('Guardando cambio…', async () => {
      await pendingEdit.current;
    });
  }

  async function analyze() {
    if (!project) return;
    await run('Capturando vistas y correspondencias UV…', async () => {
      const capture = await viewer.current!.capture();
      const result = await api<Job>(`/projects/${project.id}/analyze`, {
        method: 'POST',
        body: JSON.stringify({ model, ...capture }),
      });
      setJob(result);
      setTab('reasoning');
    });
  }
  async function exportProject() {
    if (!project) return;
    await run('Reconstruyendo bordes y exportando a resolución original…', async () => {
      const response = await fetch(
        `/api/projects/${project.id}/${project.evaluation ? 'evaluated-export' : 'export'}`,
      );
      if (!response.ok) {
        const e = await response.json();
        throw new Error(e.detail);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${project.name.replace(/[^\w -]/g, '')}-heightmaps.zip`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      setToast('Exportación completa: mapas, paleta y decisiones.');
    });
  }
  function nameFor(key: string) {
    const [aid, rid] = key.split(':');
    return (
      project?.proposal?.regions.find((r) => r.key === key)?.name ||
      project?.assets.find((a) => a.id === aid)?.regions.find((r) => r.id === +rid)?.name ||
      key
    );
  }

  return (
    <div
      className="app"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        if (!working) void importFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <input
        ref={blendInput}
        type="file"
        accept=".blend"
        hidden
        onChange={(e) => {
          void importFiles(Array.from(e.target.files ?? []));
          e.target.value = '';
        }}
      />
      <input
        ref={input}
        type="file"
        multiple
        accept=".png,.jpg,.jpeg,.webp,.tga,.bmp,.dae,.glb,.blend"
        hidden
        onChange={(e) => {
          void importFiles(Array.from(e.target.files ?? []));
          e.target.value = '';
        }}
      />
      <input
        ref={folderInput}
        type="file"
        multiple
        hidden
        {...{ webkitdirectory: '' }}
        onChange={(e) => {
          void importFiles(Array.from(e.target.files ?? []));
          e.target.value = '';
        }}
      />
      <header className="topbar with-pose-navigation">
        <a className="brand" href="/" aria-label="Figuras y relieves">
          <span className="brand-symbol">
            <img src="/fusion-static/icon-192.png" alt="" width={30} height={30} />
          </span>
          <strong>
            figuras y <span>relieves</span>
          </strong>
        </a>
        <div className="project-breadcrumb">
          <span>Taller</span>
          <CaretRight size={12} />
          <strong>{project?.name || 'Nuevo proyecto'}</strong>
        </div>
        <div className="top-actions">
          <button className="button secondary" aria-pressed={posePanel} onClick={() => { setPoseVisited(true); setPosePanel(!posePanel); setControl(false); }}>
            Posar desde imagen
          </button>
          <button className="button secondary" onClick={() => { setControl(!control); setPosePanel(false); }}>
            {control ? 'Volver al taller' : 'Equipo y lotes'}
          </button>
          <span className="local-label">
            <span className={online ? 'status-dot' : 'status-dot offline'} />
            IA global · {online ? 'configurada en Faustus' : 'no disponible'}
          </span>
          <button
            className="button plain mobile-rail"
            aria-label="Mostrar biblioteca"
            onClick={() => setRail(!rail)}
          >
            <SidebarSimple size={20} />
          </button>
          <button
            className="button secondary"
            disabled={!count || working}
            onClick={() => void exportProject()}
          >
            <DownloadSimple size={16} />
            <span>Exportar mapas</span>
          </button>
        </div>
      </header>
      {poseVisited && <div style={{ display: posePanel ? 'flex' : 'none', flex: 1, minHeight: 0 }}><ReferencePosePanel project={project} onClose={() => setPosePanel(false)} /></div>}
      {control && (
        <OperationsPanel
          model={model}
          onModel={(m) => {
            setModel(m);
            localStorage.setItem('relief-model', m);
          }}
          models={models}
          onRefresh={refreshModels}
          onOpen={(id) =>
            void run('Abriendo resultado…', async () => {
              accept(await api<Project>(`/projects/${id}`));
              setControl(false);
              await refreshProjects();
            })
          }
        />
      )}
      <div className="workspace" style={{ display: control || posePanel ? 'none' : undefined }}>
        <aside className={`library ${rail ? 'visible' : ''}`}>
          <div className="panel-heading">
            <h2>Biblioteca</h2>
          </div>
          <button
            className="button secondary wide"
            disabled={working}
            onClick={() => void newProject()}
          >
            <Plus size={18} /> Nuevo proyecto
          </button>
          <button className="import-button" disabled={working} onClick={() => void openBlender()}>
            <FolderOpen size={20} />
            <span>
              Abrir proyecto Blender<small>Modelo, ropa y texturas aplicadas</small>
            </span>
            <Plus size={15} />
          </button>
          <button
            className="text-button file-import"
            disabled={working}
            onClick={() => void openPortable()}
          >
            <FolderOpen size={14} /> Abrir .gepettos
          </button>
          <button
            className="text-button file-import"
            disabled={working}
            onClick={() => void openPortable(undefined, true)}
          >
            <FolderOpen size={14} /> Abrir carpeta portable
          </button>
          <button
            className="text-button file-import"
            disabled={working || !project}
            onClick={() => void savePortable()}
          >
            <FloppyDisk size={14} /> Guardar .gepettos
          </button>
          {portableForm && (
            <form
              className="portable-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (portableSave) void savePortable(portablePath.trim());
                else void openPortable(portablePath.trim());
              }}
            >
              <label htmlFor="portable-path">
                {portableSave ? 'Nuevo archivo .gepettos' : 'Archivo o carpeta del proyecto'}
              </label>
              <input
                id="portable-path"
                value={portablePath}
                autoFocus
                required
                placeholder="E:\\Figuras\\Proyecto.gepettos"
                onChange={(event) => setPortablePath(event.target.value)}
                aria-describedby="portable-help"
                disabled={working}
              />
              <small id="portable-help">
                {portableSave
                  ? 'Pega una ruta absoluta nueva. Los archivos existentes no se sobrescriben.'
                  : 'Pega la ruta de un archivo .gepettos o de una carpeta que contiene project.json.'}
              </small>
              <div>
                <button
                  className="button secondary"
                  type="submit"
                  disabled={working || !portablePath.trim()}
                >
                  {portableSave ? 'Guardar' : 'Abrir'}
                </button>
                <button
                  className="text-button"
                  type="button"
                  disabled={working}
                  onClick={() => setPortableForm(false)}
                >
                  Cancelar
                </button>
              </div>
            </form>
          )}
          <button
            className="text-button file-import"
            disabled={working}
            onClick={() => folderInput.current?.click()}
          >
            <FolderOpen size={14} /> Importar carpeta de texturas
          </button>
          <button
            className="text-button file-import"
            disabled={working}
            onClick={() => input.current?.click()}
          >
            <UploadSimple size={14} />
            Añadir DAE o texturas
          </button>
          {project && (
            <>
              <div className="group-heading">
                Modelo <span>{project.models.length}</span>
              </div>
              <div className="model-files">
                {project.models.length ? (
                  project.models.map((m) => (
                    <button
                      className={surface === 'model' ? 'model-file active' : 'model-file'}
                      key={m.id}
                      onClick={() => setSurface('model')}
                    >
                      <Cube size={17} />
                      <span>
                        {/^(SculptHoard|Sculptor’s Hoard|Sculptors-Hoard)-preview\.glb$/.test(
                          m.name,
                        )
                          ? 'Vista previa del modelo'
                          : m.name}
                      </span>
                    </button>
                  ))
                ) : (
                  <p className="muted small">
                    Abre un proyecto Blender para ver el modelo y su ropa.
                  </p>
                )}
              </div>
              <div className="group-heading">
                Texturas <span>{project.assets.length}</span>
              </div>
              <div className="asset-list">
                {project.assets.map((a) => (
                  <button
                    key={a.id}
                    className={`asset-row ${active?.id === a.id ? 'selected' : ''}`}
                    onClick={() => {
                      setActiveId(a.id);
                      setSelected(null);
                      setRail(false);
                    }}
                  >
                    <img src={imageUrl(project, a, 'original')} alt="" loading="lazy" />
                    <span>
                      <strong>{a.name}</strong>
                      <small>
                        {a.width} × {a.height}
                        {a.regions.length ? ` · ${a.regions.length} zonas` : ''}
                      </small>
                    </span>
                    {a.approved ? (
                      <Check size={14} className="success" />
                    ) : a.regions.length ? (
                      <span className="prepared-mark" />
                    ) : null}
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="library-bottom">
            <div className="group-heading">Proyectos guardados</div>
            <input
              className="project-search"
              aria-label="Buscar proyecto"
              placeholder="Buscar proyecto…"
              value={projectFilter}
              onChange={(e) => setProjectFilter(e.target.value)}
            />
            {projects
              .filter((p) => p.name.toLocaleLowerCase().includes(projectFilter.toLocaleLowerCase()))
              .map((p) => (
                <button
                  className={`saved-project ${project?.id === p.id ? 'current' : ''}`}
                  key={p.id}
                  disabled={working}
                  onClick={() =>
                    void run('Abriendo proyecto…', async () => {
                      accept(await api<Project>(`/projects/${p.id}`));
                      setSelected(null);
                      setJob(null);
                      setRail(false);
                    })
                  }
                >
                  <FolderOpen size={15} />
                  <span>{p.name}</span>
                </button>
              ))}
            {!projects.length && (
              <p className="muted small">Tus proyectos se guardan en este equipo.</p>
            )}
            <div className="privacy-note">
              <FloppyDisk size={14} />
              <span>
                Originales intactos.
                <br />
                Trabajo guardado localmente.
              </span>
            </div>
          </div>
        </aside>
        <main className="main-workspace">
          <div className="document-heading">
            <div>
              <h1>{active ? active.name.replace(/\.[^.]+$/, '') : project?.name || 'Nuevo proyecto'}</h1>
              <p>
                {active
                  ? `${active.width.toLocaleString('es')} × ${active.height.toLocaleString('es')} px · resolución original conservada`
                  : 'Abre un modelo con sus texturas para empezar.'}
              </p>
            </div>
            {project && (
              <ProjectControls
                project={project}
                beforeAction={async () => {
                  await pendingEdit.current;
                  if (!latest.current) throw new Error('Abre un proyecto para continuar.');
                  return latest.current;
                }}
                disabled={working}
                onProject={(p) => {
                  accept(p);
                  void refreshProjects();
                }}
                onError={setError}
              />
            )}
          </div>
          <div className="canvas-shell">
            <div className="canvas-toolbar">
              <div className="segmented dark">
                {(
                  [
                    ['model', 'Modelo 3D', Cube],
                    ['uv', 'Textura UV', GridFour],
                    [
                      project?.evaluation ? 'figure' : 'relief',
                      project?.evaluation ? 'Relieve real' : 'Muestra',
                      Waveform,
                    ],
                  ] as const
                ).map(([value, label, Icon]) => (
                  <button
                    key={value}
                    className={surface === value ? 'active' : ''}
                    aria-pressed={surface === value}
                    disabled={value !== 'model' && !active}
                    onClick={() => setSurface(value)}
                  >
                    <Icon size={16} />
                    {label}
                  </button>
                ))}
              </div>
              <div className="canvas-tools">
                <button
                  className={`icon-button dark-button ${wire ? 'on' : ''}`}
                  title="Ver triangulación UV"
                  aria-label="Ver triangulación UV"
                  disabled={surface !== 'uv'}
                  onClick={() => setWire(!wire)}
                >
                  <GridFour size={17} />
                </button>
                <button
                  className="icon-button dark-button"
                  title="Encuadrar modelo"
                  aria-label="Encuadrar modelo"
                  onClick={() => viewer.current?.reset()}
                >
                  <ArrowsOut size={18} />
                </button>
              </div>
            </div>
            <div className="stage">
              <div
                className={
                  surface === 'uv' || surface === 'figure' ? 'viewport-hidden' : 'viewport-layer'
                }
              >
                <Viewport
                  ref={viewer}
                  project={project}
                  asset={active}
                  selected={selected}
                  mode={mode}
                  surface={surface}
                  strength={strength}
                  onPick={(a, u, v) => void pick(a, u, v)}
                  onStatus={setStatus}
                />
              </div>
              {surface === 'figure' && project && <EvaluationViewport project={project} />}
              {surface === 'uv' && project && active && (
                <UVCanvas
                  project={project}
                  asset={active}
                  mode={mode}
                  selected={selected}
                  wire={wire}
                  lines={uvLines}
                  onPick={(u, v) => void pick(active.id, u, v)}
                />
              )}
              {(!project || (surface === 'model' && !project.models.length)) && (
                <div className="empty-stage">
                  <Cube size={62} weight="thin" />
                  <h2>Añade el modelo y sus texturas.</h2>
                  <p>La IA conectará lo que ve con sus regiones UV.</p>
                  <button
                    className="button primary"
                    disabled={working}
                    onClick={() => input.current?.click()}
                  >
                    <UploadSimple size={17} />
                    Abrir modelo y texturas
                  </button>
                  <button
                    className="demo-link"
                    disabled={working}
                    onClick={() =>
                      void run('Abriendo ejemplo privado…', async () => {
                        accept(await api<Project>('/demo', { method: 'POST' }));
                        await refreshProjects();
                        setSurface('model');
                      })
                    }
                  >
                    Probar con Frank <ArrowRight size={14} />
                  </button>
                  <span className="format-hint">DAE / GLB + PNG · texturas hasta 8K</span>
                </div>
              )}
              {project &&
                surface !== 'figure' &&
                (surface !== 'model' || project.models.length > 0) && (
                  <>
                    <div className="view-label">
                      {surface === 'uv'
                        ? 'UV / 2D'
                        : surface === 'relief'
                          ? 'MUESTRA DE RELIEVE'
                          : 'PERSPECTIVA'}
                      <span>
                        {surface === 'relief'
                          ? 'Vista aproximada sobre una placa'
                          : mode === 'original'
                            ? 'Textura original'
                            : mode === 'ids'
                              ? 'Regiones identificadas'
                              : mode === 'height'
                                ? 'Alturas normalizadas'
                                : 'Paleta de relieve'}
                      </span>
                    </div>
                    <div className="stage-footer">
                      <span>
                        {surface === 'model'
                          ? 'Arrastrar para girar · rueda para acercar · clic para seleccionar'
                          : surface === 'uv'
                            ? 'Selecciona una zona de la textura'
                            : 'Previsualización local; calibra el resultado en Figure Tools'}
                      </span>
                      <span className="axis-label">{surface === 'uv' ? 'U / V' : 'X Y Z'}</span>
                    </div>
                  </>
                )}
            </div>
            <div className="view-controls">
              <div className="view-mode">
                <Eye size={15} />
                <select
                  aria-label="Contenido de la vista"
                  value={mode}
                  onChange={(e) => setMode(e.target.value as ImageMode)}
                >
                  <option value="original">Original</option>
                  <option value="ids" disabled={!count}>
                    Zonas numeradas
                  </option>
                  <option value="color" disabled={!count}>
                    Colores de relieve
                  </option>
                  <option value="height" disabled={!count}>
                    Mapa de alturas
                  </option>
                </select>
              </div>
              {surface === 'relief' ? (
                <label className="strength-label">
                  Intensidad visual
                  <input
                    type="range"
                    min="0"
                    max="3"
                    step=".1"
                    value={strength}
                    onChange={(e) => setStrength(+e.target.value)}
                  />
                  <span>{strength.toFixed(1)}×</span>
                </label>
              ) : (
                <span className="view-status">{status}</span>
              )}
            </div>
          </div>
          <div className="workflow-strip">
            <div className={project ? 'complete' : ''}>
              <span>{project ? <Check size={12} /> : 1}</span>Importar
            </div>
            <div className={count ? 'complete' : ''}>
              <span>{count ? <Check size={12} /> : 2}</span>Preparar UV
            </div>
            <div className={project?.proposal ? 'complete' : ''}>
              <span>{project?.proposal ? <Check size={12} /> : 3}</span>Entender capas
            </div>
            <div className={project?.approved ? 'complete' : ''}>
              <span>{project?.approved ? <Check size={12} /> : 4}</span>Revisar y exportar
            </div>
          </div>
          {error && (
            <div className="error-banner" role="alert">
              <WarningCircle size={18} />
              <span>{error}</span>
              <button onClick={() => setError('')} aria-label="Cerrar error">
                Cerrar
              </button>
            </div>
          )}
        </main>
        <aside className="inspector">
          <div className="inspector-tabs">
            <button className={tab === 'regions' ? 'active' : ''} onClick={() => setTab('regions')}>
              Superficies <span>{count || '—'}</span>
            </button>
            <button
              className={tab === 'reasoning' ? 'active' : ''}
              onClick={() => setTab('reasoning')}
            >
              <Sparkle size={15} />
              Interpretación
            </button>
          </div>
          <div className="inspector-content">
            {project && (
              <EvaluationPanel
                key={project.id}
                project={project}
                model={model}
                disabled={working}
                onJob={setJob}
                onError={setError}
              />
            )}{' '}
            {project && count > 0 && (
              <CommandEditor
                key={project.id}
                project={project}
                model={model}
                selected={active && selected !== null ? [`${active.id}:${selected}`] : []}
                disabled={working}
                onProject={accept}
                onBusy={setBusy}
              />
            )}
            {tab === 'regions' ? (
              <>
                <section className="inspector-section">
                  <h2>Preparar regiones</h2>
                  <p className="muted small">
                    Primero localizamos los contornos. La altura la decide el contexto del modelo.
                  </p>
                  <label className="field-row">
                    Colores candidatos <span>{clusters}</span>
                    <input
                      type="range"
                      min="2"
                      max="12"
                      value={clusters}
                      onChange={(e) => setClusters(+e.target.value)}
                      disabled={working}
                    />
                  </label>
                  <label className="field-select">
                    Detalle de análisis
                    <select
                      value={resolution}
                      onChange={(e) => setResolution(+e.target.value)}
                      disabled={working}
                    >
                      <option value="512">Rápido · 512 px</option>
                      <option value="768">Equilibrado · 768 px</option>
                      <option value="1024">Fino · 1024 px</option>
                      <option value="1536">Muy fino · 1536 px</option>
                    </select>
                  </label>
                  <div className="split-actions">
                    <button
                      className="button primary"
                      disabled={!project?.assets.length || working}
                      onClick={() => void prepareAll()}
                    >
                      <Stack size={16} />
                      {ready ? 'Preparar pendientes' : 'Preparar texturas'}
                    </button>
                    <button
                      className="icon-button outline"
                      aria-label="Recalcular textura activa"
                      title="Recalcular textura activa (restablece sus ajustes)"
                      disabled={!active || working}
                      onClick={() => void prepareActive()}
                    >
                      <ArrowCounterClockwise size={17} />
                    </button>
                  </div>
                  {active?.processingMs !== null && active?.processingMs !== undefined && (
                    <div className="timing">
                      {(active.processingMs / 1000).toFixed(2)} s · análisis de {active.workWidth} ×{' '}
                      {active.workHeight}
                    </div>
                  )}
                </section>
                <section className="inspector-section regions-section">
                  <div className="section-title">
                    <h2>{active ? 'Zonas de esta textura' : 'Tus superficies'}</h2>
                    <span>{active?.regions.length ?? 0}</span>
                  </div>
                  {!active?.regions.length ? (
                    <div className="empty-regions">
                      <Stack size={30} weight="thin" />
                      <p>
                        Las zonas aparecerán aquí.
                        <br />
                        Todas comienzan a la misma altura.
                      </p>
                    </div>
                  ) : (
                    <div className="region-list">
                      {active.regions.map((r) => (
                        <button
                          className={`region-row ${selected === r.id ? 'selected' : ''}`}
                          key={r.id}
                          onClick={() => setSelected(r.id)}
                        >
                          <span
                            className="swatch"
                            style={{ background: PALETTE[r.id % PALETTE.length] }}
                          >
                            <span>{r.id + 1}</span>
                          </span>
                          <span className="region-name">
                            {r.name}
                            <small>
                              {r.confidence === null
                                ? 'Altura editable'
                                : r.geometry === 'modeled'
                                  ? 'Volumen en geometría'
                                  : r.geometry === 'painted'
                                    ? 'Detalle de textura'
                                    : 'Revisar interpretación'}
                            </small>
                          </span>
                          <span className="height-number">{r.height}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </section>
                {region && (
                  <section ref={editor} className="inspector-section region-editor">
                    <h2>Editar superficie</h2>
                    <label className="field-select">
                      Nombre
                      <input
                        value={region.name}
                        onChange={(e) => updateRegion({ name: e.target.value })}
                        onBlur={() => void saveRegion()}
                        disabled={working}
                      />
                    </label>
                    {region.displacementColor && (
                      <label className="field-select">
                        Color de relieve
                        <input
                          type="color"
                          value={
                            '#' +
                            region.displacementColor
                              .map((c) => c.toString(16).padStart(2, '0'))
                              .join('')
                          }
                          disabled={working}
                          onChange={(e) =>
                            updateRegion({
                              displacementColor: [1, 3, 5].map((i) =>
                                parseInt(e.target.value.slice(i, i + 2), 16),
                              ),
                            })
                          }
                          onBlur={() => void saveRegion()}
                        />
                        <small>
                          Este color controla el relieve en Blender. La altura numérica es solo una
                          referencia.
                        </small>
                      </label>
                    )}
                    <label className="field-row">
                      Altura relativa <span>{region.height} / 255</span>
                      <input
                        type="range"
                        min="0"
                        max="255"
                        value={region.height}
                        disabled={working || !!region.displacementColor}
                        onChange={(e) => updateRegion({ height: +e.target.value })}
                        onPointerUp={() => void saveRegion()}
                        onKeyUp={(e) => {
                          if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key))
                            void saveRegion();
                        }}
                      />
                    </label>
                    <div className="height-scale">
                      <span>Hundido</span>
                      <span>Referencia 128</span>
                      <span>Elevado</span>
                    </div>
                    <p className="region-reason">{region.reason}</p>
                    {region.confidence !== null && (
                      <p className="muted micro">
                        Confianza declarada por el modelo: {Math.round(region.confidence * 100)} %.
                        No es una probabilidad calibrada.
                      </p>
                    )}
                  </section>
                )}
              </>
            ) : (
              <>
                <section className="inspector-section">
                  <div className="section-title">
                    <h2>Entender la figura</h2>
                    <Sparkle size={18} />
                  </div>
                  <p className="muted small">
                    La IA observa el modelo desde ambos lados, sus materiales y las regiones UV
                    numeradas.
                  </p>
                  <label className="field-select">
                    Modelo de visión local
                    <select
                      value={model}
                      onChange={(e) => setModel(e.target.value)}
                      disabled={working}
                    >
                      {!models.length && <option value="">Sin modelos disponibles</option>}
                      {models
                        .filter((m) => m.vision)
                        .map((m) => (
                          <option value={m.name} key={m.name}>
                            {m.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <button
                    className="button primary wide"
                    disabled={working || !model || !count || !project?.models.length}
                    onClick={() => void analyze()}
                  >
                    <Sparkle size={17} />
                    Analizar modelo + UV
                  </button>
                  {!online && (
                    <button className="text-button" onClick={() => void refreshModels()}>
                      Volver a consultar el modelo global
                    </button>
                  )}
                  {!project?.models.length && (
                    <p className="small muted">Necesita un modelo DAE o GLB con UV.</p>
                  )}
                </section>
                {job && ['running', 'queued'].includes(job.status) && (
                  <section className="inspector-section analysis-progress">
                    <SpinnerGap size={24} className="spin" />
                    <h3>Observando superficies</h3>
                    <p>{job.message}</p>
                    <span>El tiempo depende del modelo y la GPU.</span>
                  </section>
                )}
                {project?.evaluation?.sceneUnderstanding && (
                  <SceneReading scene={project.evaluation.sceneUnderstanding} />
                )}
                {project?.proposal ? (
                  <>
                    <section className="inspector-section">
                      <div className="section-title">
                        <h2>Lectura del modelo</h2>
                        <span className="proposal-state">
                          {project.proposal.status === 'applied'
                            ? 'Aplicada'
                            : project.proposal.status === 'stale'
                              ? 'Desactualizada'
                              : 'Propuesta'}
                        </span>
                      </div>
                      <p className="analysis-summary">{project.proposal.summary}</p>
                      <p className="muted micro">
                        {project.proposal.model} · {project.proposal.duration.toFixed(1)} s
                      </p>
                    </section>
                    <section className="inspector-section">
                      <h2>Relaciones de relieve</h2>
                      <p className="muted small">
                        Activa solo las capas que necesitan volumen adicional sobre la malla.
                      </p>
                      {relations.length ? (
                        relations.map((r, i) => (
                          <label className="relation" key={i}>
                            <input
                              type="checkbox"
                              checked={r.apply}
                              disabled={working || project.proposal?.status === 'stale'}
                              onChange={(e) =>
                                setRelations((old) =>
                                  old.map((r, j) =>
                                    j === i ? { ...r, apply: e.target.checked } : r,
                                  ),
                                )
                              }
                            />
                            <span>
                              <strong>{nameFor(r.upper)}</strong>
                              <span className="relation-over">
                                <LinkSimple size={12} />
                                sobre {nameFor(r.lower)}
                              </span>
                              <small>{r.reason}</small>
                            </span>
                          </label>
                        ))
                      ) : (
                        <p className="empty-relations">
                          No se han propuesto capas superpuestas. Puedes ajustar alturas
                          manualmente.
                        </p>
                      )}
                      <button
                        className="button secondary wide"
                        disabled={working || project.proposal.status === 'stale'}
                        onClick={() =>
                          void run('Aplicando relaciones…', async () => {
                            accept(
                              await api<Project>(`/projects/${project.id}/apply`, {
                                method: 'POST',
                                body: JSON.stringify({ relations }),
                              }),
                            );
                            setMode('color');
                            setToast('Relaciones aplicadas. Revisa el relieve y ajusta las zonas.');
                          })
                        }
                      >
                        <Check size={16} />
                        Aplicar propuesta revisada
                      </button>
                    </section>
                    {!!project.proposal.warnings.length && (
                      <section className="inspector-section">
                        <h2>Para revisar</h2>
                        {project.proposal.warnings.map((w, i) => (
                          <p className="review-warning" key={i}>
                            <WarningCircle size={15} />
                            {w}
                          </p>
                        ))}
                      </section>
                    )}
                  </>
                ) : (
                  !working &&
                  !project?.evaluation?.sceneUnderstanding && (
                    <section className="inspector-section reasoning-empty">
                      <div className="layer-example">
                        <span>Hebilla</span>
                        <span>Cinturón</span>
                        <span>Pantalón</span>
                      </div>
                      <h3>El porqué de cada altura.</h3>
                      <p>
                        Una hebilla puede ir sobre un cinturón. Si ese volumen ya existe en la
                        malla, no hace falta duplicarlo.
                      </p>
                      <small>Ejemplo de relación, no análisis de tu figura.</small>
                    </section>
                  )
                )}
              </>
            )}
          </div>
          <div className="inspector-footer">
            <button
              className={`button ${project?.approved ? 'approved' : 'secondary'} wide`}
              disabled={!count || working}
              onClick={() =>
                void run('Guardando ejemplo revisado…', async () => {
                  accept(
                    await api<Project>(`/projects/${project!.id}/approve`, { method: 'POST' }),
                  );
                  setToast('Correcciones guardadas como ejemplo para futuros análisis.');
                })
              }
            >
              <Check size={17} />
              {project?.approved ? 'Revisión guardada' : 'Guardar como revisado'}
            </button>
            <p>Las correcciones crean tu biblioteca de ejemplos.</p>
          </div>
        </aside>
      </div>
      <footer className="app-footer">
        <span>
          {busy ? (
            <>
              <SpinnerGap size={13} className="spin" />
              {busy}
            </>
          ) : job && ['queued', 'running'].includes(job.status) ? (
            <>
              <Sparkle size={13} />
              Análisis de visión en curso
            </>
          ) : (
            <>
              <span className="status-dot" />
              Taller listo
            </>
          )}
        </span>
        <span>
          {count ? `${count} zonas · ${ready} texturas preparadas` : 'Procesamiento local'}
          <span className="footer-separator">/</span>Salida a resolución original
        </span>
      </footer>
      {toast && (
        <div className="toast" role="status">
          <Check size={17} />
          {toast}
        </div>
      )}
    </div>
  );
}
