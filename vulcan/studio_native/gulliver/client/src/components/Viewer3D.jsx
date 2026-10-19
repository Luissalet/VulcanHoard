import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

/**
 * The 3D preview of a build (preview.glb). Nodes are named "<group>__<part>": the group is a tile id (or "model"), the part
 * terrain / buildings / water / roads / green / frame). `hidden` is a set of part names to hide; `explode` (0..1) pulls the
 * tiles apart; `view` is "iso" | "top" | "front" (changing it moves the camera).
 *
 * A ref gives an imperative handle for listing media: `snapshot({width, height, background})` resolves a PNG Blob and
 * `turntable({seconds, fps, width, height, onProgress})` a WebM Blob, both rendered with a studio look (see `studio`).
 */
const Viewer3D = forwardRef(function Viewer3D({ url, hidden, explode = 0, view = "iso", issues = [], onInfo, labels }, ref) {
  const holder = useRef(null);
  const s = useRef({});
  const [state, setState] = useState("loading");
  const [capturing, setCapturing] = useState(false);

  useEffect(() => {
    const el = holder.current;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });   // alpha: transparent product photos
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.92;
    renderer.shadowMap.enabled = true;               // buildings and trees cast shadows, as on a real model in daylight
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x10140f);
    const camera = new THREE.PerspectiveCamera(35, 1, 0.5, 8000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    const hemi = new THREE.HemisphereLight(0xf4efe4, 0x2c2c24, 0.75);
    scene.add(hemi);
    const sun = new THREE.DirectionalLight(0xfff4e2, 1.9);
    sun.position.set(-1, 1.6, 1.1);
    sun.castShadow = true;
    sun.shadow.mapSize.set(4096, 4096);
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.02;
    scene.add(sun);
    scene.add(sun.target);
    const fill = new THREE.DirectionalLight(0xbfd4ff, 0.45);
    fill.position.set(1.2, 0.6, -0.8);
    scene.add(fill);
    const root = new THREE.Group();
    root.rotation.x = -Math.PI / 2;              // the model is Z-up, three is Y-up
    scene.add(root);
    s.current = { renderer, scene, camera, controls, root, sun, hemi, groups: [], centre: new THREE.Vector3(), size: 100,
      busy: false, dead: false, userMoved: false };
    controls.addEventListener("start", () => { s.current.userMoved = true; });     // only real user input fires this

    const resize = () => {
      if (s.current.busy) return;                  // a capture owns the renderer size until it ends
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    s.current.resize = resize;
    resize();
    let frame = 0;
    const loop = () => {
      frame = requestAnimationFrame(loop);
      if (s.current.busy) return;                  // frozen while a photo / video is rendered with the studio set-up
      controls.update();
      renderer.render(scene, camera);
    };
    loop();
    return () => {
      s.current.dead = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      root.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose?.(); });
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, []);

  useEffect(() => {
    if (!url) return undefined;
    let cancelled = false;
    setState("loading");
    const loader = new GLTFLoader();
    loader.load(url, (gltf) => {
      if (cancelled) return;
      const { root } = s.current;
      while (root.children.length) root.remove(root.children[0]);
      const groups = new Map();
      const content = gltf.scene;
      content.updateMatrixWorld(true);
      const meshes = [];
      content.traverse((o) => { if (o.isMesh) meshes.push(o); });
      let triangles = 0;
      for (const mesh of meshes) {
        const name = [mesh.name, mesh.parent?.name, mesh.geometry?.name].find((n) => n && n.includes("__")) || "model__terrain";
        const [group, part] = name.split("__");
        const geo = mesh.geometry.clone().applyMatrix4(mesh.matrixWorld);
        geo.computeVertexNormals();
        triangles += (geo.index ? geo.index.count : geo.attributes.position.count) / 3;
        const color = mesh.material?.color ? mesh.material.color.clone() : new THREE.Color(0xd8ccb4);
        const material = new THREE.MeshStandardMaterial({ color, roughness: part === "water" ? 0.35 : 0.85, metalness: 0.0, flatShading: true });
        const m = new THREE.Mesh(geo, material);
        m.userData.part = part;
        m.castShadow = part !== "water";
        m.receiveShadow = true;
        if (!groups.has(group)) groups.set(group, new THREE.Group());
        groups.get(group).add(m);
      }
      const box = new THREE.Box3();
      for (const g of groups.values()) { root.add(g); }
      root.updateMatrixWorld(true);
      box.setFromObject(root);
      const centre = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      // per-group centre (in model coordinates) for the exploded view
      const groupInfo = [...groups.entries()].map(([id, g]) => {
        const b = new THREE.Box3().setFromObject(g);
        const c = b.getCenter(new THREE.Vector3());
        return { id, g, c };
      });
      s.current.groups = groupInfo;
      s.current.centre = centre;
      s.current.size = Math.max(size.x, size.z, 1);
      s.current.modelCentre = new THREE.Box3().setFromObject(root).getCenter(new THREE.Vector3());
      const { sun } = s.current;
      const reach = Math.max(size.x, size.z) * 0.75;
      sun.position.copy(centre).add(new THREE.Vector3(-0.55, 0.9, 0.6).normalize().multiplyScalar(reach * 3));
      sun.target.position.copy(centre);
      Object.assign(sun.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach, near: reach * 0.5, far: reach * 6 });
      sun.shadow.camera.updateProjectionMatrix();
      setCamera(view);
      applyHidden(hidden);
      applyExplode(explode);
      setState("ready");
      onInfo?.({ triangles: Math.round(triangles), groups: groupInfo.map((x) => x.id) });
    }, undefined, () => { if (!cancelled) setState("error"); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  useEffect(() => { applyHidden(hidden); }, [hidden]);
  useEffect(() => {
    if (state !== "ready" || !issues.length) return undefined;
    const {root,groups,size} = s.current;
    const byGroup = new Map();
    for (const issue of issues) {
      const [x,y] = issue.position_mm;
      const group = groups.find(({g})=>g.children.some(mesh=>{
        if (!mesh.isMesh || mesh.userData.fragility) return false;
        mesh.geometry.computeBoundingBox();
        const b=mesh.geometry.boundingBox;
        return x>=b.min.x && x<=b.max.x && y>=b.min.y && y<=b.max.y;
      }))?.g || root;
      if (!byGroup.has(group)) byGroup.set(group,[]);
      byGroup.get(group).push(issue);
    }
    const radius = Math.max(.8,size*.006);
    const geometry = new THREE.RingGeometry(radius*.6,radius,16);
    const material = new THREE.MeshBasicMaterial({color:0xe6ad45,side:THREE.DoubleSide,depthTest:false,depthWrite:false,transparent:true,opacity:.95});
    const meshes=[];
    for (const [group,list] of byGroup) {
      const mesh=new THREE.InstancedMesh(geometry,material,list.length);
      mesh.userData.fragility=true;
      mesh.renderOrder=100;
      mesh.frustumCulled=false;
      list.forEach((issue,i)=>mesh.setMatrixAt(i,new THREE.Matrix4().makeTranslation(...issue.position_mm)));
      mesh.instanceMatrix.needsUpdate=true;
      group.add(mesh);meshes.push(mesh);
    }
    return ()=>{meshes.forEach(mesh=>mesh.parent?.remove(mesh));geometry.dispose();material.dispose();};
  },[state,issues]);
  useEffect(() => { applyExplode(explode); }, [explode]);
  useEffect(() => { if (state === "ready") setCamera(view); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [view]);

  function applyHidden(set) {
    const { root } = s.current;
    if (!root) return;
    root.traverse((o) => { if (o.isMesh) o.visible = !(set && set.has(o.userData.part)); });
  }

  function applyExplode(amount) {
    const { groups, modelCentre, size } = s.current;
    if (!groups || groups.length < 2 || !modelCentre) {
      groups?.forEach(({ g }) => g.position.set(0, 0, 0));
      return;
    }
    // world-space offsets converted to the root's (Z-up) frame: world x -> root x, world z -> root -y
    for (const { g, c } of groups) {
      const dx = c.x - modelCentre.x;
      const dz = c.z - modelCentre.z;
      const len = Math.hypot(dx, dz) || 1;
      const k = amount * size * 0.12;
      g.position.set((dx / len) * k, -(dz / len) * k, 0);
    }
  }

  function setCamera(v) {
    const { camera, controls, centre, size } = s.current;
    if (!camera) return;
    const d = size * 1.25;
    if (v === "top") camera.position.set(centre.x, centre.y + d * 1.45, centre.z + 0.001);
    else if (v === "front") camera.position.set(centre.x, centre.y + d * 0.25, centre.z + d * 1.35);
    else camera.position.set(centre.x - d * 0.55, centre.y + d * 0.9, centre.z + d * 1.0);
    controls.target.copy(centre);
    controls.update();
    s.current.userMoved = false;
  }

  useImperativeHandle(ref, () => ({
    snapshot: (opts) => studio(s.current, "photo", opts || {}, setCapturing),
    turntable: (opts) => studio(s.current, "video", opts || {}, setCapturing),
  }), []);

  return (
    <div className="viewer">
      <div ref={holder} className="viewer-canvas" role="img" aria-label={labels?.viewer || "3D"} />
      {state === "loading" && <div className="viewer-overlay"><span className="spinner" /> {labels?.loading}</div>}
      {state === "error" && <div className="viewer-overlay">{labels?.error}</div>}
      {capturing && <div className="viewer-overlay"><span className="spinner" /> {labels?.capturing}</div>}
    </div>
  );
});

export default Viewer3D;

/* ------------------------------------------------------------------------------------------------------------------ */
/* Studio capture: a light neutral (or transparent) backdrop, a shadow-catching ground, a warmer key light and a fitted   */
/* three-quarter camera (or the user's own camera when they have moved it). The live renderer is resized for the shot    */
/* and everything is put back afterwards.                                                                                 */
/* ------------------------------------------------------------------------------------------------------------------ */

const STUDIO_BG = 0xecebe8;

/** World-space box of the visible meshes (hidden parts and exploded tiles are taken as they are on screen). */
function visibleBox(root) {
  const box = new THREE.Box3();
  root.updateMatrixWorld(true);
  root.traverse((o) => {
    if (!o.isMesh || !o.visible) return;
    if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
    box.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld));
  });
  return box;
}

/** A three-quarter camera that fits the box exactly: the distance is the smallest one that keeps all 8 corners in frame. */
function fittedCamera(box, width, height, liveCamera) {
  const aspect = width / height;
  const cam = new THREE.PerspectiveCamera(liveCamera.fov, aspect, 1, 100);
  const pivot = box.getCenter(new THREE.Vector3());
  const az = -0.55, el = 0.5;                                   // gentle three-quarter: ~32 deg to the left, ~29 deg up
  const dir = new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el));
  const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), dir).normalize();
  const up = new THREE.Vector3().crossVectors(dir, right).normalize();
  const tanV = Math.tan(THREE.MathUtils.degToRad(cam.fov) / 2);
  const tanH = tanV * aspect;
  let dist = 1;
  const c = new THREE.Vector3();
  for (let i = 0; i < 8; i++) {
    c.set(i & 1 ? box.max.x : box.min.x, i & 2 ? box.max.y : box.min.y, i & 4 ? box.max.z : box.min.z).sub(pivot);
    const along = c.dot(dir);
    dist = Math.max(dist, Math.abs(c.dot(right)) / tanH + along, Math.abs(c.dot(up)) / tanV + along);
  }
  dist *= 1.1;                                                  // breathing room
  cam.position.copy(pivot).addScaledVector(dir, dist);
  cam.lookAt(pivot);
  return { cam, pivot, dist };
}

/** The user's camera, with the vertical field of view widened if the shot is narrower than the live view (keeps the width). */
function liveCameraFor(st, width, height) {
  const live = st.camera;
  const cam = new THREE.PerspectiveCamera(live.fov, width / height, live.near, live.far);
  cam.position.copy(live.position);
  cam.quaternion.copy(live.quaternion);
  if (width / height < live.aspect) {
    const tanH = Math.tan(THREE.MathUtils.degToRad(live.fov) / 2) * live.aspect;
    cam.fov = THREE.MathUtils.radToDeg(2 * Math.atan(tanH / (width / height)));
  }
  cam.updateProjectionMatrix();
  return { cam, pivot: st.controls.target.clone() };
}

/**
 * Render `kind` ("photo" -> PNG Blob, "video" -> WebM Blob). Resolves after the live view is restored; rejects with an
 * Error whose `.code` is "not_ready", "busy", "unsupported" or "failed".
 */
function studio(st, kind, opts, setCapturing) {
  const fail = (code, message) => Promise.reject(Object.assign(new Error(message), { code }));
  if (!st.renderer || !st.groups || !st.groups.length) return fail("not_ready", "The model is not loaded yet.");
  if (st.busy) return fail("busy", "Another photo or video is being made.");
  const isVideo = kind === "video";
  if (isVideo && (typeof MediaRecorder === "undefined" || !HTMLCanvasElement.prototype.captureStream)) {
    return fail("unsupported", "This browser cannot record video from the 3D view.");
  }
  const { renderer, scene, root, sun, hemi } = st;
  const gl = renderer.getContext();
  const limits = gl.getParameter(gl.MAX_VIEWPORT_DIMS);
  const maxSide = Math.min(limits[0], limits[1], gl.getParameter(gl.MAX_RENDERBUFFER_SIZE));
  let width = Math.round(opts.width || (isVideo ? 1280 : 2400));
  let height = Math.round(opts.height || (isVideo ? 720 : 1600));
  const k = Math.min(1, maxSide / Math.max(width, height));
  width = Math.max(2, Math.round(width * k) & ~1);               // even sizes: video encoders want them
  height = Math.max(2, Math.round(height * k) & ~1);
  const transparent = opts.background === "transparent" && !isVideo;

  // remember the live state
  const saved = {
    bg: scene.background, size: renderer.getSize(new THREE.Vector2()), ratio: renderer.getPixelRatio(), clear: renderer.getClearAlpha(),
    exposure: renderer.toneMappingExposure, sunColor: sun.color.clone(), sunIntensity: sun.intensity, hemi: hemi.intensity,
  };
  st.busy = true;
  setCapturing(true);
  renderer.domElement.style.visibility = "hidden";

  const box = visibleBox(root);
  const size = box.getSize(new THREE.Vector3());
  const span = Math.max(size.x, size.z, 1);
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(span * 6, span * 6),
    new THREE.ShadowMaterial({ color: 0x2a2118, opacity: 0.32 }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.set((box.min.x + box.max.x) / 2, box.min.y - span * 0.0015, (box.min.z + box.max.z) / 2);
  ground.receiveShadow = true;
  scene.add(ground);

  scene.background = transparent ? null : new THREE.Color(STUDIO_BG);
  renderer.setClearColor(0x000000, transparent ? 0 : 1);
  renderer.toneMappingExposure = 1.0;
  sun.color.set(0xffe3bf);                                         // slightly warmer key light
  sun.intensity = 2.1;
  hemi.intensity = 0.85;
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);

  const { cam, pivot } = st.userMoved ? liveCameraFor(st, width, height) : fittedCamera(box, width, height, st.camera);
  if (!st.userMoved) { cam.near = Math.max(0.1, cam.position.distanceTo(pivot) * 0.02); cam.far = cam.position.distanceTo(pivot) * 12 + span * 4; cam.updateProjectionMatrix(); }
  const offset = cam.position.clone().sub(pivot);

  let finished = false;
  const restore = () => {
    if (finished) return;
    finished = true;
    scene.remove(ground);
    ground.geometry.dispose();
    ground.material.dispose();
    scene.background = saved.bg;
    renderer.setClearColor(0x000000, saved.clear);
    renderer.toneMappingExposure = saved.exposure;
    sun.color.copy(saved.sunColor);
    sun.intensity = saved.sunIntensity;
    hemi.intensity = saved.hemi;
    renderer.setPixelRatio(saved.ratio);
    renderer.setSize(saved.size.x, saved.size.y, false);
    renderer.domElement.style.visibility = "";
    st.busy = false;
    st.resize?.();
    setCapturing(false);
  };

  if (!isVideo) {
    return new Promise((resolve, reject) => {
      try {
        renderer.render(scene, cam);
        renderer.domElement.toBlob((blob) => {
          restore();
          if (blob) resolve(blob); else reject(Object.assign(new Error("The browser could not encode the image."), { code: "failed" }));
        }, "image/png");
      } catch (err) {
        restore();
        reject(Object.assign(err, { code: "failed" }));
      }
    });
  }

  // turntable: wall-clock driven so the clip lasts `seconds` even when frames are slow
  const seconds = Math.max(1, opts.seconds || 8);
  const fps = Math.max(1, Math.round(opts.fps || 30));
  return new Promise((resolve, reject) => {
    let recorder;
    let raf = 0;
    const chunks = [];
    const abort = (err) => {
      cancelAnimationFrame(raf);
      try { if (recorder && recorder.state !== "inactive") recorder.stop(); } catch { /* already stopped */ }
      restore();
      reject(Object.assign(err instanceof Error ? err : new Error(String(err)), { code: err?.code || "failed" }));
    };
    try {
      const mime = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find((m) => MediaRecorder.isTypeSupported(m));
      if (!mime) throw Object.assign(new Error("This browser cannot record WebM video."), { code: "unsupported" });
      const stream = renderer.domElement.captureStream(fps);
      recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 8_000_000 });
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.onerror = (e) => abort(e.error || new Error("Recording failed."));
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        if (finished) return;                                          // aborted
        restore();
        const blob = new Blob(chunks, { type: "video/webm" });
        if (blob.size) resolve(blob); else reject(Object.assign(new Error("The recording is empty."), { code: "failed" }));
      };
      const frame = (turn) => {
        cam.position.set(pivot.x + offset.x * Math.cos(turn) + offset.z * Math.sin(turn), pivot.y + offset.y, pivot.z - offset.x * Math.sin(turn) + offset.z * Math.cos(turn));
        cam.lookAt(pivot);
        renderer.render(scene, cam);
      };
      frame(0);
      recorder.start(500);
      opts.onProgress?.(0);
      const t0 = performance.now();
      const tick = (now) => {
        if (st.dead) { abort(Object.assign(new Error("The viewer was closed."), { code: "failed" })); return; }
        const f = Math.min(1, (now - t0) / (seconds * 1000));
        frame(f * Math.PI * 2);
        opts.onProgress?.(f);
        if (f < 1) raf = requestAnimationFrame(tick);
        else recorder.stop();
      };
      raf = requestAnimationFrame(tick);
    } catch (err) {
      abort(err);
    }
  });
}
