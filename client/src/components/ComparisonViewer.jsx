import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';

export default function ComparisonViewer({ result, aligned }) {
  const host = useRef(null), actions = useRef(null);
  const [phase, setPhase] = useState('Cargando geometrías…');
  useEffect(() => {
    const container = host.current;
    if (!container) return undefined;
    let renderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true }); }
    catch { setPhase('El visor necesita WebGL. Las medidas y coordenadas están disponibles debajo.'); return undefined; }
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xfbf9f6);
    const camera = new THREE.PerspectiveCamera(40, 1, .1, 100000);
    camera.up.set(0, 0, 1);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute('aria-label', 'Revisiones A y B en un mismo marco. Arrastra para girar; usa los botones para acercar, alejar y encajar.');
    container.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.listenToKeyEvents(renderer.domElement);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x84786c, 2));
    const light = new THREE.DirectionalLight(0xffffff, 2);
    light.position.set(-1, -2, 3); scene.add(light);
    const resources = [], group = new THREE.Group(); scene.add(group);
    let disposed = false, frame = 0, dirty = true;
    const resize = () => {
      const width = container.clientWidth || 400, height = container.clientHeight || 300;
      renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix(); dirty = true;
    };
    const observer = new ResizeObserver(resize); observer.observe(container); resize();
    const render = () => { if (controls.update() || dirty) { renderer.render(scene, camera); dirty = false; } frame = requestAnimationFrame(render); };
    controls.addEventListener('change', () => { dirty = true; }); render();
    const fit = () => {
      const box = new THREE.Box3().setFromObject(group);
      if (box.isEmpty()) return;
      const centre = box.getCenter(new THREE.Vector3()), radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, .001);
      const distance = radius / Math.sin(camera.fov * Math.PI / 360) * 1.2;
      camera.near = distance / 1000; camera.far = distance * 100; camera.updateProjectionMatrix();
      camera.position.set(centre.x + distance * .62, centre.y - distance * .62, centre.z + distance * .48);
      controls.target.copy(centre); controls.update(); dirty = true;
    };
    actions.current = { fit, zoom: (factor) => { camera.position.sub(controls.target).multiplyScalar(factor).add(controls.target); controls.update(); dirty = true; } };
    setPhase('Cargando geometrías…');
    const load = async (source, role) => {
      const query = new URLSearchParams({ units: source.requested_units, sha: source.sha256 });
      const geometry = await new STLLoader().loadAsync(`/api/models/${source.id}/comparison-mesh?${query}`);
      if (disposed) { geometry.dispose(); return; }
      resources.push(geometry);
      const material = new THREE.MeshStandardMaterial({ color: role === 'a' ? 0x38607b : 0xb56f39,
        wireframe: role === 'a', transparent: true, opacity: role === 'a' ? .5 : .82,
        side: THREE.DoubleSide, depthWrite: role !== 'a', depthTest: role !== 'a', roughness: .7 });
      resources.push(material);
      const mesh = new THREE.Mesh(geometry, material);
      mesh.renderOrder = role === 'a' ? 2 : 0;
      if (role === 'b' && aligned) mesh.applyMatrix4(new THREE.Matrix4().set(...result.alignment.matrix_b_to_a.flat()));
      group.add(mesh);
    };
    Promise.all([load(result.source_a, 'a'), load(result.source_b, 'b')]).then(() => {
      if (disposed) return;
      const distances = aligned ? result.aligned : result.raw;
      const locations = [...distances.a_to_b.locations, ...distances.b_to_a.locations];
      if (locations.length) {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(locations.flatMap(p => p.point_mm), 3));
        const material = new THREE.PointsMaterial({ color: 0xac2f28, size: 9, sizeAttenuation: false, depthTest: false, depthWrite: false, transparent: true, opacity: 1 });
        const points = new THREE.Points(geometry, material); points.renderOrder = 3;
        resources.push(geometry, material); group.add(points);
      }
      fit(); setPhase('Arrastra para girar · rueda para acercar. Puntos rojos: muestras seleccionadas fuera de tolerancia.');
    }).catch(() => { if (!disposed) setPhase('No se pudieron cargar las geometrías. Si los archivos cambiaron, vuelve a comparar.'); });
    return () => {
      disposed = true; actions.current = null; cancelAnimationFrame(frame); observer.disconnect();
      controls.dispose(); resources.forEach(resource => resource.dispose()); renderer.dispose(); renderer.domElement.remove();
    };
  }, [result, aligned]);
  return <div>
    <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
      <span>A · referencia en alambre</span><span>B · revisión sólida</span>
      <div className="flex gap-2 sm:ml-auto">
        <button type="button" className="btn" style={{ minHeight: 44 }} onClick={() => actions.current?.zoom(.8)} aria-label="Acercar comparación">Acercar</button>
        <button type="button" className="btn" style={{ minHeight: 44 }} onClick={() => actions.current?.zoom(1.25)} aria-label="Alejar comparación">Alejar</button>
        <button type="button" className="btn" style={{ minHeight: 44 }} onClick={() => actions.current?.fit()}>Encajar</button>
      </div>
    </div>
    <div ref={host} className="relative h-[320px] overflow-hidden rounded-lg border md:h-[420px]" style={{ borderColor: 'var(--line)', background: '#fbf9f6' }} />
    <p role="status" className="help mt-2">{phase}</p>
  </div>;
}
