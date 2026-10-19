// What the Faustus copilot panel (launcher) asks this app for: the real objects open in the view.
// The launcher is injected by the server (`install_copilot`); this module only answers `window.hoardCopilot.context()`
// and tells the app to refresh after an agent turn. It never throws and never rejects.
import type { Project, Region } from './types';

const REGION_FIELDS = ['name', 'height', 'displacementColor'] as const;
export function changedRegionFields(base: Region | undefined, current: Region) {
  return Object.fromEntries(REGION_FIELDS.filter((key) =>
    JSON.stringify(base?.[key] ?? null) !== JSON.stringify(current[key] ?? null),
  ).map((key) => [key, current[key] ?? null]));
}

export function rebaseProject(base: Project | null, fresh: Project, current: Project | null): Project {
  if (base?.id !== fresh.id || current?.id !== fresh.id) return fresh;
  return { ...fresh, assets: fresh.assets.map((asset) => ({ ...asset, regions: asset.regions.map((region) => {
    const before = base.assets.find((a) => a.id === asset.id)?.regions.find((r) => r.id === region.id);
    const draft = current.assets.find((a) => a.id === asset.id)?.regions.find((r) => r.id === region.id);
    return before && draft ? { ...region, ...changedRegionFields(before, draft) } : region;
  }) })) };
}

export type CopilotView = {
  projectId: string | null;
  assetId: string | null;
  regionId: number | null;
  surface: string;
  mode: string;
  tab: string;
  panel: 'workspace' | 'control' | 'pose';
  regionDraft: { name: string; height: number; displacementColor?: number[] | null } | null;
  commandDraft: string;
  regionChanges?: Record<string, unknown>;
};

export const view: CopilotView = {
  projectId: null,
  assetId: null,
  regionId: null,
  surface: 'model',
  mode: 'original',
  tab: 'regions',
  panel: 'workspace',
  regionDraft: null,
  commandDraft: '',
};

type Loader = (path: string) => Promise<unknown>;

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** Pure: the context additions for a view, reading real data through `load` (the app's own API). */
export async function buildContext(
  currentState: CopilotView,
  load: Loader,
  fallbackProjectId: string | null = null,
) {
  const state = structuredClone(currentState);
  const page =
    state.panel === 'workspace' ? (state.projectId ? 'project' : 'library') : state.panel;
  const out: {
    project?: Record<string, unknown>;
    active_objects: Record<string, unknown>[];
    view: Record<string, unknown>;
  } = {
    active_objects: [],
    view: { page, surface: state.surface, image_mode: state.mode, inspector_tab: state.tab },
  };
  const id = state.projectId || fallbackProjectId;
  let project: Project | null = null;
  if (id && /^[a-f0-9]{12}$/.test(id)) {
    try {
      project = (await load(`/api/projects/${id}`)) as Project;
    } catch {
      project = null;
    }
  }
  if (!project || !Array.isArray(project.assets)) return out;
  const textures = project.assets.length;
  out.project = {
    external_project_id: project.id,
    title: project.name,
    textures,
    models: project.models?.length ?? 0,
    revision: project.revision,
  };
  out.active_objects.push({
    kind: 'project',
    object_id: project.id,
    title: project.name,
    textures,
    prepared_textures: project.assets.filter((a) => a.regions?.length).length,
    revision: project.revision,
    approved: !!project.approved,
    has_proposal: !!project.proposal,
    has_evaluation: !!project.evaluation,
  });
  const asset = project.assets.find((a) => a.id === state.assetId) ?? project.assets[0];
  if (asset) {
    out.active_objects.push({
      kind: 'texture',
      object_id: asset.id,
      title: asset.name,
      regions: asset.regions?.length ?? 0,
      approved: !!asset.approved,
      material: asset.material ?? null,
    });
    const region = asset.regions?.find((r) => r.id === state.regionId);
    if (region) {
      out.active_objects.push({
        kind: 'region',
        object_id: `${asset.id}:${region.id}`,
        title: region.name,
        height: region.height,
        geometry: region.geometry,
        confidence: region.confidence,
      });
      const draft = state.regionDraft;
      const saved = {
        name: region.name,
        height: region.height,
        displacementColor: region.displacementColor ?? null,
      };
      const changes = state.regionChanges;
      if (changes !== undefined ? Object.keys(changes).length > 0 : draft && !same({ ...draft, displacementColor: draft.displacementColor ?? null }, saved)) {
        out.view.editor = { kind: 'region', dirty: true, fields: changes ?? { ...draft }, saved };
      }
    }
  }
  if (project.evaluation) {
    out.active_objects.push({
      kind: 'evaluation',
      object_id: project.evaluation.id,
      title: 'Figure evaluation',
      final: project.evaluation.final,
      ai_acceptable: project.evaluation.aiAcceptable,
    });
  }
  if (state.commandDraft.trim()) {
    out.view.editor = out.view.editor ?? {
      kind: 'command',
      dirty: true,
      fields: { instruction: state.commandDraft.slice(0, 2000) },
    };
  }
  return out;
}

const listeners = new Set<() => void>();
/** The app subscribes here to refresh its project list and the open project after an agent turn. */
export function onAgentChange(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** The app reports what is on screen; a changed project announces itself to the launcher. */
export function reportView(patch: Partial<CopilotView>) {
  const before = view.projectId;
  Object.assign(view, patch);
  if (typeof window !== 'undefined' && view.projectId !== before)
    window.dispatchEvent(new Event('hoard:copilot-context-changed'));
}

export function installHoardCopilot() {
  if (typeof window === 'undefined') return;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const load: Loader = async (path) => {
    const response = await fetch(path);
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  };
  (window as unknown as { hoardCopilot: unknown }).hoardCopilot = {
    async context() {
      try {
        const fallback = new URLSearchParams(location.search).get('project');
        return await buildContext(view, load, fallback);
      } catch {
        return { active_objects: [] };
      }
    },
    onTurnEnd() {
      clearTimeout(timer);
      timer = setTimeout(() => listeners.forEach((fn) => fn()), 250);
    },
  };
}
