import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../context.js";
import { Busy, Icon, ICONS, Switch, useBusy, useStored } from "../components/ui.jsx";
import { useContextProvider } from "../components/hooks.js";
import MapPicker from "../components/MapPicker.jsx";
import { ProfilePicker } from "../components/PrintProfiles.jsx";
import { sizeOfBox } from "../geo.js";
import { num } from "../format.js";
import { MAX_TRACK_BYTES, frameBox, summariseTrack } from "../track.js";

const START = { lon: -3.7038, lat: 40.4168, width_m: 2000, height_m: 2000 };
const SHAPES = ["rect", "rounded", "circle", "hexagon"];
const PRESET_KEYS = ["city", "landscape", "relief"];

function NumberField({ label, value, onChange, step = 1, min, max, unit, hint, id }) {
  return (
    <label className="block" htmlFor={id}>
      <span className="label">{label}</span>
      <div className="unit-field">
        <input id={id} className="field num" type="number" inputMode="decimal" step={step} min={min} max={max}
          value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />
        {unit && <span className="unit">{unit}</span>}
      </div>
      {hint && <span className="help mt-1 block">{hint}</span>}
    </label>
  );
}

function Toggle({ label, checked, onChange, hint }) {
  return (
    <div className="toggle-row">
      <div className="min-w-0 flex-1"><div>{label}</div>{hint && <div className="help">{hint}</div>}</div>
      <Switch checked={checked} onChange={onChange} label={label} />
    </div>
  );
}

function Group({ title, children, open = false, icon }) {
  return (
    <details className="cfg-group" open={open}>
      <summary><Icon d={icon} size={15} /> <span>{title}</span><Icon d={ICONS.chevronDown} size={14} className="chev" /></summary>
      <div className="cfg-body">{children}</div>
    </details>
  );
}

export default function Nueva({ query }) {
  const { t, lang, notify, version } = useApp();
  const editId = query.get("edit") || "";
  const [defaults, setDefaults] = useState(null);
  const [form, setForm] = useState(null);
  const [base, setBase] = useState(null);
  const [name, setName] = useState("");
  const [place, setPlace] = useState("");
  const [preset, setPreset] = useState("");
  const [check, setCheck] = useState(null);
  const [checkError, setCheckError] = useState(null);
  const [features, setFeatures] = useState(null);
  const [results, setResults] = useState([]);
  const [search, setSearch] = useState("");
  const [flyTo, setFlyTo] = useState(null);
  const [layer, setLayer] = useStored("gulliver.layer", "osm");
  const [last, setLast] = useStored("gulliver.lastArea", START);
  const [picking, setPicking] = useState(false);
  const [trackName, setTrackName] = useState("");
  const [busy, run] = useBusy();
  const checkSeq = useRef(0);

  // load presets and settings (and the model when editing)
  useEffect(() => {
    let alive = true;
    (async () => {
      const [p, s] = await Promise.all([api.call("presets_get"), api.call("settings_get")]);
      if (!alive) return;
      setDefaults(p);
      if (editId) {
        const m = await api.call("model_get", { model_id: editId });
        if (!alive) return;
        setForm({ ...p.defaults, ...m.params });
        setBase({ ...p.defaults, ...m.params });
        setName(m.name || "");
        setPlace(m.place || "");
        setPreset(m.preset || "");
        setFlyTo(boxAround(m.params));
      } else {
        const pr = s.default_preset || "city";
        const start = { ...p.defaults, ...(p.presets[pr] || {}), ...START, ...(last || {}), dem: s.dem || "auto" };
        setForm(start);
        setBase(start);
        setPreset(pr);
        setFlyTo(boxAround(start));
      }
    })().catch((e) => notify(e.message, "error"));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editId]);

  const set = useCallback((patch) => setForm((f) => ({ ...f, ...patch })), []);
  // the route in the form, read once per change for the map and for framing (the server does the real parsing)
  const trackInfo = useMemo(() => (form?.track ? summariseTrack(form.track) : null), [form?.track]);

  // what the build would be (debounced)
  useEffect(() => {
    if (!form) return undefined;
    const mine = ++checkSeq.current;
    const timer = setTimeout(async () => {
      try {
        const r = await api.call("area_check", { params: clean(form, { skipTrack: true }) });
        if (mine === checkSeq.current) { setCheck(r); setCheckError(null); }
      } catch (e) {
        if (mine === checkSeq.current) setCheckError(e);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [form]);
  useEffect(() => { setFeatures(null); }, [form?.lon, form?.lat, form?.width_m, form?.height_m]);

  const dirty = useMemo(() => !!form && !!base && JSON.stringify(form) !== JSON.stringify(base), [form, base]);
  useContextProvider(() => form && ({ kind: editId ? "model-params-form" : "new-model-form", object_id: editId || "new", dirty: dirty || !!name,
    fields: { name, place, preset, ...clean(form, { skipTrack: true }), track_loaded: !!form.track } }));

  const doSearch = async (e) => {
    e?.preventDefault();
    if (!search.trim()) return;
    const r = await run("search", () => api.call("place_search", { query: search.trim() }));
    if (r) {
      setResults(r.results || []);
      if (!(r.results || []).length) notify(t("no_results"), "error");
    }
  };

  const pick = (hit) => {
    const size = sizeOfBox(hit.bbox);
    const square = form.shape === "circle" || form.shape === "hexagon";
    // a whole municipality is far too big for a city model: keep the area at the scale the preset is made for
    const limit = { city: 3000, landscape: 40000, relief: 60000 }[preset || "city"] || 3000;
    const k = Math.min(1, limit / Math.max(size.width_m, size.height_m));
    const r = (x) => (x > 5000 ? Math.round(x / 500) * 500 : Math.round(x / 50) * 50);
    const w = r((square ? Math.max(size.width_m, size.height_m) : size.width_m) * k);
    const h = square ? w : r(size.height_m * k);
    const next = { lon: round6(hit.lon), lat: round6(hit.lat), width_m: w, height_m: h };
    set(next);
    setFlyTo(boxAround(next));
    setResults([]);
    setPlace(hit.name);
    if (!name || name === shortName(place)) setName(shortName(hit.name));
  };

  const applyPreset = (key) => {
    setPreset(key);
    setForm((f) => ({ ...f, ...(defaults?.presets?.[key] || {}) }));
  };

  const countFeatures = async () => {
    const r = await run("features", () => api.call("area_check", { params: clean(form, { skipTrack: true }), fetch_features: true }));
    if (r) setFeatures(r.features || null);
  };

  const submit = async () => {
    setLast({ lon: form.lon, lat: form.lat, width_m: form.width_m, height_m: form.height_m });
    if (editId) {
      const r = await run("save", () => api.call("model_update", { model_id: editId, name, place, params: clean(form, { sendEmptyTrack: true }), build: true }));
      if (r) { notify(t("rebuilding")); window.location.hash = `#/m/${editId}`; }
    } else {
      const r = await run("save", () => api.call("model_create", { name, place, preset: preset || undefined, params: clean(form, { sendEmptyTrack: true }), build: true }));
      if (r) { notify(t("building_started")); window.location.hash = `#/m/${r.id}`; }
    }
  };

  const pickHighlight = ({ lon, lat }) => { set({ highlight_lon: lon, highlight_lat: lat }); setPicking(false); };
  const clearHighlight = () => { set({ highlight_lon: null, highlight_lat: null }); setPicking(false); };
  const hasHighlight = Number.isFinite(form?.highlight_lon) && Number.isFinite(form?.highlight_lat);
  const loadTrack = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > MAX_TRACK_BYTES) { notify(t("track_too_big"), "error"); return; }
    let text = "";
    try { text = await file.text(); } catch { notify(t("track_unreadable"), "error"); return; }
    if (text.length > MAX_TRACK_BYTES) { notify(t("track_too_big"), "error"); return; }
    if (!summariseTrack(text)) { notify(t("track_unreadable"), "error"); return; }
    set({ track: text });
    setTrackName(file.name);
  };
  const clearTrack = () => { set({ track: "" }); setTrackName(""); };
  const frameTrack = () => {
    if (!trackInfo) return;
    const box = frameBox(trackInfo.bbox, form.shape);
    set(box);
    setFlyTo(boxAround(box));
  };

  if (!form) return <div className="p-6"><span className="spinner" /> {t("loading")}</div>;
  const square = form.shape === "circle" || form.shape === "hexagon";
  const warn = (check?.warnings || []).map((w) => w[lang] || w.en);
  const byPanels = form.panels_x * form.panels_y > 1;
  const suggested = byPanels ? check?.suggested_panels : check?.suggested_tiles;
  const tilesOff = suggested && !check.fits_bed && (byPanels ? (suggested[0] !== form.panels_x || suggested[1] !== form.panels_y)
    : (suggested[0] !== form.tiles_x || suggested[1] !== form.tiles_y));

  return (
    <div className="new-layout">
      <div className="new-map">
        <form className="map-search" onSubmit={doSearch} role="search">
          <input className="field" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("search_place")} aria-label={t("search_place")} />
          <Busy busy={busy.search} className="btn btn-primary" type="submit" onClick={doSearch} aria-label={t("search")}><Icon d={ICONS.search} size={15} /></Busy>
          {results.length > 0 && (
            <ul className="map-results" role="listbox">
              {results.map((r, i) => (
                <li key={i}><button type="button" onClick={() => pick(r)}><b>{shortName(r.name)}</b><span className="help">{r.name}</span></button></li>
              ))}
            </ul>
          )}
        </form>
        <div className="map-layers seg" role="group" aria-label={t("base_map")}>
          {["osm", "topo", "pnoa", "esri"].map((k) => (
            <button key={k} type="button" aria-pressed={layer === k} onClick={() => setLayer(k)}>{t(`layer_${k}`)}</button>
          ))}
        </div>
        <button type="button" className="btn btn-sm btn-icon map-fit" onClick={() => setFlyTo(boxAround(form))} aria-label={t("map_fit")} title={t("map_fit")}>
          <Icon d={ICONS.target} size={15} />
        </button>
        <MapPicker value={form} base={layer} flyTo={flyTo} onChange={set} pickMode={picking} onPick={pickHighlight}
          highlight={hasHighlight ? { lon: form.highlight_lon, lat: form.highlight_lat } : null} route={trackInfo?.draw || null}
          labels={{ map: t("map_label"), move: t("map_move"), resize: t("map_resize") }} />
        <div className="map-hint help">{picking ? t("pick_hint") : t("map_hint")}</div>
      </div>

      <div className="new-panel">
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            {editId && <a className="btn btn-sm btn-icon" href={`#/m/${editId}`} aria-label={t("back")}><Icon d={ICONS.arrowLeft} size={14} /></a>}
            <h1 className="flex-1">{editId ? t("edit_model") : t("new_model")}</h1>
          </div>
          <label className="block">
            <span className="label">{t("name")}</span>
            <input className="field" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("name_placeholder")} maxLength={120} />
          </label>
          {place && <div className="help trunc" title={place}>{place}</div>}
          <ProfilePicker value={form.print_profile} onChange={profile=>set({print_profile:profile,...(profile?{nozzle_mm:profile.nozzle_mm}:{})})}/>
          <div>
            <span className="label">{t("preset")}</span>
            <div className="seg w-full" role="group">
              {PRESET_KEYS.map((k) => <button key={k} type="button" className="flex-1" aria-pressed={preset === k} onClick={() => applyPreset(k)}>{t(`preset_${k}`)}</button>)}
            </div>
            <div className="help mt-1">{t(`preset_${preset || "city"}_help`)}</div>
          </div>
        </div>

        <Summary check={check} error={checkError} features={features} form={form} busy={busy.features} onCount={countFeatures}
          onTiles={tilesOff ? () => set(byPanels ? { panels_x: suggested[0], panels_y: suggested[1] } : { tiles_x: suggested[0], tiles_y: suggested[1] }) : null} warnings={warn} />

        <Group title={t("g_area")} icon={ICONS.target} open>
          <div className="grid grid-cols-2 gap-3">
            <NumberField id="f-w" label={square ? t("diameter") : t("width")} unit="m" step={50} min={50} value={form.width_m}
              onChange={(v) => set(square ? { width_m: v, height_m: v } : { width_m: v })} />
            {!square && <NumberField id="f-h" label={t("height")} unit="m" step={50} min={50} value={form.height_m} onChange={(v) => set({ height_m: v })} />}
          </div>
          <div>
            <span className="label">{t("shape")}</span>
            <div className="seg w-full" role="group">
              {SHAPES.map((k) => <button key={k} type="button" className="flex-1" aria-pressed={form.shape === k}
                onClick={() => set(k === "circle" || k === "hexagon" ? { shape: k, height_m: form.width_m } : { shape: k })}>{t(`shape_${k}`)}</button>)}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <NumberField id="f-lat" label={t("lat")} step={0.0001} value={form.lat} onChange={(v) => set({ lat: v })} />
            <NumberField id="f-lon" label={t("lon")} step={0.0001} value={form.lon} onChange={(v) => set({ lon: v })} />
          </div>
        </Group>

        <Group title={t("g_print")} icon={ICONS.ruler} open>
          <div className="grid grid-cols-2 gap-3">
            <NumberField id="f-size" label={t("size_mm")} unit="mm" step={5} min={30} value={form.size_mm} onChange={(v) => set({ size_mm: v })} hint={t("size_mm_help")} />
            <NumberField id="f-base" label={t("base_mm")} unit="mm" step={0.5} min={1} value={form.base_mm} onChange={(v) => set({ base_mm: v })} />
            <NumberField id="f-z" label={t("z_exag")} unit="×" step={0.1} min={0.2} value={form.z_exag} onChange={(v) => set({ z_exag: v })} />
            <NumberField id="f-bz" label={t("building_exag")} unit="×" step={0.1} min={0.2} value={form.building_exag} onChange={(v) => set({ building_exag: v })} />
            <NumberField id="f-res" label={t("resolution")} unit="mm" step={0.05} min={0.15} value={form.resolution_mm} onChange={(v) => set({ resolution_mm: v })} hint={t("resolution_help")} />
            <label className="block">
              <span className="label">{t("dem_source")}</span>
              <select className="field" value={form.dem} onChange={(e) => set({ dem: e.target.value })}>
                {["auto", "ign", "terrarium"].map((k) => <option key={k} value={k}>{t(`dem_${k}`)}</option>)}
              </select>
            </label>
          </div>
        </Group>

        <Group title={t("g_layers")} icon={ICONS.layers}>
          <Toggle label={t("l_buildings")} checked={form.buildings} onChange={(v) => set({ buildings: v })} />
          <Toggle label={t("l_water")} checked={form.water} onChange={(v) => set({ water: v })} />
          <Toggle label={t("l_waterways")} checked={form.waterways} onChange={(v) => set({ waterways: v })} />
          <Toggle label={t("l_sea")} checked={form.sea} onChange={(v) => set({ sea: v })} hint={t("l_sea_help")} />
          <Toggle label={t("l_green")} checked={form.green} onChange={(v) => set({ green: v })} />
          <Toggle label={t("l_rails")} checked={form.rails} onChange={(v) => set({ rails: v })} hint={t("l_rails_help")} />
          <Toggle label={t("l_bridges")} checked={form.bridges} onChange={(v) => set({ bridges: v })} />
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="label">{t("roads")}</span>
              <select className="field" value={form.roads} onChange={(e) => set({ roads: e.target.value })}>
                {["engraved", "raised", "flat", "none"].map((k) => <option key={k} value={k}>{t(`roads_${k}`)}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label">{t("road_detail")}</span>
              <select className="field" value={form.road_detail} onChange={(e) => set({ road_detail: e.target.value })}>
                {["auto", "all", "local", "main", "none"].map((k) => <option key={k} value={k}>{t(`detail_${k}`)}</option>)}
              </select>
            </label>
            <NumberField id="f-contours" label={t("contours")} unit="m" step={5} min={-1} value={form.contours_m} onChange={(v) => set({ contours_m: v })} hint={t("contours_help")} />
            <NumberField id="f-wd" label={t("water_depth")} unit="mm" step={0.1} min={0.1} value={form.water_depth_mm} onChange={(v) => set({ water_depth_mm: v })} />
            <NumberField id="f-wl" label={t("water_lines")} unit="" step={1} min={0} max={6} value={form.water_lines} onChange={(v) => set({ water_lines: v })} hint={t("water_lines_help")} />
            <NumberField id="f-rd" label={t("road_depth")} unit="mm" step={0.05} min={0.05} value={form.road_depth_mm} onChange={(v) => set({ road_depth_mm: v })} hint={t("road_depth_help")} />
          </div>
        </Group>

        <Group title={t("g_detail")} icon={ICONS.layers}>
          <Toggle label={t("l_trees")} checked={form.trees} onChange={(v) => set({ trees: v })} hint={t("l_trees_help")} />
          <Toggle label={t("l_paths")} checked={form.paths} onChange={(v) => set({ paths: v })} hint={t("l_paths_help")} />
          <Toggle label={t("l_arches")} checked={form.arches} onChange={(v) => set({ arches: v })} hint={t("l_arches_help")} />
          <Toggle label={t("l_tunnels")} checked={form.tunnels ?? true} onChange={(v) => set({ tunnels: v })} hint={t("l_tunnels_help")} />
          <Toggle label={t("l_zones")} checked={form.zones} onChange={(v) => set({ zones: v })} hint={t("l_zones_help")} />
          {form.zones && <Toggle label={t("l_ground_photo")} checked={form.ground_from_photo} onChange={(v) => set({ ground_from_photo: v })} hint={t("l_ground_photo_help")} />}
          <Toggle label={t("l_meadows")} checked={form.meadows} onChange={(v) => set({ meadows: v })} hint={t("l_meadows_help")} />
          <Toggle label={t("l_plazas")} checked={form.plazas} onChange={(v) => set({ plazas: v })} hint={t("l_plazas_help")} />
          <Toggle label={t("l_dsm")} checked={form.dsm_heights} onChange={(v) => set({ dsm_heights: v })} hint={t("l_dsm_help")} />
          {form.dsm_heights && <Toggle label={t("l_laser_roofs")} checked={form.laser_roofs !== false} onChange={(v) => set({ laser_roofs: v })} hint={t("l_laser_roofs_help")} />}
          <Toggle label={t("l_landcover")} checked={form.landcover} onChange={(v) => set({ landcover: v })} hint={t("l_landcover_help")} />
          <Toggle label={t("l_fields")} checked={form.fields} onChange={(v) => set({ fields: v })} hint={t("l_fields_help")} />
          {form.fields && <Toggle label={t("l_field_rows")} checked={form.field_rows} onChange={(v) => set({ field_rows: v })} hint={t("l_field_rows_help")} />}
          {form.fields && form.field_rows && <Toggle label={t("l_rows_photo")} checked={form.rows_from_photo ?? true} onChange={(v) => set({ rows_from_photo: v })} hint={t("l_rows_photo_help")} />}
          {form.fields && form.field_rows && (
            <div className="grid grid-cols-2 gap-3">
              <NumberField id="f-rowsp" label={t("row_spacing")} unit="mm" step={0.1} min={0.8} max={5} value={form.row_spacing_mm} onChange={(v) => set({ row_spacing_mm: v })} />
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="label">{t("roofs")}</span>
              <select className="field" value={form.roofs} onChange={(e) => set({ roofs: e.target.value })}>
                {["auto", "tagged", "flat"].map((k) => <option key={k} value={k}>{t(`roofs_${k}`)}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label">{t("buildings_source")}</span>
              <select className="field" value={form.buildings_source} onChange={(e) => set({ buildings_source: e.target.value })}>
                {["auto", "cadastre", "osm"].map((k) => <option key={k} value={k}>{t(`bsrc_${k}`)}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label">{t("tree_source")}</span>
              <select className="field" value={form.tree_source} onChange={(e) => set({ tree_source: e.target.value })}>
                {["auto", "osm"].map((k) => <option key={k} value={k}>{t(`tsrc_${k}`)}</option>)}
              </select>
            </label>
            <NumberField id="f-td" label={t("tree_density")} unit="×" step={0.1} min={0} max={3} value={form.tree_density} onChange={(v) => set({ tree_density: v })} />
            <NumberField id="f-ts" label={t("tree_size")} unit="mm" step={0.1} min={0.5} value={form.tree_size_mm} onChange={(v) => set({ tree_size_mm: v })} />
            <NumberField id="f-snow" label={t("snow_m")} unit="m" step={100} min={-1} value={form.snow_m} onChange={(v) => set({ snow_m: v })} hint={t("snow_help")} />
            <NumberField id="f-mrr" label={t("max_relief")} unit="×" step={0.05} min={0.05} max={2} value={form.max_relief_ratio} onChange={(v) => set({ max_relief_ratio: v })} hint={t("max_relief_help")} />
            <NumberField id="f-rock" label={t("rock_slope")} unit="°" step={1} min={10} max={80} value={form.rock_slope_deg} onChange={(v) => set({ rock_slope_deg: v })} />
            <NumberField id="f-tall" label={t("tall_buildings")} unit="m" step={10} min={0} max={600} value={form.tall_buildings_m} onChange={(v) => set({ tall_buildings_m: v })} hint={t("tall_buildings_help")} />
          </div>
        </Group>

        <Group title={t("g_finish")} icon={ICONS.edit}>
          <div className="grid grid-cols-2 gap-3">
            <NumberField id="f-frame" label={t("frame")} unit="mm" step={1} min={0} value={form.frame_mm} onChange={(v) => set({ frame_mm: v })} hint={t("frame_help")} />
          </div>
          <Toggle label={t("label_bottom")} checked={form.label_bottom} onChange={(v) => set({ label_bottom: v })} hint={t("label_help")} />
          {form.label_bottom && (
            <label className="block">
              <span className="label">{t("label_text")}</span>
              <input className="field" value={form.label} maxLength={80} onChange={(e) => set({ label: e.target.value })} placeholder={t("label_placeholder")} />
            </label>
          )}
          <label className="block">
            <span className="label">{t("frame_style")}</span>
            <select className="field" value={form.frame_style} onChange={(e) => set({ frame_style: e.target.value })}>
              {["flat", "bevel", "step"].map((k) => <option key={k} value={k}>{t(`frame_style_${k}`)}</option>)}
            </select>
          </label>
          <Toggle label={t("plaque")} checked={form.plaque} onChange={(v) => set({ plaque: v })} hint={t("plaque_help")} />
          {form.plaque && (
            <label className="block">
              <span className="label">{t("plaque_text")}</span>
              <input className="field" value={form.plaque_text} maxLength={60} onChange={(e) => set({ plaque_text: e.target.value })} placeholder={t("plaque_placeholder")} />
            </label>
          )}
          <div>
            <span className="label">{t("panels")}</span>
            <div className="grid grid-cols-2 gap-3">
              <NumberField id="f-px" label={t("panels_x")} step={1} min={1} max={6} value={form.panels_x} onChange={(v) => set({ panels_x: v })} />
              <NumberField id="f-py" label={t("panels_y")} step={1} min={1} max={6} value={form.panels_y} onChange={(v) => set({ panels_y: v })} />
            </div>
            <span className="help mt-1 block">{t("panels_help")}</span>
            {form.panels_x * form.panels_y > 1 && !(form.frame_mm > 0) && <div className="help mt-2">{t("panels_need_frame")}</div>}
          </div>
          <Toggle label={t("thicken")} checked={form.thicken} onChange={(v) => set({ thicken: v })} hint={t("thicken_help")} />
          <div className="grid grid-cols-2 gap-3">
            <NumberField id="f-nozzle" label={t("nozzle")} unit="mm" step={0.05} min={0.1} max={1.2} value={form.nozzle_mm} onChange={(v) => set({ nozzle_mm: v })} hint={t("nozzle_help")} />
          </div>
        </Group>

        <Group title={t("g_personal")} icon={ICONS.star} open={hasHighlight || !!form.track}>
          <div className="space-y-2">
            <span className="label">{t("highlight")}</span>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn btn-sm" aria-pressed={picking} onClick={() => setPicking(!picking)}>
                <Icon d={ICONS.target} size={13} /> {picking ? t("highlight_cancel") : t("highlight_set")}
              </button>
              {hasHighlight && <button type="button" className="btn btn-sm" onClick={clearHighlight}><Icon d={ICONS.close} size={13} /> {t("highlight_remove")}</button>}
            </div>
            <div className="help">{hasHighlight ? t("highlight_at", { lat: num(form.highlight_lat, 5, lang), lon: num(form.highlight_lon, 5, lang) }) : t("highlight_help")}</div>
          </div>
          <div className="space-y-2">
            <span className="label">{t("route")}</span>
            <input type="file" className="field" accept=".gpx,.kml,.geojson,.json" onChange={loadTrack} aria-label={t("route")} />
            {form.track ? (
              <>
                <div className="help">{t(trackInfo && trackInfo.lines === 1 ? "track_loaded_one" : "track_loaded", { name: trackName || t("track_file"), pts: trackInfo ? num(trackInfo.points, 0, lang) : "?", n: trackInfo ? trackInfo.lines : "?" })}</div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn btn-sm" onClick={frameTrack} disabled={!trackInfo}><Icon d={ICONS.target} size={13} /> {t("track_frame")}</button>
                  <button type="button" className="btn btn-sm" onClick={clearTrack}><Icon d={ICONS.close} size={13} /> {t("track_remove")}</button>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <NumberField id="f-tw" label={t("track_width")} unit="mm" step={0.1} min={0.4} max={5} value={form.track_width_mm} onChange={(v) => set({ track_width_mm: v })} />
                  <NumberField id="f-th" label={t("track_height")} unit="mm" step={0.1} min={0.2} max={4} value={form.track_height_mm} onChange={(v) => set({ track_height_mm: v })} />
                </div>
              </>
            ) : <div className="help">{t("track_help")}</div>}
          </div>
        </Group>

        <Group title={t("g_tiles")} icon={ICONS.grid} open={form.tiles_x * form.tiles_y > 1}>
          <div className="grid grid-cols-2 gap-3">
            <NumberField id="f-tx" label={t("tiles_x")} step={1} min={1} max={12} value={form.tiles_x} onChange={(v) => set({ tiles_x: v })} />
            <NumberField id="f-ty" label={t("tiles_y")} step={1} min={1} max={12} value={form.tiles_y} onChange={(v) => set({ tiles_y: v })} />
          </div>
          {form.tiles_x * form.tiles_y > 1 && (
            <label className="block">
              <span className="label">{t("tile_cuts")}</span>
              <select className="field" value={form.tile_cuts} onChange={(e) => set({ tile_cuts: e.target.value })}>
                {["straight", "streets"].map((k) => <option key={k} value={k}>{t(`tile_cuts_${k}`)}</option>)}
              </select>
              <span className="help mt-1 block">{t("tile_cuts_help")}</span>
            </label>
          )}
          <Toggle label={t("pins")} checked={form.pins} onChange={(v) => set({ pins: v })} hint={t("pins_help")} />
          <Toggle label={t("tile_labels")} checked={form.tile_labels} onChange={(v) => set({ tile_labels: v })} />
        </Group>

        <Group title={t("g_output")} icon={ICONS.cube}>
          <div className="seg w-full" role="group">
            {["color", "mono"].map((k) => <button key={k} type="button" className="flex-1" aria-pressed={form.color === k} onClick={() => set({ color: k })}>{t(`color_${k}`)}</button>)}
          </div>
          <div className="help">{t(`color_${form.color}_help`)}</div>
          {form.color === "color" && (
            <>
              <span className="label">{t("palette")}</span>
              <div className="palette-list" role="radiogroup" aria-label={t("palette")}>
                {["classic", "sand", "grey", "white", "alpine"].map((k) => {
                  const c = defaults?.palettes?.[k] || {};
                  return (
                    <button key={k} type="button" role="radio" aria-checked={(form.palette || "classic") === k} className="palette-opt"
                            onClick={() => set({ palette: k })}>
                      <span className="palette-swatches" aria-hidden="true">
                        {["terrain", "roads", "buildings", "green", "trees", "water", "rock"].map((part) => <span key={part} style={{ background: c[part] }} />)}
                      </span>
                      <span>{t(`palette_${k}`)}</span>
                    </button>
                  );
                })}
              </div>
              <div className="help">{t("palette_help")}</div>
            </>
          )}
        </Group>

        <div className="new-actions">
          {editId && <a className="btn" href={`#/m/${editId}`}>{t("cancel")}</a>}
          <Busy busy={busy.save} className="btn btn-primary flex-1" onClick={submit} disabled={!check || !!checkError}>
            <Icon d={ICONS.play} size={14} /> {editId ? t("save_rebuild") : t("create_build")}
          </Busy>
        </div>
      </div>
    </div>
  );
}

function Summary({ check, error, features, form, busy, onCount, onTiles, warnings }) {
  const { t, lang } = useApp();
  if (error) return <div className="banner banner-danger">{error.message}{error.hint && <div className="help">{error.hint}</div>}</div>;
  if (!check) return <div className="panel help"><span className="spinner" /> {t("loading")}</div>;
  return (
    <div className="panel space-y-2.5" aria-live="polite">
      <div className="facts-grid">
        <div><span>{t("scale")}</span><b>{check.scale}</b></div>
        <div><span>{t("model_size")}</span><b>{num(check.model_mm[0], 0, lang)} × {num(check.model_mm[1], 0, lang)} <small>mm</small></b></div>
        <div><span>{t("ground")}</span><b>{num(check.ground_m[0] / 1000, 2, lang)} × {num(check.ground_m[1] / 1000, 2, lang)} <small>km</small></b></div>
        <div><span>{t(check.suggested_panels || (check.panels && check.panels[0] * check.panels[1] > 1) ? "panels_word" : "pieces")}</span><b>{check.panels && check.panels[0] * check.panels[1] > 1 ? check.panels[0] * check.panels[1] : check.tiles[0] * check.tiles[1]} <small>({num(check.tile_mm[0], 0, lang)} × {num(check.tile_mm[1], 0, lang)} mm)</small></b></div>
      </div>
      <div className="help">{t("sources_line", { dem: check.dem, roads: t(`rank_${check.road_rank}`) })}</div>
      {warnings.map((w, i) => <div key={i} className="banner banner-warn">{w}</div>)}
      {onTiles && <button type="button" className="btn btn-sm" onClick={onTiles}><Icon d={ICONS.grid} size={13} /> {check.suggested_panels ? t("use_panels", { x: check.suggested_panels[0], y: check.suggested_panels[1] }) : t("use_tiles", { x: check.suggested_tiles[0], y: check.suggested_tiles[1] })}</button>}
      {features ? (
        <div className="help">{t("features_line", { b: num(features.buildings, 0, lang), r: num(features.roads, 0, lang), w: num(features.water_km2, 2, lang), g: num(features.green_km2, 2, lang) })}
          {features.trees != null && <div>{t("features_line2", { t: num(features.trees, 0, lang), p: num(features.building_parts || 0, 0, lang), f: num(features.roofs || 0, 0, lang), k: num(features.rail_bridges || 0, 0, lang) })}</div>}
          {features.landmarks?.length > 0 && <div>{t("landmarks")}: {features.landmarks.slice(0, 6).join(", ")}</div>}
        </div>
      ) : (
        <Busy busy={busy} className="btn btn-sm" onClick={onCount}><Icon d={ICONS.search} size={13} /> {t("count_features")}</Busy>
      )}
    </div>
  );
}

// What goes to the server: empty values are left out, except an empty route when saving (so a removed route is removed);
// the route text itself stays out of the live check, which only needs the area.
function clean(form, { skipTrack = false, sendEmptyTrack = false } = {}) {
  const out = {};
  for (const [k, v] of Object.entries(form)) {
    if (k === "track") {
      if (skipTrack) continue;
      if (v === "" && sendEmptyTrack) { out[k] = ""; continue; }
    }
    if (v === "" || v === null || v === undefined || Number.isNaN(v)) continue;
    out[k] = v;
  }
  return out;
}

function shortName(display) {
  return String(display || "").split(",")[0].trim();
}

function round6(x) {
  return Math.round(Number(x) * 1e6) / 1e6;
}

function boxAround(p) {
  const kx = 111320 * Math.cos((p.lat * Math.PI) / 180);
  const dlon = (p.width_m * 0.75) / kx;
  const dlat = (p.height_m * 0.75) / 110574;
  return [p.lon - dlon, p.lat - dlat, p.lon + dlon, p.lat + dlat];
}
