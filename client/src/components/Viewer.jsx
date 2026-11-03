import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";

const MATERIAL = () => new THREE.MeshStandardMaterial({ color: 0xd6cdc0, metalness: 0.05, roughness: 0.65, side: THREE.DoubleSide });
export const DENSE_TRIANGLES = 300_000; // from here on the dense (virtual geometry) mode is offered when WebGPU exists
export const WEB_GLB_TO_MM = 1000; // web GLBs are written in metres (glTF units); shown at the library's millimetre scale
export const hasWebGPU = () => typeof navigator !== "undefined" && !!navigator.gpu;

function load(format, url) {
  return new Promise((resolve, reject) => {
    const onError = (e) => reject(new Error(e?.message || "No se pudo leer el archivo."));
    if (format === "stl") new STLLoader().load(url, (geometry) => resolve(new THREE.Mesh(geometry, MATERIAL())), undefined, onError);
    else if (format === "obj") new OBJLoader().load(url, (group) => resolve(group), undefined, onError);
    else if (format === "3mf") new ThreeMFLoader().load(url, (group) => resolve(group), undefined, onError);
    else if (format === "glb") new GLTFLoader().setMeshoptDecoder(MeshoptDecoder).load(url, (gltf) => resolve(gltf.scene), undefined, onError);
    else reject(new Error(`Formato no compatible: ${format}`));
  });
}

function prepare(object) {
  object.traverse((node) => {
    if (node.isMesh) {
      node.material = MATERIAL();
      if (!node.geometry.attributes.normal) node.geometry.computeVertexNormals();
    }
  });
  return object;
}

function dispose(object) {
  object.traverse((node) => {
    if (node.isMesh) {
      node.geometry?.dispose();
      node.material?.dispose();
    }
  });
}

async function makeRenderer(dense) {
  if (dense) {
    const { WebGPURenderer } = await import("three/webgpu");
    const renderer = new WebGPURenderer({ antialias: true });
    await renderer.init();
    if (!renderer.backend?.isWebGPUBackend) { // three falls back to WebGL2 when the GPU device cannot be created
      renderer.dispose();
      throw new Error("el navegador no pudo crear el dispositivo WebGPU");
    }
    return renderer;
  }
  return new THREE.WebGLRenderer({ antialias: true, alpha: false });
}

/** three.js viewer: orbit controls, Z-up print orientation, auto-fit, grid at the build plate. `glbUrl` shows a (meshopt
 * compressed) GLB instead of the model file. Dense mode: WebGPU renderer + three-virtual-geometry (cluster LOD picked per
 * frame on the GPU), offered for very dense meshes when the browser has WebGPU; anything failing falls back to WebGL. */
export default function Viewer({ model, glbUrl = null, onCloseGlb }) {
  const host = useRef(null);
  const [state, setState] = useState({ phase: "loading", error: null });
  const [dense, setDense] = useState(false);
  const [denseNote, setDenseNote] = useState("");
  const [shownSize, setShownSize] = useState(null); // world box of what is on screen, in mm
  const denseOffer = hasWebGPU() && (model?.triangles || 0) >= DENSE_TRIANGLES;
  useEffect(() => { setDense(false); setDenseNote(""); }, [model?.id]);

  useEffect(() => {
    if (!host.current || !model) return undefined;
    const container = host.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xfbf9f6);
    const camera = new THREE.PerspectiveCamera(40, 4 / 3, 0.1, 100000);
    camera.up.set(0, 0, 1);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a7f74, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(-1, -1.5, 2.5);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffe9d6, 0.5);
    fill.position.set(2, 1, 0.5);
    scene.add(fill);

    let disposed = false;
    let renderer = null;
    let controls = null;
    let observer = null;
    let object = null;
    let grid = null;
    let vg = null;
    const resize = () => {
      if (!renderer) return;
      const width = container.clientWidth || 640;
      const height = container.clientHeight || 480;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };

    const fit = (target) => {
      const box = new THREE.Box3().setFromObject(target);
      if (box.isEmpty()) return;
      const size = box.getSize(new THREE.Vector3());
      const centre = box.getCenter(new THREE.Vector3());
      const radius = Math.max(size.length() / 2, 1e-3);
      const distance = radius / Math.sin((camera.fov * Math.PI) / 360) * 1.15;
      camera.near = distance / 100;
      camera.far = distance * 100;
      camera.position.set(centre.x + distance * 0.62, centre.y - distance * 0.62, centre.z + distance * 0.48);
      camera.updateProjectionMatrix();
      controls.target.copy(centre);
      controls.update();
      if (!disposed) setShownSize(size.toArray().map((v) => Math.round(v * 100) / 100));
      const span = Math.max(size.x, size.y) * 1.6;
      grid = new THREE.GridHelper(span, 10, 0xd9cfc7, 0xe9e2dc);
      grid.rotation.x = Math.PI / 2;
      grid.position.set(centre.x, centre.y, box.min.z);
      scene.add(grid);
    };

    const start = async () => {
      try {
        renderer = await makeRenderer(dense);
      } catch (error) {
        if (!disposed && dense) { setDenseNote(`Modo denso no disponible (${error.message}); se usa el visor normal.`); setDense(false); }
        return;
      }
      if (disposed) { renderer.dispose(); return; }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      container.appendChild(renderer.domElement);
      controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      observer = new ResizeObserver(resize);
      observer.observe(container);
      resize();
      renderer.setAnimationLoop(() => {
        controls.update();
        renderer.render(scene, camera);
      });
      setState({ phase: "loading", error: null });
      let loaded = await load(glbUrl ? "glb" : model.format, glbUrl || `/api/models/${model.id}/file?v=${encodeURIComponent(model.sha256 || "")}`);
      if (disposed) { dispose(loaded); return; }
      if (glbUrl) { // the web GLB is in metres (glTF); the viewer works in millimetres like the library files
        const inMillimetres = new THREE.Group();
        inMillimetres.add(loaded);
        inMillimetres.scale.setScalar(WEB_GLB_TO_MM);
        loaded = inMillimetres;
      }
      object = prepare(loaded);
      if (dense) {
        try {
          const { VirtualGeometry } = await import("three-virtual-geometry");
          vg = new VirtualGeometry();
          await vg.add(object);
          if (!disposed) setDenseNote("Modo denso activo: WebGPU + geometría virtual (detalle por clústeres según la distancia).");
        } catch (error) {
          if (!disposed) { setDenseNote(`La geometría virtual falló (${error.message}); se usa el visor normal.`); setDense(false); }
          return;
        }
      }
      if (disposed) return;
      scene.add(object);
      fit(object);
      setState({ phase: "ready", error: null });
    };
    start().catch((error) => !disposed && setState({ phase: "error", error: error.message }));

    return () => {
      disposed = true;
      renderer?.setAnimationLoop(null);
      observer?.disconnect();
      controls?.dispose();
      if (object) dispose(object);
      if (grid) grid.dispose();
      vg?.dispose?.();
      renderer?.dispose();
      renderer?.domElement.remove();
    };
  }, [model?.id, model?.format, model?.sha256, glbUrl, dense]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div className="viewer" ref={host} aria-label="Visor 3D" data-viewer-mode={dense ? "dense" : glbUrl ? "glb" : "normal"}>
        <div className="hud">
          {state.phase === "loading" && "Cargando modelo…"}
          {state.phase === "ready" && "Arrastra para girar · rueda para acercar · botón derecho para desplazar"}
          {state.phase === "error" && <span style={{ color: "var(--danger-ink)" }}>{state.error}</span>}
        </div>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-3 text-[12px]">
        {glbUrl && <span className="help" data-glb-shown-size={shownSize ? shownSize.join("x") : ""}>Viendo el GLB web comprimido (en metros, mostrado en mm{shownSize ? `: ${shownSize.join(" × ")} mm` : ""}) · <button type="button" className="btn-link" onClick={onCloseGlb}>volver al archivo original</button></span>}
        {denseOffer && (
          <label className="inline-flex items-center gap-1" title="WebGPU + three-virtual-geometry: dibuja solo el detalle que se ve">
            <input type="checkbox" checked={dense} onChange={(e) => { setDenseNote(""); setDense(e.target.checked); }} />Modo denso (WebGPU)
          </label>
        )}
        {!denseOffer && (model?.triangles || 0) >= DENSE_TRIANGLES && (
          <span className="help">Malla muy densa y este navegador no tiene WebGPU: crea una variante en «Simplificar (LOD)» para moverla con fluidez.</span>
        )}
        {denseNote && <span className="help" data-dense-note>{denseNote}</span>}
      </div>
    </div>
  );
}
