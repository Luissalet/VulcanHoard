import { useEffect, useImperativeHandle, useRef, forwardRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { ColladaLoader } from 'three/addons/loaders/ColladaLoader.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { imageUrl } from './types';
import type { Asset, ImageMode, Project, Surface } from './types';

type MeshInfo = {
  name: string;
  material: string;
  texture: string | null;
  vertices: number;
  uv: boolean;
  bounds: number[];
  regionSamples?: { key: string; samples: number; center: number[]; bounds: number[] }[];
};
export type ViewportHandle = {
  capture: () => Promise<{ views: { label: string; image: string }[]; meshes: MeshInfo[] }>;
  reset: () => void;
  uvLines: (aid: string) => number[][];
};
type Props = {
  project: Project | null;
  asset: Asset | null;
  selected: number | null;
  mode: ImageMode;
  surface: Surface;
  strength: number;
  onPick: (aid: string, u: number, v: number) => void;
  onStatus: (s: string) => void;
};
type Engine = {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  root: THREE.Group;
  grid: THREE.GridHelper;
  plane: THREE.Mesh;
  size: number;
  render: () => void;
  dispose: () => void;
  textures: Map<string, THREE.Texture>;
  loaded: boolean;
};

function disposeObject(root: THREE.Object3D) {
  root.traverse((o) => {
    if (o instanceof THREE.Mesh) {
      o.geometry.dispose();
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of mats) m.dispose();
    }
  });
}
function basename(url: string) {
  try {
    return decodeURIComponent(url)
      .replaceAll('\\', '/')
      .split('/')
      .pop()!
      .split('?')[0]
      .toLowerCase();
  } catch {
    return url.toLowerCase();
  }
}
function textureStem(name: string) {
  return basename(name)
    .replace(/\.(png|jpe?g|webp|tga|bmp)$/, '')
    .replace(/\.\d{3}$/, '');
}
function materialStem(name: string) {
  return textureStem(name).replace(/_(alb|col).*$/, '');
}

export default forwardRef<ViewportHandle, Props>(function Viewport(props, ref) {
  const container = useRef<HTMLDivElement>(null);
  const engine = useRef<Engine | null>(null);
  const current = useRef(props);
  const lastSurface = useRef(props.surface);
  current.current = props;
  const modelKey = props.project?.models.map((m) => m.id).join(',') ?? '';
  async function texture(
    e: Engine,
    p: Project,
    a: Asset,
    mode: ImageMode,
    flipY = true,
    selection: number | null = null,
  ) {
    const actual = mode === 'original' || a.regions.length === 0 ? 'original' : mode;
    const url = imageUrl(p, a, actual);
    const key = url + ':' + flipY + ':' + selection;
    if (e.textures.has(key)) return e.textures.get(key)!;
    const t = await new THREE.TextureLoader().loadAsync(url);
    t.flipY = flipY;
    t.colorSpace = actual === 'height' ? THREE.NoColorSpace : THREE.SRGBColorSpace;
    if (selection !== null && a.regions.length) {
      const response = await fetch(`/api/projects/${p.id}/assets/${a.id}/mask?v=${a.version}`);
      if (response.ok) {
        const labels = new Int16Array(await response.arrayBuffer());
        const canvas = document.createElement('canvas');
        canvas.width = a.workWidth;
        canvas.height = a.workHeight;
        const ctx = canvas.getContext('2d')!;
        ctx.drawImage(t.image, 0, 0, canvas.width, canvas.height);
        const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
        for (let i = 0; i < labels.length; i++) {
          const selected = labels[i] === selection;
          for (let c = 0; c < 3; c++)
            pixels.data[i * 4 + c] = Math.round(
              selected
                ? pixels.data[i * 4 + c] * 0.65 + [255, 235, 167][c] * 0.35
                : pixels.data[i * 4 + c] * 0.34,
            );
        }
        ctx.putImageData(pixels, 0, 0);
        t.image = canvas;
        t.needsUpdate = true;
      }
    }
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.anisotropy = Math.min(4, e.renderer.capabilities.getMaxAnisotropy());
    e.textures.set(key, t);
    if (e.textures.size > 40) {
      const oldest = e.textures.keys().next().value!;
      e.textures.get(oldest)?.dispose();
      e.textures.delete(oldest);
    }
    return t;
  }
  async function setMaterials(mode: ImageMode, highlight = false) {
    const e = engine.current,
      p = current.current.project;
    if (!e || !p) return;
    const tasks: Promise<void>[] = [];
    e.root.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const mat of mats) {
        const m = mat as THREE.MeshStandardMaterial;
        const aid = m.userData.asset;
        const a = p.assets.find((a) => a.id === aid);
        if (!a) continue;
        const select =
          highlight && a.id === current.current.asset?.id ? current.current.selected : null;
        tasks.push(
          texture(e, p, a, mode, m.userData.flipY ?? true, select).then((t) => {
            m.map = t;
            m.color.set(
              highlight && current.current.selected !== null && a.id !== current.current.asset?.id
                ? 0x777777
                : 0xffffff,
            );
            m.needsUpdate = true;
          }),
        );
      }
    });
    await Promise.all(tasks);
  }
  function metadata() {
    const e = engine.current;
    const rows: MeshInfo[] = [];
    if (!e) return rows;
    e.root.updateMatrixWorld(true);
    e.root.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const bounds = new THREE.Box3().setFromObject(o);
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      mats.forEach((m) =>
        rows.push({
          name: o.name,
          material: m.name,
          texture: m.userData.asset ?? null,
          vertices: o.geometry.attributes.position.count,
          uv: !!o.geometry.attributes.uv,
          bounds: [...bounds.min.toArray(), ...bounds.max.toArray()].map((v) => +v.toFixed(4)),
        }),
      );
    });
    return rows;
  }
  async function groundedMetadata() {
    const e = engine.current,
      p = current.current.project;
    const rows = metadata();
    if (!e || !p) return rows;
    const masks = new Map<string, Int16Array>();
    await Promise.all(
      p.assets
        .filter((a) => a.regions.length)
        .map(async (a) => {
          const r = await fetch(`/api/projects/${p.id}/assets/${a.id}/mask?v=${a.version}`);
          if (r.ok) masks.set(a.id, new Int16Array(await r.arrayBuffer()));
        }),
    );
    e.root.updateMatrixWorld(true);
    e.root.traverse((o) => {
      if (!(o instanceof THREE.Mesh)) return;
      const g = o.geometry,
        pos = g.attributes.position,
        uv = g.attributes.uv;
      if (!uv) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      const groups = g.groups.length
        ? g.groups
        : [{ start: 0, count: g.index?.count ?? pos.count, materialIndex: 0 }];
      for (const group of groups) {
        const mat = mats[group.materialIndex ?? 0];
        if (!mat) continue;
        const a = p.assets.find((a) => a.id === mat.userData.asset),
          mask = a ? masks.get(a.id) : null;
        if (!a || !mask) continue;
        const accumulated = new Map<
          number,
          { n: number; sum: THREE.Vector3; bounds: THREE.Box3 }
        >();
        for (let i = group.start; i < group.start + group.count; i += 3) {
          const ids = [0, 1, 2].map((j) => (g.index ? g.index.getX(i + j) : i + j));
          const ps = ids.map((j) =>
            new THREE.Vector3().fromBufferAttribute(pos, j).applyMatrix4(o.matrixWorld),
          );
          for (const weights of [
            [2 / 3, 1 / 6, 1 / 6],
            [1 / 6, 2 / 3, 1 / 6],
            [1 / 6, 1 / 6, 2 / 3],
          ]) {
            let u = 0,
              v = 0;
            const xyz = new THREE.Vector3();
            ids.forEach((j, k) => {
              u += uv.getX(j) * weights[k];
              v += uv.getY(j) * weights[k];
              xyz.addScaledVector(ps[k], weights[k]);
            });
            u = ((u % 1) + 1) % 1;
            v = ((v % 1) + 1) % 1;
            if (mat.userData.flipY !== false) v = 1 - v;
            const rid =
              mask[
                Math.min(a.workHeight - 1, Math.floor(v * a.workHeight)) * a.workWidth +
                  Math.min(a.workWidth - 1, Math.floor(u * a.workWidth))
              ];
            if (rid < 0) continue;
            const item = accumulated.get(rid) ?? {
              n: 0,
              sum: new THREE.Vector3(),
              bounds: new THREE.Box3(),
            };
            item.n++;
            item.sum.add(xyz);
            item.bounds.expandByPoint(xyz);
            accumulated.set(rid, item);
          }
        }
        const row = rows.find((row) => row.name === o.name && row.texture === a.id);
        if (row)
          row.regionSamples = a.regions.map((r) => {
            const s = accumulated.get(r.id);
            return {
              key: `${a.id}:${r.id}`,
              samples: s?.n ?? 0,
              center: s
                ? s.sum
                    .divideScalar(s.n)
                    .toArray()
                    .map((v) => +(v / e.size).toFixed(3))
                : [],
              bounds: s
                ? [...s.bounds.min.toArray(), ...s.bounds.max.toArray()].map(
                    (v) => +(v / e.size).toFixed(3),
                  )
                : [],
            };
          });
      }
    });
    return rows;
  }
  useImperativeHandle(ref, () => ({
    reset() {
      const e = engine.current;
      if (!e) return;
      e.camera.position.set(e.size * 1.25, e.size * 0.8, e.size * 1.8);
      e.controls.target.set(0, 0, 0);
      e.controls.update();
      e.render();
    },
    uvLines(aid) {
      const e = engine.current;
      const lines: number[][] = [];
      e?.root.traverse((o) => {
        if (!(o instanceof THREE.Mesh)) return;
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        if (!mats.some((m) => m.userData.asset === aid)) return;
        const uv = o.geometry.attributes.uv;
        if (!uv) return;
        const idx = o.geometry.index;
        const count = idx ? idx.count : uv.count;
        const flip = mats.find((m) => m.userData.asset === aid)?.userData.flipY ?? true;
        const vy = (i: number) => (flip ? uv.getY(i) : 1 - uv.getY(i));
        for (let i = 0; i < count && lines.length < 15000; i += 3) {
          const a = idx ? idx.getX(i) : i,
            b = idx ? idx.getX(i + 1) : i + 1,
            c = idx ? idx.getX(i + 2) : i + 2;
          lines.push([uv.getX(a), vy(a), uv.getX(b), vy(b), uv.getX(c), vy(c)]);
        }
      });
      return lines;
    },
    async capture() {
      const e = engine.current;
      if (!e || !e.loaded || !current.current.project?.models.length)
        throw new Error('Carga un modelo 3D y espera a que termine de abrirse.');
      const oldPosition = e.camera.position.clone(),
        oldTarget = e.controls.target.clone();
      const oldAspect = e.camera.aspect;
      const size = e.renderer.getSize(new THREE.Vector2());
      const ratio = e.renderer.getPixelRatio();
      const gridVisible = e.grid.visible,
        planeVisible = e.plane.visible,
        rootVisible = e.root.visible;
      const views: { label: string; image: string }[] = [];
      try {
        e.root.visible = true;
        e.grid.visible = false;
        e.plane.visible = false;
        e.renderer.setPixelRatio(1);
        e.renderer.setSize(640, 640, false);
        e.camera.aspect = 1;
        e.camera.updateProjectionMatrix();
        e.controls.target.set(0, 0, 0);
        for (const mode of ['original', 'ids'] as ImageMode[]) {
          await setMaterials(mode);
          for (const [label, angle] of [
            ['Frente', 0],
            ['Dorso', Math.PI],
          ] as const) {
            e.camera.position.set(
              Math.sin(angle + 0.22) * e.size * 1.8,
              e.size * 0.3,
              Math.cos(angle + 0.22) * e.size * 1.8,
            );
            e.camera.lookAt(0, 0, 0);
            e.renderer.render(e.scene, e.camera);
            views.push({
              label: `${label} / ${mode === 'original' ? 'textura original' : 'regiones numeradas'}`,
              image: e.renderer.domElement.toDataURL('image/png'),
            });
          }
        }
      } finally {
        await setMaterials(current.current.mode, true);
        e.renderer.setPixelRatio(ratio);
        e.renderer.setSize(size.x, size.y, false);
        e.camera.aspect = oldAspect;
        e.camera.updateProjectionMatrix();
        e.camera.position.copy(oldPosition);
        e.controls.target.copy(oldTarget);
        e.grid.visible = gridVisible;
        e.plane.visible = planeVisible;
        e.root.visible = rootVisible;
        e.controls.update();
        e.render();
      }
      return { views, meshes: await groundedMetadata() };
    },
  }));

  useEffect(() => {
    if (!container.current) return;
    const host = container.current;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: false,
        preserveDrawingBuffer: true,
      });
    } catch {
      props.onStatus('WebGL no está disponible. Puedes usar el editor UV.');
      return;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
    renderer.setClearColor(0x272c2b);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.25;
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 10000);
    camera.position.set(2, 1.4, 3);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.minDistance = 0.05;
    controls.maxDistance = 100;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x657366, 2.6));
    const key = new THREE.DirectionalLight(0xfff5e6, 3.8);
    key.position.set(3, 6, 4);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xc4d9ec, 2);
    fill.position.set(-4, 1, -2);
    scene.add(fill);
    const root = new THREE.Group();
    scene.add(root);
    const grid = new THREE.GridHelper(10, 40, 0x555d58, 0x373e3a);
    grid.position.y = -0.85;
    scene.add(grid);
    const plane = new THREE.Mesh(
      new THREE.PlaneGeometry(1.8, 1.8, 256, 256),
      new THREE.MeshStandardMaterial({ color: 0xe1ddd1, roughness: 0.85, side: THREE.DoubleSide }),
    );
    plane.rotation.x = -0.7;
    plane.visible = false;
    scene.add(plane);
    const render = () => renderer.render(scene, camera);
    controls.addEventListener('change', render);
    const resize = new ResizeObserver(() => {
      const w = host.clientWidth,
        h = host.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      render();
    });
    resize.observe(host);
    let down = [0, 0];
    const pointerDown = (event: PointerEvent) => {
      down = [event.clientX, event.clientY];
    };
    const pointerUp = (event: PointerEvent) => {
      if (
        Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5 ||
        current.current.surface !== 'model'
      )
        return;
      const box = renderer.domElement.getBoundingClientRect();
      const ray = new THREE.Raycaster();
      ray.setFromCamera(
        new THREE.Vector2(
          ((event.clientX - box.left) / box.width) * 2 - 1,
          (-(event.clientY - box.top) / box.height) * 2 + 1,
        ),
        camera,
      );
      const hit = ray.intersectObject(root, true).find((h) => h.uv);
      if (hit && hit.object instanceof THREE.Mesh && hit.uv) {
        const mats = Array.isArray(hit.object.material)
          ? hit.object.material
          : [hit.object.material];
        const mat = mats[hit.face?.materialIndex ?? 0];
        const vy = ((hit.uv.y % 1) + 1) % 1;
        if (mat?.userData.asset)
          current.current.onPick(
            mat.userData.asset,
            ((hit.uv.x % 1) + 1) % 1,
            mat.userData.flipY === false ? vy : 1 - vy,
          );
      }
    };
    renderer.domElement.addEventListener('pointerdown', pointerDown);
    renderer.domElement.addEventListener('pointerup', pointerUp);
    const e: Engine = {
      renderer,
      scene,
      camera,
      controls,
      root,
      grid,
      plane,
      size: 1.5,
      render,
      textures: new Map(),
      loaded: false,
      dispose() {
        resize.disconnect();
        controls.dispose();
        renderer.dispose();
        disposeObject(root);
        disposeObject(plane);
        e.textures.forEach((t) => t.dispose());
        grid.geometry.dispose();
        (grid.material as THREE.Material).dispose();
        host.removeChild(renderer.domElement);
      },
    };
    engine.current = e;
    render();
    return () => {
      engine.current = null;
      e.dispose();
    };
    // Scene construction is independent of project updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const e = engine.current,
      p = current.current.project;
    if (!e) return;
    let cancelled = false;
    e.loaded = false;
    disposeObject(e.root);
    e.root.clear();
    e.root.position.set(0, 0, 0);
    if (!p?.models.length) {
      current.current.onStatus('Abre un proyecto Blender para ver su modelo y texturas aplicadas.');
      e.render();
      return;
    }
    current.current.onStatus('Abriendo modelo…');
    const manager = new THREE.LoadingManager();
    const pending = new Map<string, THREE.Texture>();
    // A missing file must not become an uninitialized alpha texture: alphaTest
    // would discard every fragment and make an otherwise valid mesh disappear.
    const missingImages = new Set<string>();
    const neutralImage =
      'data:image/svg+xml,' +
      encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><path fill="white" d="M0 0h1v1H0z"/></svg>',
      );
    manager.setURLModifier((url) => {
      if (/^(data:|blob:)/i.test(url)) return url;
      const name = basename(url);
      const a = p.assets.find((a) => a.name.toLowerCase() === name);
      if (a) return imageUrl(p, a, 'original');
      if (/\.(png|jpe?g|webp|tga|bmp)$/i.test(name)) {
        missingImages.add(name);
        return neutralImage;
      }
      return url;
    });
    manager.onError = (url) => missingImages.add(basename(url));
    async function load() {
      const loaded: THREE.Object3D[] = [];
      for (const m of p!.models) {
        const url = `/api/projects/${p!.id}/source/${m.id}`;
        let obj: THREE.Object3D;
        if (m.file.endsWith('.dae')) {
          const text = await fetch(url).then((r) => r.text());
          const data = new ColladaLoader(manager).parse(text, '/');
          obj = data.scene;
        } else {
          obj = (await new GLTFLoader(manager).loadAsync(url)).scene;
        }
        if (cancelled) {
          disposeObject(obj);
          return;
        }
        loaded.push(obj);
        e!.root.add(obj);
      }
      // Let image-loader callbacks finish before matching material sources.
      await Promise.all(
        p!.assets.map(async (a) => {
          pending.set(a.id, await texture(e!, p!, a, 'original'));
        }),
      );
      if (cancelled) return;
      for (const obj of loaded)
        obj.traverse((o) => {
          if (!(o instanceof THREE.Mesh)) return;
          const materials = Array.isArray(o.material) ? o.material : [o.material];
          const replacements = materials.map((old) => {
            const previous = old as THREE.MeshStandardMaterial;
            const src = previous.map?.image?.src ?? '';
            // Blender copies append .001 to materials and glTF drops image extensions.
            // Prefer source identity, then exact image stems, then unique material stems.
            let matches = p!.assets.filter(
              (a) => src.includes(`/assets/${a.id}/`) || basename(src) === a.name.toLowerCase(),
            );
            const assignedMaterial = previous.userData.sculptorsHoardMaterial;
            if (assignedMaterial)
              matches = p!.assets.filter((a) => a.material === assignedMaterial);
            if (!matches.length && previous.map?.name)
              matches = p!.assets.filter(
                (a) => textureStem(previous.map!.name) === textureStem(a.name),
              );
            if (!matches.length)
              matches = p!.assets.filter((a) => materialStem(old.name) === materialStem(a.name));
            const a = matches.length === 1 ? matches[0] : undefined;
            const mat = new THREE.MeshStandardMaterial({
              name: old.name,
              map: a ? pending.get(a.id) : previous.map?.image ? previous.map : null,
              color:
                a || previous.map?.image
                  ? (previous.color ?? new THREE.Color(0xffffff))
                  : new THREE.Color(0xd8d5cb),
              roughness: 0.8,
              metalness: 0,
              side: THREE.DoubleSide,
              transparent: previous.transparent,
              alphaTest: 0.35,
            });
            mat.userData.flipY = previous.map?.flipY ?? true;
            if (a) mat.userData.asset = a.id;
            old.dispose();
            return mat;
          });
          o.material = Array.isArray(o.material) ? replacements : replacements[0];
          o.frustumCulled = false;
        });
      e!.root.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(e!.root);
      const extent = box.getSize(new THREE.Vector3()),
        center = box.getCenter(new THREE.Vector3());
      const max = Math.max(extent.x, extent.y, extent.z) || 1;
      e!.root.position.sub(center);
      e!.size = max;
      e!.camera.near = max / 1000;
      e!.camera.far = max * 100;
      e!.camera.updateProjectionMatrix();
      e!.camera.position.set(max * 0.95, max * 0.45, max * 1.7);
      e!.controls.target.set(0, 0, 0);
      e!.controls.maxDistance = max * 12;
      e!.controls.minDistance = max * 0.25;
      e!.grid.position.y = -extent.y / 2 - 0.002;
      e!.grid.scale.setScalar(max / 3);
      e!.controls.update();
      e!.loaded = true;
      await setMaterials(current.current.mode);
      e!.render();
      const rows = metadata();
      const missing = rows.filter((m) => !m.texture).length;
      current.current.onStatus(
        missing
          ? `${rows.length} superficies visibles · ${missing} sin textura vinculada. Importa la carpeta del modelo con sus imágenes.`
          : `${rows.length} superficies · UV vinculadas`,
      );
    }
    load().catch((err) => {
      if (!cancelled) current.current.onStatus(`No se pudo abrir el modelo: ${err.message}`);
    });
    return () => {
      cancelled = true;
    };
    // Reload geometry only when source inventory changes, not on every height adjustment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.project?.id, modelKey, props.project?.assets.map((a) => a.id).join(',')]);

  useEffect(() => {
    const e = engine.current,
      p = props.project;
    if (!e) return;
    let cancelled = false;
    const previousSurface = lastSurface.current;
    lastSurface.current = props.surface;
    e.root.visible = props.surface === 'model';
    e.plane.visible = props.surface === 'relief';
    e.grid.visible = props.surface !== 'uv';
    e.render();
    async function update() {
      if (props.surface === 'model') {
        if (previousSurface === 'relief') {
          e!.camera.position.set(e!.size * 0.95, e!.size * 0.45, e!.size * 1.7);
          e!.camera.near = e!.size / 1000;
          e!.camera.far = e!.size * 100;
          e!.camera.updateProjectionMatrix();
          e!.controls.minDistance = e!.size * 0.25;
          e!.controls.maxDistance = e!.size * 12;
          e!.grid.scale.setScalar(e!.size / 3);
          e!.controls.update();
        }
        await setMaterials(props.mode, true);
      }
      if (props.surface === 'relief' && p && props.asset) {
        const a = props.asset,
          mat = e!.plane.material as THREE.MeshStandardMaterial;
        const t = await texture(e!, p, a, a.regions.length ? 'height' : 'original');
        if (cancelled) return;
        mat.map = null;
        mat.displacementMap = a.regions.length ? t : null;
        mat.displacementScale = props.strength * 0.16;
        mat.displacementBias = -props.strength * 0.08;
        mat.bumpMap = a.regions.length ? t : null;
        mat.bumpScale = props.strength * 0.09;
        mat.needsUpdate = true;
        e!.plane.scale.set(a.width / a.height, 1, 1);
        e!.camera.position.set(1.4, 1.4, 2.7);
        e!.camera.near = 0.01;
        e!.camera.far = 100;
        e!.camera.updateProjectionMatrix();
        e!.controls.target.set(0, 0, 0);
        e!.controls.minDistance = 0.3;
        e!.controls.maxDistance = 15;
        e!.grid.position.y = -0.75;
        e!.grid.scale.setScalar(0.5);
        e!.controls.update();
      }
      if (!cancelled) {
        e!.render();
        e!.renderer.domElement.dataset.readyState = `${props.surface}:${props.selected}`;
      }
    }
    update().catch((err) => current.current.onStatus(err.message));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    props.mode,
    props.surface,
    props.asset?.id,
    props.selected,
    props.project?.assets.map((a) => a.version).join(','),
    props.strength,
  ]);
  return (
    <div
      ref={container}
      className="webgl"
      aria-label="Visor 3D. Arrastra para girar, rueda para acercar y pulsa una superficie para seleccionarla."
    />
  );
});
