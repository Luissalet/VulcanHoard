import React, { useEffect, useState } from "react";
import { FORMAT_LABEL } from "../format.js";

export function Toast({ message, onClose }) {
  useEffect(() => {
    if (!message) return undefined;
    const timer = setTimeout(onClose, 4000);
    return () => clearTimeout(timer);
  }, [message, onClose]);
  if (!message) return null;
  return (
    <div className="toast" role="status">
      {message} <button type="button" className="ml-3 underline" onClick={onClose}>Cerrar</button>
    </div>
  );
}

export function Switch({ checked, onChange, label }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} className="switch" onClick={() => onChange(!checked)} />;
}

export function Empty({ title, children, action }) {
  return (
    <div className="rounded-lg border border-dashed p-8 text-center" style={{ borderColor: "var(--field-line)" }}>
      <p className="font-semibold">{title}</p>
      <p className="help mt-1">{children}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function PageHeader({ title, description, children }) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[26px] font-semibold leading-tight md:text-[30px]">{title}</h1>
        {description && <p className="help mt-1 max-w-[70ch]">{description}</p>}
      </div>
      {children}
    </header>
  );
}

export function FormatChip({ format }) {
  return <span className="chip chip-accent">{FORMAT_LABEL[format] || format}</span>;
}

export function Progress({ progress }) {
  if (!progress) return null;
  const total = progress.files_total || 0;
  const pct = progress.phase === "done" ? 100 : total ? Math.round((progress.files_done / total) * 100) : 0;
  return (
    <div className="bar mt-2" aria-hidden="true">
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Editable list of tags: chips with a remove button plus an input that adds on Enter or comma. */
export function TagEditor({ tags, onChange, placeholder = "añadir etiqueta…" }) {
  const [draft, setDraft] = useState("");
  const commit = () => {
    const extra = draft.split(/[,;]/).map((t) => t.trim().toLowerCase()).filter(Boolean);
    if (extra.length) onChange([...new Set([...tags, ...extra])]);
    setDraft("");
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {tags.map((tag) => (
        <span key={tag} className="tag">
          {tag}
          <button type="button" aria-label={`Quitar ${tag}`} onClick={() => onChange(tags.filter((t) => t !== tag))}>×</button>
        </span>
      ))}
      <input
        className="field w-auto min-w-[140px] flex-1"
        style={{ padding: "3px 8px" }}
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            commit();
          }
        }}
      />
    </div>
  );
}

/** Small ×-able notice used for per-file errors. */
export function ErrorList({ errors }) {
  if (!errors || !errors.length) return null;
  return (
    <ul className="mt-1 max-h-48 space-y-1 overflow-auto rounded-md border p-2 text-[12px]" style={{ borderColor: "var(--danger-line)", background: "var(--danger-bg)", color: "var(--danger-ink)" }}>
      {errors.map((e, i) => (
        <li key={i}><span className="font-semibold">{e.path}</span>: {e.error}</li>
      ))}
    </ul>
  );
}
