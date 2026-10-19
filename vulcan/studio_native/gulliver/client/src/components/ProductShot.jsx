import React, { useState } from "react";
import { useApp } from "../context.js";
import { Icon } from "./ui.jsx";

// Strings for this control only (Spain Spanish first); kept here so the shared dictionary stays untouched.
const TEXT = {
  es: {
    group: "Foto y vídeo para anuncios", photo: "Foto", video: "Vídeo", photo_tip: "Foto de producto (PNG, fondo de estudio)",
    video_tip: "Vídeo de giro de 360° (WebM)", recording: "Grabando {n} %", rendering: "Preparando el vídeo…", capturing: "Generando…",
    photo_done: "Foto guardada: {name}", video_done: "Vídeo guardado: {name}", upload_failed: "Se descargó, pero no se pudo guardar en el servidor: {msg}",
    failed: "No se pudo generar: {msg}", unsupported: "Este navegador no puede grabar vídeo del visor 3D.",
  },
  en: {
    group: "Photo and video for listings", photo: "Photo", video: "Video", photo_tip: "Product photo (PNG, studio background)",
    video_tip: "360° turntable video (WebM)", recording: "Recording {n} %", rendering: "Preparing the video…", capturing: "Rendering…",
    photo_done: "Photo saved: {name}", video_done: "Video saved: {name}", upload_failed: "Downloaded, but it could not be saved on the server: {msg}",
    failed: "Could not render it: {msg}", unsupported: "This browser cannot record video from the 3D view.",
  },
};

const fill = (text, vars) => text.replace(/\{(\w+)\}/g, (_, k) => String(vars?.[k] ?? ""));
export const shotText = (lang, key, vars) => fill((TEXT[lang] || TEXT.es)[key] || key, vars);

const CAMERA = "M3 8a1 1 0 011-1h3l1.5-2h7L17 7h3a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1zM12 16.5a3.5 3.5 0 100-7 3.5 3.5 0 000 7z";

/** A file name that is safe on every OS but keeps accents and spaces turned into dashes. */
export function safeName(name) {
  return (String(name || "").replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").trim().replace(/\s+/g, "-").slice(0, 80)) || "maqueta";
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function upload(modelId, kind, blob) {
  const response = await fetch(`/api/models/${encodeURIComponent(modelId)}/media/${kind}`, {
    method: "POST", headers: { "Content-Type": blob.type }, body: blob,
  });
  if (!response.ok) {
    let msg = `Error ${response.status}`;
    try { const body = await response.json(); msg = body.error || body.detail || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return response.json();
}

/**
 * Photo and turntable-video buttons for the viewer toolbar. `viewerRef` is the ref given to Viewer3D; the files are
 * downloaded and also stored in the model's current build (POST /api/models/{id}/media/{photo|video}).
 */
export default function ProductShot({ viewerRef, modelId, modelName }) {
  const { lang, notify } = useApp();
  const tx = (key, vars) => shotText(lang, key, vars);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [progress, setProgress] = useState(null);       // null | 0..1 while recording, 1 = encoding
  const recording = progress !== null;
  const base = safeName(modelName);

  const finish = async (kind, blob, filename, doneKey) => {
    download(blob, filename);
    try {
      const saved = await upload(modelId, kind, blob);
      notify(tx(doneKey, { name: saved.name }));
    } catch (err) {
      notify(tx("upload_failed", { msg: err.message }), "error");
    }
  };
  const failed = (err) => notify(err?.code === "unsupported" ? tx("unsupported") : tx("failed", { msg: err?.message || "" }), "error");

  const photo = async () => {
    if (!viewerRef.current || photoBusy || recording) return;
    setPhotoBusy(true);
    try {
      const blob = await viewerRef.current.snapshot({ width: 2400, height: 1600 });
      await finish("photo", blob, `${base}-foto.png`, "photo_done");
    } catch (err) { failed(err); } finally { setPhotoBusy(false); }
  };

  const video = async () => {
    if (!viewerRef.current || photoBusy || recording) return;
    setProgress(0);
    try {
      const blob = await viewerRef.current.turntable({ seconds: 8, fps: 30, width: 1280, height: 720, onProgress: (f) => setProgress(f) });
      setProgress(1);
      await finish("video", blob, `${base}-giro.webm`, "video_done");
    } catch (err) { failed(err); } finally { setProgress(null); }
  };

  return (
    <div className="flex items-center gap-1" role="group" aria-label={tx("group")}>
      <button type="button" className="btn btn-sm" onClick={photo} disabled={photoBusy || recording} title={tx("photo_tip")}>
        {photoBusy ? <span className="spinner" aria-hidden="true" /> : <Icon d={CAMERA} size={13} />} {tx("photo")}
      </button>
      <button type="button" className="btn btn-sm" onClick={video} disabled={photoBusy || recording} title={tx("video_tip")} aria-live="polite">
        {recording ? <span className="spinner" aria-hidden="true" /> : <Icon d="M4 6h12v12H4zM16 10l5-3v10l-5-3" size={13} />}{" "}
        {recording ? (progress >= 1 ? tx("rendering") : tx("recording", { n: Math.round(progress * 100) })) : tx("video")}
      </button>
    </div>
  );
}
