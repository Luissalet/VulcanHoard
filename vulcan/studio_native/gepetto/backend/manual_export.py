"""Export explicit manual RGB projects without converting their ordinal heights."""
import json
from pathlib import Path
import re
import tempfile
import uuid
import zipfile

import numpy as np
from PIL import Image
from .processing import displacement_color, write_manual_palette


def export_manual_zip(project, directory):
    root = Path(directory)
    assets = project['assets']
    if not assets or any(not a['regions'] for a in assets):
        raise ValueError('Prepara todas las texturas antes de exportar la paleta manual.')
    for a in assets:
        for region in a['regions']:
            displacement_color(region)
    target = root / f'manual-export-{uuid.uuid4().hex}.zip'
    try:
        with tempfile.TemporaryDirectory(dir=root) as temp:
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                entries = []
                for a in assets:
                    stem = re.sub(r'[^a-zA-Z0-9_.-]+', '_', Path(a['name']).stem)[:100]
                    name = f'{stem}_{a["id"]}_relieve.png'
                    output = Path(temp) / name
                    with np.load(root / f'{a["id"]}.npz', allow_pickle=False) as masks:
                        with Image.open(root / 'sources' / a['file']) as source:
                            write_manual_palette(source, masks['labels'], a['regions'], masks['centers'], output)
                    archive.write(output, name)
                    entries.append({'source':a['name'],'file':name,'width':a['width'],'height':a['height'],'regions':a['regions']})
                archive.writestr('manual-palette.json', json.dumps(dict(
                    profile='manual-rgb-preserve-scene', project=project['name'], textures=entries,
                    physical_calibration=False, ordinal_heights_are_not_production_maps=True), ensure_ascii=False, indent=2))
                archive.writestr('LEEME.txt',
                    'PALETA MANUAL PARA FIGURE TOOLS\n\n'
                    'Los PNG conservan los colores de relieve elegidos y el alfa original.\n'
                    'Sustituye las imágenes correspondientes en tu escena de Figure Tools y recárgalas.\n'
                    'Conserva el espacio de color y los nodos de tu escena de trabajo habitual.\n'
                    'No importes estos mapas con el puente antiguo de alturas R - 128/255.\n'
                    'Las alturas numéricas del proyecto son referencias ordinales; estos RGB son los mapas de producción.\n'
                    'Guarda además el archivo .gepettos para continuar editando el proyecto.\n')
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise
