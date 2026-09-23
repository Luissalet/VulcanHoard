import React, { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";

const MATERIAL = () => new THREE.MeshStandardMaterial({ color: 0xd6cdc0, metalness: 0.05, roughness: 0.65, side: THREE.DoubleSide });

function load(format, url) {
  return new Promise((resolve, reject) => {
    const onError = (e) => reject(new Error(e?.message || "No se pudo leer el archivo."));
    if (format === "stl") new STLLoader().load(url, (geometry) => resolve(new THREE.Mesh(geometry, MATERIAL())), undefined, onError);
    else if (format === "obj") new OBJLoader().load(url, (group) => resolve(group), undefined, onError);
    else if (format === "3mf") new ThreeMFLoader().load(url, (group) => resolve(group), undefined, onError);
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

/** three.js viewer: orbit controls, Z-up print orientation, auto-fit, grid at the build plate. */
export default function Viewer({ model }) {
  const host = useRef(null);
  const [state, setState] = useState({ phase: "loading", error: null });

  useEffect(() => {
    if (!host.current || !model) return undefined;
    const container = host.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xfbf9f6);
    const camera = new THREE.PerspectiveCamera(40, 4 / 3, 0.1, 100000);
    camera.up.set(0, 0, 1);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    container.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a7f74, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(-1, -1.5, 2.5);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffe9d6, 0.5);
    fill.position.set(2, 1, 0.5);
    scene.add(fill);

    let frame = 0;
    let disposed = false;
    let object = null;
    let grid = null;
    const resize = () => {
      const width = container.clientWidth || 640;
      const height = container.clientHeight || 480;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();
    const animate = () => {
      frame = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

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
      const span = Math.max(size.x, size.y) * 1.6;
      grid = new THREE.GridHelper(span, 10, 0xd9cfc7, 0xe9e2dc);
      grid.rotation.x = Math.PI / 2;
      grid.position.set(centre.x, centre.y, box.min.z);
      scene.add(grid);
    };

    setState({ phase: "loading", error: null });
    load(model.format, `/api/models/${model.id}/file`)
      .then((loaded) => {
        if (disposed) return dispose(loaded);
        object = prepare(loaded);
        scene.add(object);
        fit(object);
        setState({ phase: "ready", error: null });
      })
      .catch((error) => !disposed && setState({ phase: "error", error: error.message }));

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      if (object) dispose(object);
      if (grid) grid.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [model?.id, model?.format]);

  return (
    <div className="viewer" ref={host} aria-label="Visor 3D">
      <div className="hud">
        {state.phase === "loading" && "Cargando modelo…"}
        {state.phase === "ready" && "Arrastra para girar · rueda para acercar · botón derecho para desplazar"}
        {state.phase === "error" && <span style={{ color: "var(--danger-ink)" }}>{state.error}</span>}
      </div>
    </div>
  );
}
