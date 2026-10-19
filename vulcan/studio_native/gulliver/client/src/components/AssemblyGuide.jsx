import React, {useEffect, useState} from 'react';
import {api, fileUrl} from '../api.js';
import {useApp} from '../context.js';
import {ErrorBox} from './ui.jsx';
import {assemblyT} from '../assembly-strings.js';

export default function AssemblyGuide({model}) {
  const {lang} = useApp(), t = assemblyT(lang);
  const [builds,setBuilds] = useState([]), [build,setBuild] = useState(model.build.id);
  const [data,setData] = useState(null), [output,setOutput] = useState(null);
  const [error,setError] = useState(null), [busy,setBusy] = useState(false);
  useEffect(()=>{
    let active=true;
    api.call('model_builds',{model_id:model.id}).then(r=>{if(active)setBuilds(r.builds || []);}).catch(e=>{if(active)setError(e);});
    return ()=>{active=false;};
  },[model.id,model.build.id]);
  useEffect(()=>{setData(null);setOutput(null);setError(null);},[build,lang]);
  const prepare = async () => {
    setBusy(true);setError(null);
    try {
      const page=await api.call('model_assembly',{model_id:model.id,build_id:build});
      const exported=await api.call('model_assembly_export',{model_id:model.id,build_id:build,lang});
      setData(page);setOutput(exported);
    } catch(e) {setError(e);} finally {setBusy(false);}
  };
  const more = async () => {
    setBusy(true);setError(null);
    try {
      const page=await api.call('model_assembly',{model_id:model.id,build_id:build,offset:data.next_offset});
      setData(old=>({...page,pieces:[...old.pieces,...page.pieces]}));
    } catch(e) {setError(e);} finally {setBusy(false);}
  };
  return <details className="assembly-guide panel">
    <summary><b>{t('title')}</b></summary>
    <p className="help">{t('intro')}</p>
    <div className="flex flex-wrap gap-2 items-end">
      <label className="min-w-0 flex-1"><span className="help">{t('build')}</span>
        <select value={build} disabled={busy} onChange={e=>setBuild(e.target.value)}>
          {builds.length ? builds.map(b=><option key={b.id} value={b.id}>{b.id}{b.current?` · ${t('current')}`:''}</option>) : <option value={build}>{build}</option>}
        </select>
      </label>
      <button type="button" className="btn" disabled={busy} onClick={prepare}>{busy?t('preparing'):t('prepare')}</button>
    </div>
    {error && <ErrorBox error={error}/>}
    {data && output && <>
      <div className="flex flex-wrap gap-2 mt-3">
        <a className="btn" href={output.url} target="_blank" rel="noopener">{t('open')}</a>
        <a className="btn" href={output.url+'&download=1'}>{t('download')}</a>
      </div>
      <img className="assembly-map" src={output.map_url} alt={t('note')}/>
      <p className="help">{t('note')} {t('original')}</p>
      {!data.snapshot_known && <p className="help">{t('history')}</p>}
      <h3>{t('legend')}</h3>
      <ul className="assembly-materials">{data.materials.map(m=><li key={m.part}>
        <span className="swatch" style={{background:m.color}}/>
        <span>{m.labels[lang==='en'?1:0]} · {m.color} · {m.material || t('unknown')}</span>
      </li>)}</ul>
      <ol className="assembly-pieces">{data.pieces.map(p=><li key={p.id} value={p.number}>
        <b>{p.id}</b> · {p.size_mm.join(' × ')} mm · {t(p.fits_bed?'fit':'nofit')}
        <div className="help">{t('files')}: <a href={fileUrl(model.id,p.stl,{kept:data.build_id,download:true})}>STL</a> · <a href={fileUrl(model.id,p['3mf'],{kept:data.build_id,download:true})}>3MF</a></div>
      </li>)}</ol>
      {data.next_offset !== null && <button className="btn" type="button" disabled={busy} onClick={more}>{t('more')}</button>}
    </>}
  </details>;
}
