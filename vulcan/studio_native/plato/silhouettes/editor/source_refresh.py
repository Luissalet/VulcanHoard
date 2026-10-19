"""Refresh packaged PNGs from explicitly configured local collection roots."""
from __future__ import annotations

import hashlib
import json
import threading
import zipfile
from pathlib import Path

from .asset_adapter import AssetImportError, import_asset, MAX_FILE_BYTES

_lock = threading.RLock()
_packages = {}


def source_paths(doc, config_path):
    """Resolve by project/asset identity, never by filename across generations.

    Paths are read from server-owned configuration, not from an uploaded document.
    Packages remain portable and contain no machine-specific source paths.
    """
    if not config_path.is_file():
        return {}, False
    config = json.loads(config_path.read_text(encoding='utf8'))
    records = []
    with _lock:
        active = set()
        for collection in config.get('collections', []):
            root, artwork = Path(collection['projects']).resolve(), Path(collection['artwork']).resolve()
            for package in root.glob('*/*.silhouettes'):
                if not package.parent.name[:1].isdigit():
                    continue
                active.add(package)
                try:
                    stat = package.stat()
                    signature = (stat.st_size, stat.st_mtime_ns, str(artwork))
                    cached = _packages.get(package)
                    if not cached or cached[0] != signature:
                        with zipfile.ZipFile(package) as archive:
                            if archive.getinfo('document.json').file_size > 32 * 1024 * 1024:
                                continue
                            saved = json.loads(archive.read('document.json'))
                        assets = {}
                        for aid, asset in saved.get('assets', {}).items():
                            name = asset.get('source_filename', '')
                            if asset.get('source_type') != 'png' or not name or '/' in name or '\\' in name:
                                continue
                            path = (artwork / name).resolve()
                            if path.parent == artwork:
                                assets[aid] = (name, path)
                        cached = signature, (saved.get('id'), assets)
                        _packages[package] = cached
                    records.append(cached[1])
                except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                    continue
        for path in set(_packages) - active:
            del _packages[path]
    exact = [record for record in records if record[0] == doc['id']]
    candidates = exact or records
    paths = {}
    for aid, asset in doc.get('assets', {}).items():
        matches = {assets[aid][1] for _, assets in candidates
                   if aid in assets and assets[aid][0] == asset.get('source_filename')}
        if len(matches) == 1:
            paths[aid] = matches.pop()
    return paths, True


def refresh_sources(doc, assets_bytes, paths):
    """Replace only changed PNG geometry; preserve layers, hierarchy and placement."""
    updated, warnings = [], []
    for aid, old in list(doc['assets'].items()):
        if old.get('source_type') != 'png':
            continue
        name = old.get('source_filename', aid)
        source = paths.get(aid)
        if source is None:
            warnings.append(f'{name}: no se ha localizado un original inequívoco; se conserva la copia guardada.')
            continue
        try:
            if source.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('El PNG supera el tamaño permitido.')
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() == old.get('source_sha256'):
                assets_bytes[aid] = data
                continue
            imported = import_asset(data, name, mm_per_source_unit=old.get('mm_per_source_unit'),
                                    smoothing=old.get('trace_settings', {}).get('smoothing', True))
            replacement = imported.to_document_asset()
            replacement['canonical_svg'] = imported.canonical_svg
            replacement['name'] = old.get('name', replacement['name'])
            # The editor normalizes geometry around its centre; layer poses and
            # physical pixel scale remain unchanged and no fitting is performed.
            doc['assets'][imported.asset_id] = replacement
            del doc['assets'][aid]
            for layer in doc['layers'].values():
                if layer['asset_id'] == aid:
                    layer['asset_id'] = imported.asset_id
            assets_bytes.pop(aid, None)
            assets_bytes[imported.asset_id] = data
            updated.append(name)
        except (OSError, ValueError, AssetImportError) as error:
            warnings.append(f'{name}: no se pudo actualizar ({error}); se conserva la copia guardada.')
    return updated, warnings


def persist_assets(doc, assets_bytes, directory):
    for aid, asset in doc['assets'].items():
        target = directory / aid
        target.mkdir(parents=True, exist_ok=True)
        if aid in assets_bytes:
            (target / f"source.{asset['source_type']}").write_bytes(assets_bytes[aid])
        if asset.get('canonical_svg'):
            (target / 'canonical.svg').write_text(asset['canonical_svg'], encoding='utf8')
