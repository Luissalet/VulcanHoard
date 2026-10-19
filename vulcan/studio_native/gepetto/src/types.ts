export type Region = {
  id: number;
  cluster: number;
  name: string;
  height: number;
  color: number[];
  displacementColor?: number[] | null;
  area: number;
  center: number[];
  bbox: number[];
  confidence: number | null;
  reason: string;
  geometry: 'painted' | 'modeled' | 'unknown';
  semanticGroup?: string;
  heightGroup?: string;
  scenePart?: string | null;
};
export type SceneUnderstanding = {
  parts: {
    key: string;
    name: string;
    appearance: string;
    location: string;
    representation: 'modeled' | 'painted' | 'mixed' | 'uncertain';
    controlEvidence: string;
    printRequirement: string;
  }[];
  relations: {
    part: string;
    reference: string;
    relation: 'covers' | 'inside' | 'borders' | 'continues' | 'separate';
    evidence: string;
  }[];
  uncertainties: string[];
};
export type Asset = {
  id: string;
  name: string;
  file: string;
  width: number;
  height: number;
  workWidth: number;
  workHeight: number;
  regions: Region[];
  version: number;
  processingMs: number | null;
  approved: boolean;
  material?: string;
  sourcePath?: string;
};
export type ModelFile = { id: string; name: string; file: string };
export type Relation = { upper: string; lower: string; reason: string; apply: boolean };
export type Proposal = {
  summary: string;
  regions: { key: string; name: string; reason: string; confidence: number; geometry: string }[];
  relations: Relation[];
  warnings: string[];
  model: string;
  duration: number;
  status: 'pending' | 'applied' | 'stale';
};
export type Project = {
  id: string;
  name: string;
  assets: Asset[];
  models: ModelFile[];
  proposal: Proposal | null;
  updated: number;
  revision: number;
  approved: boolean;
  history: unknown[];
  blenderSource?: ModelFile;
  blendImport?: { objects: string[]; warnings: string[]; seconds: number };
  evaluation?: {
    id: string;
    final: number;
    aiAcceptable: boolean;
    hasDetails?: boolean;
    hasFinished?: boolean;
    targetMaterials?: string[];
    revision: number;
    review: { assessment: string; issues: string[] };
    sceneUnderstanding?: SceneUnderstanding | null;
  };
};
export type ImageMode = 'original' | 'ids' | 'color' | 'height';
export type Surface = 'model' | 'uv' | 'relief' | 'figure';
export const PALETTE = [
  '#a799e1',
  '#add4a3',
  '#eaa9b7',
  '#e6c374',
  '#82bfcd',
  '#e49a70',
  '#bcb5a6',
  '#8da4d7',
];
export const imageUrl = (p: Project, a: Asset, mode: ImageMode) =>
  `/api/projects/${p.id}/assets/${a.id}/${mode}.png?v=${a.version}`;
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, {
    ...options,
    headers:
      options.body instanceof FormData
        ? options.headers
        : { 'Content-Type': 'application/json', ...options.headers },
  });
  if (!response.ok) {
    const e = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(e.detail || 'No se pudo completar la operación.');
  }
  return response.json();
}
