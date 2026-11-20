import { locale, tr } from './i18n';
import { serverMessage } from './serverMessages';
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
      if (alive)
        setError(
          tr(
            'No se pudo cargar la textura. Vuelve a seleccionarla.',
            'Could not load the texture. Select it again.',
          ),
        );
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
          aria-label={tr(
            `Textura UV ${asset.name}. Selecciona también las zonas desde el panel lateral.`,
            `UV texture ${asset.name}. You can also select regions from the side panel.`,
          )}
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
  const [status, setStatus] = useState(tr('Modelo + texturas UV', 'Model + UV textures'));
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
          setToast(serverMessage(data.message));
        }
        if (data.status === 'error') setError(serverMessage(data.message));
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
      setError(
        tr(
          'Elige un solo .blend. Se abrirá la escena con sus texturas aplicadas.',
          'Choose one .blend file. The scene will open with its textures applied.',
        ),
      );
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
        tr(
          'Importa DAE o GLB junto con sus texturas PNG, JPG, WebP o TGA. Para evaluar con Figure Tools, añade también una copia .blend con texturas empaquetadas o rutas absolutas.',
          'Import a DAE or GLB with its PNG, JPG, WebP or TGA textures. To evaluate with Figure Tools, add a .blend copy with packed textures or absolute paths.',
        ),
      );
      return;
    }
    await run(
      blends.length
        ? tr(
            'Abriendo escena Blender y texturas aplicadas…',
            'Opening Blender scene with textures applied…',
          )
        : tr(`Importando ${supported.length} archivos…`, `Importing ${supported.length} files…`),
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
        if (result.errors.length) setError(result.errors.map(serverMessage).join(' · '));
        setSurface(result.project.models.length ? 'model' : 'uv');
        setMode('original');
        setToast(
          blends.length
            ? tr(
                `Escena Blender abierta · ${result.project.assets.length} texturas a resolución original`,
                `Blender scene opened · ${result.project.assets.length} textures at original resolution`,
              )
            : tr(
                `${result.project.assets.length} texturas en el proyecto`,
                `${result.project.assets.length} textures in the project`,
              ),
        );
        await refreshProjects();
      },
    );
  }
  async function newProject() {
    await run(tr('Creando proyecto vacío…', 'Creating an empty project…'), async () => {
      await pendingEdit.current;
      const p = await api<Project>('/projects', {
        method: 'POST',
        body: JSON.stringify({ name: tr('Sin título', 'Untitled') }),
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
      setStatus(tr('Abre un proyecto Blender para empezar.', 'Open a Blender project to get started.'));
      setToast(
        tr(
          'Proyecto vacío. El anterior sigue en Proyectos guardados.',
          'Empty project created. The previous one is in Saved projects.',
        ),
      );
      await refreshProjects();
    });
  }
  async function openPortable(path?: string, folder = false) {
    if (!path && !window.sculptorsHoardDesktop?.pickPortable) {
      setPortableSave(false);
      setPortableForm(true);
      return;
    }
    await run(tr('Abriendo proyecto portable…', 'Opening portable project…'), async () => {
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
      setToast(
        tr(
          'Proyecto abierto para revisar. El original se conserva.',
          'Project opened for review. The original has been kept.',
        ),
      );
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
    await run(tr('Guardando proyecto .gepettos…', 'Saving .gepettos project…'), async () => {
      const selectedPath = path || (await window.sculptorsHoardDesktop!.savePortable!());
      if (!selectedPath) return;
      await pendingEdit.current;
      await api(`/projects/${project.id}/export-portable`, {
        method: 'POST',
        body: JSON.stringify({ path: selectedPath }),
      });
      setPortableForm(false);
      setToast(
        tr(
          'Archivo .gepettos guardado. Listo para abrir y revisar.',
          '.gepettos file saved and ready to open for review.',
        ),
      );
    });
  }
  async function openBlender() {
    if (!window.sculptorsHoardDesktop) {
      blendInput.current?.click();
      return;
    }
    await run(
      tr('Abriendo escena Blender y texturas aplicadas…', 'Opening Blender scene with textures applied…'),
      async () => {
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
        setToast(
          tr(
            `Escena Blender abierta · ${result.assets.length} texturas a resolución original`,
            `Blender scene opened · ${result.assets.length} textures at original resolution`,
          ),
        );
        await refreshProjects();
      },
    );
  }
  async function prepareAll() {
    if (!project) return;
    await run(tr('Preparando regiones…', 'Preparing regions…'), async () => {
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
      setToast(
        tr(
          'Regiones listas. Ahora la IA puede relacionarlas con el modelo.',
          'Regions ready for the AI to match to the model.',
        ),
      );
    });
  }
  async function prepareActive() {
    if (!project || !active) return;
    await run(tr('Detectando regiones…', 'Detecting regions…'), async () => {
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
      if (!r.ok) throw new Error(tr('No se pudo leer la máscara.', 'Could not read the mask.'));
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
    await run(tr('Guardando cambio…', 'Saving change…'), async () => {
      await pendingEdit.current;
    });
  }

  async function analyze() {
    if (!project) return;
    await run(
      tr('Capturando vistas y correspondencias UV…', 'Capturing views and UV correspondences…'),
      async () => {
        const capture = await viewer.current!.capture();
        const result = await api<Job>(`/projects/${project.id}/analyze`, {
          method: 'POST',
          body: JSON.stringify({ model, ...capture }),
        });
        setJob(result);
        setTab('reasoning');
      },
    );
  }
  async function exportProject() {
    if (!project) return;
    await run(
      tr(
        'Reconstruyendo bordes y exportando a resolución original…',
        'Rebuilding edges and exporting at original resolution…',
      ),
      async () => {
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
        setToast(
          tr(
            'Exportación completa: mapas, paleta y decisiones.',
            'Export complete. Maps, palette and decisions saved.',
          ),
        );
      },
    );
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
        <a className="brand" href="/" aria-label={tr('Figuras y relieves', 'Figures and reliefs')}>
          <span className="brand-symbol">
            <img src="/fusion-static/icon-192.png" alt="" width={30} height={30} />
          </span>
          <strong>
            {tr('figuras y', 'figures and')} <span>{tr('relieves', 'reliefs')}</span>
          </strong>
        </a>
        <div className="project-breadcrumb">
          <span>{tr('Taller', 'Workshop')}</span>
          <CaretRight size={12} />
          <strong>{project?.name || tr('Nuevo proyecto', 'New project')}</strong>
        </div>
        <div className="top-actions">
          <button className="button secondary" aria-pressed={posePanel} onClick={() => { setPoseVisited(true); setPosePanel(!posePanel); setControl(false); }}>
            {tr('Posar desde imagen', 'Pose from image')}
          </button>
          <button
            className="button secondary"
            onClick={() => {
              setControl(!control);
              setPosePanel(false);
            }}
          >
            {control
              ? tr('Volver al taller', 'Back to workshop')
              : tr('Equipo y lotes', 'System and batches')}
          </button>
          <span className="local-label">
            <span className={online ? 'status-dot' : 'status-dot offline'} />
            {tr('IA global', 'Global AI')} ·{' '}
            {online
              ? tr('configurada en Faustus', 'configured in Faustus')
              : tr('no disponible', 'unavailable')}
          </span>
          <button
            className="button plain mobile-rail"
            aria-label={tr('Mostrar biblioteca', 'Show library')}
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
            <span>{tr('Exportar mapas', 'Export maps')}</span>
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
            void run(tr('Abriendo resultado…', 'Opening result…'), async () => {
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
            <h2>{tr('Biblioteca', 'Library')}</h2>
          </div>
          <button
            className="button secondary wide"
            disabled={working}
            onClick={() => void newProject()}
          >
            <Plus size={18} /> {tr('Nuevo proyecto', 'New project')}
          </button>
          <button className="import-button" disabled={working} onClick={() => void openBlender()}>
            <FolderOpen size={20} />
            <span>
              {tr('Abrir proyecto Blender', 'Open Blender project')}
              <small>
                {tr('Modelo, ropa y texturas aplicadas', 'Model, clothing and applied textures')}
              </small>
            </span>
            <Plus size={15} />
          </button>
          <button
            className="text-button file-import"
            disabled={working}
            onClick={() => void openPortable()}
          >
            <FolderOpen size={14} /> {tr('Abrir .gepettos', 'Open .gepettos')}
          </button>
          <button
            className="text-button file-import"
            disabled={working}
            onClick={() => void openPortable(undefined, true)}
          >
            <FolderOpen size={14} /> {tr('Abrir carpeta portable', 'Open portable folder')}
          </button>
          <button
            className="text-button file-import"
            disabled={working || !project}
            onClick={() => void savePortable()}
          >
            <FloppyDisk size={14} /> {tr('Guardar .gepettos', 'Save .gepettos')}
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
                {portableSave
                  ? tr('Nuevo archivo .gepettos', 'New .gepettos file')
                  : tr('Archivo o carpeta del proyecto', 'Project file or folder')}
              </label>
              <input
                id="portable-path"
                value={portablePath}
                autoFocus
                required
                placeholder={tr('E:\\\\Figuras\\\\Proyecto.gepettos', 'E:\\Figures\\Project.gepettos')}
                onChange={(event) => setPortablePath(event.target.value)}
                aria-describedby="portable-help"
                disabled={working}
              />
              <small id="portable-help">
                {portableSave
                  ? tr(
                      'Pega una ruta absoluta nueva. Los archivos existentes no se sobrescriben.',
                      'Paste a new absolute path. Existing files are kept.',
                    )
                  : tr(
                      'Pega la ruta de un archivo .gepettos o de una carpeta que contiene project.json.',
                      'Paste the path to a .gepettos file or a folder containing project.json.',
                    )}
              </small>
              <div>
                <button
                  className="button secondary"
                  type="submit"
                  disabled={working || !portablePath.trim()}
                >
                  {portableSave ? tr('Guardar', 'Save') : tr('Abrir', 'Open')}
                </button>
                <button
                  className="text-button"
                  type="button"
                  disabled={working}
                  onClick={() => setPortableForm(false)}
                >
                  {tr('Cancelar', 'Cancel')}
                </button>
              </div>
            </form>
          )}
          <button
            className="text-button file-import"
            disabled={working}
            onClick={() => folderInput.current?.click()}
          >
            <FolderOpen size={14} /> {tr('Importar carpeta de texturas', 'Import texture folder')}
          </button>
          <button
            className="text-button file-import"
            disabled={working}
            onClick={() => input.current?.click()}
          >
            <UploadSimple size={14} />
            {tr('Añadir DAE o texturas', 'Add DAE or textures')}
          </button>
          {project && (
            <>
              <div className="group-heading">
                {tr('Modelo', 'Model')} <span>{project.models.length}</span>
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
                          ? tr('Vista previa del modelo', 'Model preview')
                          : m.name}
                      </span>
                    </button>
                  ))
                ) : (
                  <p className="muted small">
                    {tr(
                      'Abre un proyecto Blender para ver el modelo y su ropa.',
                      'Open a Blender project to see the model and its clothing.',
                    )}
                  </p>
                )}
              </div>
              <div className="group-heading">
                {tr('Texturas', 'Textures')} <span>{project.assets.length}</span>
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
            <div className="group-heading">{tr('Proyectos guardados', 'Saved projects')}</div>
            <input
              className="project-search"
              aria-label={tr('Buscar proyecto', 'Find a project')}
              placeholder={tr('Buscar proyecto…', 'Find a project…')}
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
                    void run(tr('Abriendo proyecto…', 'Opening project…'), async () => {
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
              <p className="muted small">
                {tr(
                  'Tus proyectos se guardan en este equipo.',
                  'Your projects are saved on this computer.',
                )}
              </p>
            )}
            <div className="privacy-note">
              <FloppyDisk size={14} />
              <span>
                {tr('Originales intactos.', 'Originals preserved.')}
                <br />
                {tr('Trabajo guardado localmente.', 'Work saved locally.')}
              </span>
            </div>
          </div>
        </aside>
        <main className="main-workspace">
          <div className="document-heading">
            <div>
              <h1>
                {active
                  ? active.name.replace(/\.[^.]+$/, '')
                  : project?.name || tr('Nuevo proyecto', 'New project')}
              </h1>
              <p>
                {active
                  ? tr(
                      `${active.width.toLocaleString(locale)} × ${active.height.toLocaleString(locale)} px · resolución original conservada`,
                      `${active.width.toLocaleString(locale)} × ${active.height.toLocaleString(locale)} px · original resolution preserved`,
                    )
                  : tr(
                      'Abre un modelo con sus texturas para empezar.',
                      'Open a model with its textures to get started.',
                    )}
              </p>
            </div>
            {project && (
              <ProjectControls
                project={project}
                beforeAction={async () => {
                  await pendingEdit.current;
                  if (!latest.current)
                    throw new Error(tr('Abre un proyecto para continuar.', 'Open a project to continue.'));
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
                    ['uv', tr('Textura UV', 'UV texture'), GridFour],
                    [
                      project?.evaluation ? 'figure' : 'relief',
                      project?.evaluation
                        ? tr('Relieve real', 'Calculated relief')
                        : tr('Muestra', 'Preview'),
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
                  title={tr('Ver triangulación UV', 'Show UV triangulation')}
                  aria-label={tr('Ver triangulación UV', 'Show UV triangulation')}
                  disabled={surface !== 'uv'}
                  onClick={() => setWire(!wire)}
                >
                  <GridFour size={17} />
                </button>
                <button
                  className="icon-button dark-button"
                  title={tr('Encuadrar modelo', 'Frame model')}
                  aria-label={tr('Encuadrar modelo', 'Frame model')}
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
                  <h2>{tr('Añade el modelo y sus texturas.', 'Add the model and its textures.')}</h2>
                  <p>
                    {tr(
                      'La IA conectará lo que ve con sus regiones UV.',
                      'The AI will match what it sees to the UV regions.',
                    )}
                  </p>
                  <button
                    className="button primary"
                    disabled={working}
                    onClick={() => input.current?.click()}
                  >
                    <UploadSimple size={17} />
                    {tr('Abrir modelo y texturas', 'Open model and textures')}
                  </button>
                  <button
                    className="demo-link"
                    disabled={working}
                    onClick={() =>
                      void run(tr('Abriendo ejemplo privado…', 'Opening example…'), async () => {
                        accept(await api<Project>('/demo', { method: 'POST' }));
                        await refreshProjects();
                        setSurface('model');
                      })
                    }
                  >
                    {tr('Abrir ejemplo', 'Open example')} <ArrowRight size={14} />
                  </button>
                  <span className="format-hint">
                    {tr('DAE / GLB + PNG · texturas hasta 8K', 'DAE / GLB + PNG · textures up to 8K')}
                  </span>
                </div>
              )}
              {project && surface !== 'figure' && (surface !== 'model' || project.models.length > 0) && (
                <>
                  <div className="view-label">
                    {surface === 'uv'
                      ? 'UV / 2D'
                      : surface === 'relief'
                        ? tr('MUESTRA DE RELIEVE', 'RELIEF SAMPLE')
                        : tr('PERSPECTIVA', 'PERSPECTIVE')}
                    <span>
                      {surface === 'relief'
                        ? tr('Vista aproximada sobre una placa', 'Approximate preview on a plate')
                        : mode === 'original'
                          ? tr('Textura original', 'Original texture')
                          : mode === 'ids'
                            ? tr('Regiones identificadas', 'Detected regions')
                            : mode === 'height'
                              ? tr('Alturas normalizadas', 'Normalised heights')
                              : tr('Paleta de relieve', 'Relief palette')}
                    </span>
                  </div>
                  <div className="stage-footer">
                    <span>
                      {surface === 'model'
                        ? tr(
                            'Arrastrar para girar · rueda para acercar · clic para seleccionar',
                            'Drag to rotate · scroll to zoom · click to select',
                          )
                        : surface === 'uv'
                          ? tr('Selecciona una zona de la textura', 'Select a region of the texture')
                          : tr(
                              'Previsualización local; calibra el resultado en Figure Tools',
                              'Local preview. Calibrate the result in Figure Tools.',
                            )}
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
                  aria-label={tr('Contenido de la vista', 'View content')}
                  value={mode}
                  onChange={(e) => setMode(e.target.value as ImageMode)}
                >
                  <option value="original">{tr('Original', 'Original')}</option>
                  <option value="ids" disabled={!count}>
                    {tr('Zonas numeradas', 'Numbered regions')}
                  </option>
                  <option value="color" disabled={!count}>
                    {tr('Colores de relieve', 'Relief colours')}
                  </option>
                  <option value="height" disabled={!count}>
                    {tr('Mapa de alturas', 'Height map')}
                  </option>
                </select>
              </div>
              {surface === 'relief' ? (
                <label className="strength-label">
                  {tr('Intensidad visual', 'Preview strength')}
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
              <span>{project ? <Check size={12} /> : 1}</span>
              {tr('Importar', 'Import')}
            </div>
            <div className={count ? 'complete' : ''}>
              <span>{count ? <Check size={12} /> : 2}</span>
              {tr('Preparar UV', 'Prepare UVs')}
            </div>
            <div className={project?.proposal ? 'complete' : ''}>
              <span>{project?.proposal ? <Check size={12} /> : 3}</span>
              {tr('Entender capas', 'Interpret layers')}
            </div>
            <div className={project?.approved ? 'complete' : ''}>
              <span>{project?.approved ? <Check size={12} /> : 4}</span>
              {tr('Revisar y exportar', 'Review and export')}
            </div>
          </div>
          {error && (
            <div className="error-banner" role="alert">
              <WarningCircle size={18} />
              <span>{error}</span>
              <button onClick={() => setError('')} aria-label={tr('Cerrar error', 'Dismiss error')}>
                {tr('Cerrar', 'Dismiss')}
              </button>
            </div>
          )}
        </main>
        <aside className="inspector">
          <div className="inspector-tabs">
            <button className={tab === 'regions' ? 'active' : ''} onClick={() => setTab('regions')}>
              {tr('Superficies', 'Surfaces')} <span>{count || '—'}</span>
            </button>
            <button
              className={tab === 'reasoning' ? 'active' : ''}
              onClick={() => setTab('reasoning')}
            >
              <Sparkle size={15} />
              {tr('Interpretación', 'Interpretation')}
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
                  <h2>{tr('Preparar regiones', 'Prepare regions')}</h2>
                  <p className="muted small">
                    {tr(
                      'Primero localizamos los contornos. La altura la decide el contexto del modelo.',
                      "First we find the outlines. Heights depend on the model's geometry.",
                    )}
                  </p>
                  <label className="field-row">
                    {tr('Colores candidatos', 'Candidate colours')} <span>{clusters}</span>
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
                    {tr('Detalle de análisis', 'Analysis detail')}
                    <select
                      value={resolution}
                      onChange={(e) => setResolution(+e.target.value)}
                      disabled={working}
                    >
                      <option value="512">{tr('Rápido · 512 px', 'Fast · 512 px')}</option>
                      <option value="768">{tr('Equilibrado · 768 px', 'Balanced · 768 px')}</option>
                      <option value="1024">{tr('Fino · 1024 px', 'Fine · 1024 px')}</option>
                      <option value="1536">{tr('Muy fino · 1536 px', 'Very fine · 1536 px')}</option>
                    </select>
                  </label>
                  <div className="split-actions">
                    <button
                      className="button primary"
                      disabled={!project?.assets.length || working}
                      onClick={() => void prepareAll()}
                    >
                      <Stack size={16} />
                      {ready
                        ? tr('Preparar pendientes', 'Prepare remaining textures')
                        : tr('Preparar texturas', 'Prepare textures')}
                    </button>
                    <button
                      className="icon-button outline"
                      aria-label={tr('Recalcular textura activa', 'Recalculate active texture')}
                      title={tr(
                        'Recalcular textura activa (restablece sus ajustes)',
                        'Recalculate active texture and reset its settings',
                      )}
                      disabled={!active || working}
                      onClick={() => void prepareActive()}
                    >
                      <ArrowCounterClockwise size={17} />
                    </button>
                  </div>
                  {active?.processingMs !== null && active?.processingMs !== undefined && (
                    <div className="timing">
                      {(active.processingMs / 1000).toFixed(2)}{' '}
                      s · {tr('análisis de', 'analysed at')} {active.workWidth} ×{' '}
                      {active.workHeight}
                    </div>
                  )}
                </section>
                <section className="inspector-section regions-section">
                  <div className="section-title">
                    <h2>
                      {active
                        ? tr('Zonas de esta textura', 'Regions in this texture')
                        : tr('Tus superficies', 'Your surfaces')}
                    </h2>
                    <span>{active?.regions.length ?? 0}</span>
                  </div>
                  {!active?.regions.length ? (
                    <div className="empty-regions">
                      <Stack size={30} weight="thin" />
                      <p>
                        {tr('Las zonas aparecerán aquí.', 'Regions will appear here.')}
                        <br />
                        {tr('Todas comienzan a la misma altura.', 'They all start at the same height.')}
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
                                ? tr('Altura editable', 'Editable height')
                                : r.geometry === 'modeled'
                                  ? tr('Volumen en geometría', 'Modelled volume')
                                  : r.geometry === 'painted'
                                    ? tr('Detalle de textura', 'Texture detail')
                                    : tr('Revisar interpretación', 'Review interpretation')}
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
                    <h2>{tr('Editar superficie', 'Edit surface')}</h2>
                    <label className="field-select">
                      {tr('Nombre', 'Name')}
                      <input
                        value={region.name}
                        onChange={(e) => updateRegion({ name: e.target.value })}
                        onBlur={() => void saveRegion()}
                        disabled={working}
                      />
                    </label>
                    {region.displacementColor && (
                      <label className="field-select">
                        {tr('Color de relieve', 'Relief colour')}
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
                          {tr(
                            'Este color controla el relieve en Blender. La altura numérica es solo una referencia.',
                            'This colour controls the relief in Blender. The numerical height is a reference.',
                          )}
                        </small>
                      </label>
                    )}
                    <label className="field-row">
                      {tr('Altura relativa', 'Relative height')} <span>{region.height} / 255</span>
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
                      <span>{tr('Hundido', 'Recessed')}</span>
                      <span>{tr('Referencia 128', 'Reference 128')}</span>
                      <span>{tr('Elevado', 'Raised')}</span>
                    </div>
                    <p className="region-reason">{region.reason}</p>
                    {region.confidence !== null && (
                      <p className="muted micro">
                        {tr('Confianza declarada por el modelo:', 'Confidence reported by the model:')}{' '}
                        {Math.round(region.confidence * 100)}{' '}
                        {tr(
                          ' %. No es una probabilidad calibrada.',
                          ' %. This is an uncalibrated estimate.',
                        )}
                      </p>
                    )}
                  </section>
                )}
              </>
            ) : (
              <>
                <section className="inspector-section">
                  <div className="section-title">
                    <h2>{tr('Entender la figura', 'Interpret figure')}</h2>
                    <Sparkle size={18} />
                  </div>
                  <p className="muted small">
                    {tr(
                      'La IA observa el modelo desde ambos lados, sus materiales y las regiones UV numeradas.',
                      'The AI examines the model from both sides, its materials and the numbered UV regions.',
                    )}
                  </p>
                  <label className="field-select">
                    {tr('Modelo de visión local', 'Local vision model')}
                    <select
                      value={model}
                      onChange={(e) => setModel(e.target.value)}
                      disabled={working}
                    >
                      {!models.length && (
                        <option value="">{tr('Sin modelos disponibles', 'No models available')}</option>
                      )}
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
                    {tr('Analizar modelo + UV', 'Analyse model + UVs')}
                  </button>
                  {!online && (
                    <button className="text-button" onClick={() => void refreshModels()}>
                      {tr('Volver a consultar el modelo global', 'Check the global model again')}
                    </button>
                  )}
                  {!project?.models.length && (
                    <p className="small muted">
                      {tr('Necesita un modelo DAE o GLB con UV.', 'Requires a DAE or GLB model with UVs.')}
                    </p>
                  )}
                </section>
                {job && ['running', 'queued'].includes(job.status) && (
                  <section className="inspector-section analysis-progress">
                    <SpinnerGap size={24} className="spin" />
                    <h3>{tr('Observando superficies', 'Examining surfaces')}</h3>
                    <p>{serverMessage(job.message)}</p>
                    <span>
                      {tr(
                        'El tiempo depende del modelo y la GPU.',
                        'The time depends on the model and GPU.',
                      )}
                    </span>
                  </section>
                )}
                {project?.evaluation?.sceneUnderstanding && (
                  <SceneReading scene={project.evaluation.sceneUnderstanding} />
                )}
                {project?.proposal ? (
                  <>
                    <section className="inspector-section">
                      <div className="section-title">
                        <h2>{tr('Lectura del modelo', 'Model interpretation')}</h2>
                        <span className="proposal-state">
                          {project.proposal.status === 'applied'
                            ? tr('Aplicada', 'Applied')
                            : project.proposal.status === 'stale'
                              ? tr('Desactualizada', 'Out of date')
                              : tr('Propuesta', 'Proposal')}
                        </span>
                      </div>
                      <p className="analysis-summary">{project.proposal.summary}</p>
                      <p className="muted micro">
                        {project.proposal.model} · {project.proposal.duration.toFixed(1)} s
                      </p>
                    </section>
                    <section className="inspector-section">
                      <h2>{tr('Relaciones de relieve', 'Relief relationships')}</h2>
                      <p className="muted small">
                        {tr(
                          'Activa solo las capas que necesitan volumen adicional sobre la malla.',
                          'Enable the layers that need extra volume above the mesh.',
                        )}
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
                                {tr('sobre', 'over')} {nameFor(r.lower)}
                              </span>
                              <small>{r.reason}</small>
                            </span>
                          </label>
                        ))
                      ) : (
                        <p className="empty-relations">
                          {tr(
                            'No se han propuesto capas superpuestas. Puedes ajustar alturas manualmente.',
                            'No overlapping layers were proposed. You can adjust heights manually.',
                          )}
                        </p>
                      )}
                      <button
                        className="button secondary wide"
                        disabled={working || project.proposal.status === 'stale'}
                        onClick={() =>
                          void run(tr('Aplicando relaciones…', 'Applying relationships…'), async () => {
                            accept(
                              await api<Project>(`/projects/${project.id}/apply`, {
                                method: 'POST',
                                body: JSON.stringify({ relations }),
                              }),
                            );
                            setMode('color');
                            setToast(
                              tr(
                                'Relaciones aplicadas. Revisa el relieve y ajusta las zonas.',
                                'Relationships applied. Review the relief and adjust the regions.',
                              ),
                            );
                          })
                        }
                      >
                        <Check size={16} />
                        {tr('Aplicar propuesta revisada', 'Apply reviewed proposal')}
                      </button>
                    </section>
                    {!!project.proposal.warnings.length && (
                      <section className="inspector-section">
                        <h2>{tr('Para revisar', 'To review')}</h2>
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
                        <span>{tr('Hebilla', 'Buckle')}</span>
                        <span>{tr('Cinturón', 'Belt')}</span>
                        <span>{tr('Pantalón', 'Trousers')}</span>
                      </div>
                      <h3>{tr('El porqué de cada altura.', 'How heights are assigned')}</h3>
                      <p>
                        {tr(
                          'Una hebilla puede ir sobre un cinturón. Si ese volumen ya existe en la malla, no hace falta duplicarlo.',
                          'A buckle can sit over a belt. Existing volume in the mesh needs no added relief.',
                        )}
                      </p>
                      <small>
                        {tr(
                          'Ejemplo de relación, no análisis de tu figura.',
                          'Example of a relationship for illustration.',
                        )}
                      </small>
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
                void run(tr('Guardando ejemplo revisado…', 'Saving reviewed example…'), async () => {
                  accept(
                    await api<Project>(`/projects/${project!.id}/approve`, {
                      method: 'POST',
                    }),
                  );
                  setToast(
                    tr(
                      'Correcciones guardadas como ejemplo para futuros análisis.',
                      'Corrections saved as an example for future analyses.',
                    ),
                  );
                })
              }
            >
              <Check size={17} />
              {project?.approved
                ? tr('Revisión guardada', 'Review saved')
                : tr('Guardar como revisado', 'Save as reviewed')}
            </button>
            <p>
              {tr(
                'Las correcciones crean tu biblioteca de ejemplos.',
                'Saved corrections become examples for later analyses.',
              )}
            </p>
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
              {tr('Análisis de visión en curso', 'Vision analysis in progress')}
            </>
          ) : (
            <>
              <span className="status-dot" />
              {tr('Taller listo', 'Workshop ready')}
            </>
          )}
        </span>
        <span>
          {count
            ? tr(
                `${count} zonas · ${ready} texturas preparadas`,
                `${count} regions · ${ready} textures prepared`,
              )
            : tr('Procesamiento local', 'Local processing')}
          <span className="footer-separator">/</span>
          {tr('Salida a resolución original', 'Output at original resolution')}
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
