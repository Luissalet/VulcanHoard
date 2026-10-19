"""Assembly instructions from a kept build's exported solids and parameter snapshot, never current settings."""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from urllib.parse import quote

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon

from .errors import GulliverError
from .hoard_link.atomic import read_json, write_bytes_atomic
from .mesh.build import PART_LABELS


def base_outline(path: Path):
    """Read the actual binary STL base. Fill engraving holes; do not invent the positions of horizontal pin holes."""
    data = path.read_bytes()
    dtype = np.dtype([('normal', '<f4', 3), ('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
    if len(data) < 84 or len(data) != 84 + int.from_bytes(data[80:84], 'little') * 50:
        raise GulliverError('unavailable', 'The assembly guide needs the original binary STL export.')
    records = np.frombuffer(data, dtype=dtype, offset=84)
    vertices = records['vertices']
    bottom = np.all(np.abs(vertices[:, :, 2] - vertices[:, :, 2].min()) < 0.0001, axis=1)
    if not bottom.any():
        raise GulliverError('unavailable', 'No flat base was found in this export.')
    geometry = shapely.union_all(shapely.polygons(vertices[bottom, :, :2]))
    polygons = list(geometry.geoms) if geometry.geom_type == 'MultiPolygon' else [geometry]
    return shapely.union_all([Polygon(p.exterior) for p in polygons if p.geom_type == 'Polygon'])


def _build(services, model_id, build_id=''):
    rec = services.projects.get(model_id)
    bid = build_id or (rec.get('build') or {}).get('id')
    if not bid:
        raise GulliverError('not_found', 'This model has not been built yet.')
    folder = services.projects.build_folder(model_id, bid)
    report = services.projects.report(model_id, bid)
    params = read_json(folder / 'params.json', None)
    materials = {p['part']: p for p in (report.get('material_estimate') or {}).get('parts', [])}
    pieces = report.get('tiles') or [{'id': '1', 'row': 0, 'col': 0, 'size_mm': report['size_mm'],
                                    'stl': report['files']['stl'], '3mf': report['files']['3mf'],
                                    'parts': list(report['parts']), 'fits_bed': report.get('fits_bed')}]
    return rec, bid, folder, report, params, materials, pieces


def guide(services, model_id, build_id='', *, offset=0, limit=12):
    rec, bid, folder, report, params, materials, pieces = _build(services, model_id, build_id)
    palette = []
    for part, item in report['parts'].items():
        palette.append({'part': part, 'labels': list(PART_LABELS.get(part, (part, part))),
                        'color': item.get('color', '#cccccc'), 'material': materials.get(part, {}).get('material')})
    pins = None
    if params is not None:
        pins = {'count': report.get('pins', 0), 'diameter_mm': params['pin_diameter_mm'],
                'cut_length_mm': round(2 * params['pin_depth_mm'] - .5, 2)}
    page = [{**p, 'number': i + 1} for i, p in enumerate(pieces) if offset <= i < offset + limit]
    return {'model_id': model_id, 'build_id': bid, 'name': report.get('name', rec['name']),
            'scale': report['scale'], 'size_mm': report['size_mm'], 'bed_mm': report['bed_mm'],
            'pieces': page, 'total': len(pieces), 'offset': offset,
            'next_offset': offset + limit if offset + limit < len(pieces) else None,
            'materials': palette, 'pins': pins, 'tile_labels': params.get('tile_labels') if params else None,
            'panels': bool(params and params.get('panels_x', 1) * params.get('panels_y', 1) > 1),
            'snapshot_known': params is not None, 'attribution': report.get('attribution', [])}


def diagram(services, data):
    _, _, _, _, params, _, pieces = _build(services, data['model_id'], data['build_id'])
    outlines = [base_outline(services.projects.file(data['model_id'], p['stl'], data['build_id'])) for p in pieces]
    bounds = shapely.union_all(outlines).bounds
    span = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 1)
    # Separate the pieces so their cut edges remain legible. Framed panels already overlap in exported map coordinates.
    gap = span * .025 + (2 * max(params.get('frame_mm', 0), 8) if data['panels'] and params else 0)
    shifted = [affinity.translate(g, xoff=p.get('col', 0) * gap, yoff=-p.get('row', 0) * gap)
               for g, p in zip(outlines, pieces)]
    x0, y0, x1, y1 = shapely.union_all(shifted).bounds
    pad = span * .08
    font = min(span * .035, span / max(6, len(pieces) ** .5 * 4))
    drawing = []
    for number, (g, p) in enumerate(zip(shifted, pieces), 1):
        polygons = list(g.geoms) if g.geom_type == 'MultiPolygon' else [g]
        for polygon in polygons:
            points = ' '.join(f'{x:.3f},{-y:.3f}' for x, y in polygon.exterior.coords)
            drawing.append(f'<polygon points="{points}" fill="#eee8dc" stroke="#3a3630" stroke-width="{span*.0015}"/>')
        c = max(polygons, key=lambda p: p.area).representative_point()
        label = escape(f"{number} · {p['id']}")
        drawing.append(f'<text x="{c.x:.3f}" y="{-c.y:.3f}" text-anchor="middle" dominant-baseline="middle" '
                       f'font-size="{font}" fill="#161616" font-family="sans-serif">{label}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="N ↑" '
            f'viewBox="{x0-pad} {-y1-pad*1.5} {x1-x0+2*pad} {y1-y0+2.5*pad}">'
            f'<text x="{x0}" y="{-y1-pad*.5}" font-size="{font}" font-family="sans-serif">N ↑</text>'
            + ''.join(drawing) + '</svg>')


def export(services, model_id, build_id='', *, lang='es'):
    data = guide(services, model_id, build_id, limit=10000)
    bid = data['build_id']
    folder = services.projects.build_folder(model_id, bid)
    svg = diagram(services, data)
    en = lang == 'en'
    tr = lambda es, english: english if en else es
    title = tr('Guía de montaje', 'Assembly guide')
    note = tr('Vista cenital con norte arriba. Piezas separadas para ver los cantos; no es una plantilla a escala real.',
              'Top view, north up. Pieces are separated to show their edges; this is not a full-size template.')
    steps = [tr('Comprueba los archivos y los colores de esta generación antes de imprimir. El STL es de un solo material; asigna los filamentos a las partes del 3MF en el laminador.',
                'Check this build’s files and colours before printing. STL is a single material; assign filaments to the 3MF parts in your slicer.'),
             tr('Orienta las piezas con el norte arriba y coloca las filas de norte a sur y las columnas de oeste a este. Los números de esta guía acompañan los identificadores de los archivos.',
                'Keep north up, rows from north to south and columns from west to east. Guide numbers accompany the file identifiers.'),
             tr('Presenta las piezas sin forzar los detalles finos y comprueba que coinciden los cantos antes de fijarlas.',
                'Dry-fit the pieces gently and check that their edges match before fixing them.')]
    if data['panels']:
        steps.append(tr('Son paneles enmarcados independientes: no unas sus marcos ni les añadas pines.',
                        'These are separate framed panels: do not join their frames or add pins.'))
    elif data['pins'] and data['pins']['count']:
        p = data['pins']
        steps.append(tr(f"Esta generación tiene {p['count']} encajes para pines: diámetro {p['diameter_mm']:g} mm; longitud de corte {p['cut_length_mm']:g} mm. Sus posiciones no se dibujan en este plano.",
                        f"This build has {p['count']} pin connections: diameter {p['diameter_mm']:g} mm; cut length {p['cut_length_mm']:g} mm. Their positions are not drawn on this plan."))
    if not data['snapshot_known']:
        steps.append(tr('Generación antigua sin parámetros guardados: no se deducen las medidas de los pines ni las marcas inferiores de los ajustes actuales.',
                        'Older build without saved parameters: pin dimensions and underside labels are not inferred from current settings.'))
    if data['tile_labels']:
        steps.append(tr('El identificador de cada pieza está grabado debajo.', 'Each piece’s identifier is engraved underneath.'))
    cards = []
    for p in data['pieces']:
        names = ', '.join(escape(next((m['labels'][int(en)] for m in data['materials'] if m['part'] == key), key)) for key in p['parts'])
        size = ' × '.join(f'{v:g}' for v in p['size_mm']) + ' mm'
        fits = tr('Cabe en la cama', 'Fits the bed') if p.get('fits_bed') else tr('Revisar cama de impresión', 'Check printer bed')
        cards.append(f'<section><h3>{p["number"]} · {escape(p["id"])}</h3><p>{size} · {fits}</p>'
                     f'<p>STL: <code>{escape(p["stl"])}</code><br>3MF: <code>{escape(p["3mf"])}</code></p><p>{names}</p></section>')
    legend = ''.join(f'<li><i style="background:{escape(m["color"], quote=True)}"></i>{escape(m["labels"][int(en)])} · '
                     f'{escape(m["color"])} · {escape(m["material"] or tr("Material sin especificar", "Material unspecified"))}</li>' for m in data['materials'])
    thumb = services.projects.file(model_id, 'thumb.png', bid)
    image = base64.b64encode(thumb.read_bytes()).decode('ascii')
    html = f'''<!doctype html><html lang="{lang}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · {escape(data['name'])}</title><style>
body{{font:16px/1.5 system-ui,sans-serif;color:#25221e;background:#fffdf8;max-width:1050px;margin:auto;padding:28px}}
h1,h2,h3{{line-height:1.2}}h1{{margin-bottom:8px}}small,code{{overflow-wrap:anywhere}}.plans{{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:center}}.plans img,.plans svg{{width:100%}}
section{{border-top:1px solid #bcb5a8;padding:12px 0;break-inside:avoid}}.pieces{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 28px}}li{{margin:8px 0}}i{{display:inline-block;width:16px;height:16px;border:1px solid #777;margin-right:8px;vertical-align:middle}}
@media(max-width:600px){{body{{padding:16px}}.plans,.pieces{{grid-template-columns:1fr}}}}@media print{{body{{background:white;padding:0;font-size:10pt}}.plans{{break-inside:avoid}}h2{{break-after:avoid}}@page{{size:A4;margin:15mm}}}}
</style><header><h1>{escape(title)} · {escape(data['name'])}</h1><small>{tr('Generación', 'Build')}: {bid} · {tr('Escala', 'Scale')} {escape(str(data['scale']))} · {data['total']} {tr('piezas', 'pieces')}</small></header>
<div class="plans"><div>{svg}</div><img alt="{tr('Vista de la maqueta', 'Model top view')}" src="data:image/png;base64,{image}"></div><p>{note}</p>
<h2>{tr('Montaje', 'Assembly')}</h2><ol>{''.join('<li>'+escape(s)+'</li>' for s in steps)}</ol>
<h2>{tr('Colores y materiales', 'Colours and materials')}</h2><ul>{legend}</ul>
<h2>{tr('Piezas y archivos', 'Pieces and files')}</h2><div class="pieces">{''.join(cards)}</div>
<footer><p>{tr('Imprime o guarda en PDF desde el menú de tu navegador.', 'Print or save as PDF from your browser menu.')}</p><small>{escape('; '.join(data['attribution']))}</small></footer></html>'''
    filename = f'assembly-{lang}.html'
    mapname = 'assembly-map.svg'
    write_bytes_atomic(folder / filename, html.encode('utf8'))
    write_bytes_atomic(folder / mapname, svg.encode('utf8'))
    base = f'/api/models/{model_id}/files/'
    return {'model_id': model_id, 'build_id': bid, 'file': filename, 'path': str(folder / filename),
            'url': base + quote(filename) + '?build=' + bid, 'map_url': base + mapname + '?build=' + bid,
            'bytes': (folder / filename).stat().st_size}
