"""Conservative Unity serialized TextAsset discovery and verified repacking.

Only ResourceManager-linked locale paths in the main resources.assets are
considered. No bundle, media, StringTable, or Addressables decoding is done.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path


_LOCALE_ROOTS = frozenset(('lang', 'language', 'languages', 'localization',
                           'localisation', 'i18n', 'l10n', 'translations'))


class UnityAssetError(ValueError):
    error_code = 'unity_format'


def _unity():
    try:
        import UnityPy
    except ImportError as exc:
        raise UnityAssetError('UnityPy is unavailable') from exc
    return UnityPy


def _data_dir(root):
    root = Path(root)
    if not (root / 'UnityPlayer.dll').is_file():
        return None
    choices = sorted(p for p in root.glob('*_Data') if p.is_dir() and
                     (p / 'globalgamemanagers').is_file())
    if len(choices) != 1:
        return None
    return choices[0]


def inspect_source(root):
    data = _data_dir(root)
    if data is None:
        return None
    present = (data / 'resources.assets').is_file()
    return {'engine': 'unity', 'engine_label': 'Unity', 'supported': present,
            'details': '지원되는 언어별 TextAsset은 준비 단계에서 확인합니다.',
            'stop_message': '' if present else '이 Unity 빌드에서 지원되는 resources.assets를 찾지 못했습니다.'}


def _file(path):
    env = _unity().load(Path(path).read_bytes())
    files = list(env.files.values())
    if len(files) != 1 or not hasattr(files[0], 'objects'):
        raise UnityAssetError('Expected one Unity serialized file')
    return files[0]


def inspect_unity(root, *, asset_root=None):
    data = _data_dir(root)
    if data is None:
        raise UnityAssetError('Unity Windows data or main resources.assets not found')
    if asset_root is None:
        resource_path = data / 'resources.assets'
    else:
        try:
            snapshot = Path(asset_root).resolve(strict=True)
            resource_path = (snapshot / data.name / 'resources.assets').resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise UnityAssetError('Snapshot resources.assets not found') from exc
        if resource_path.parent != snapshot / data.name:
            raise UnityAssetError('Serialized asset outside snapshot root')
    if not resource_path.is_file():
        raise UnityAssetError('Unity Windows data or main resources.assets not found')
    manager = _file(data / 'globalgamemanagers')
    objects = [o for o in manager.objects.values() if o.type.name == 'ResourceManager']
    if len(objects) != 1:
        raise UnityAssetError('A unique ResourceManager is required')
    resources = _file(resource_path)
    manager_version = getattr(manager, 'unity_version', None)
    resource_version = getattr(resources, 'unity_version', None)
    if manager_version and resource_version and manager_version != resource_version:
        raise UnityAssetError('Unity versions of ResourceManager and resources.assets differ')
    candidates = defaultdict(dict)
    container = objects[0].read().m_Container
    base = {}
    for path, ptr in container:
        if int(ptr.m_FileID) > 0 and int(ptr.m_FileID) <= len(manager.externals):
            ext = manager.externals[int(ptr.m_FileID) - 1].path.replace('\\', '/')
            obj = resources.objects.get(int(ptr.m_PathID))
            if ext.lower() == 'resources.assets' and obj is not None and obj.type.name == 'TextAsset':
                base[path.lower()] = {'file': data.name + '/resources.assets', 'path_id': int(ptr.m_PathID), 'resource_path': path, 'name': obj.read().m_Name}
    for path, ptr in container:
        parts = path.replace('\\', '/').split('/')
        if len(parts) < 3 or parts[0].lower() not in _LOCALE_ROOTS:
            continue
        lang = parts[1].lower()
        if not re.fullmatch(r'[a-z]{2,3}(?:-[a-z0-9]{2,8})*', lang):
            continue
        file_id = int(ptr.m_FileID)
        if file_id < 1 or file_id > len(manager.externals):
            continue
        external = manager.externals[file_id - 1].path.replace('\\', '/')
        if external.lower() != 'resources.assets':
            continue
        obj = resources.objects.get(int(ptr.m_PathID))
        if obj is None or obj.type.name != 'TextAsset':
            continue
        suffix = '/'.join(parts[2:]).lower()
        entry = {'file': data.name + '/resources.assets', 'path_id': int(ptr.m_PathID),
                 'resource_path': path, 'name': obj.read().m_Name}
        key = (parts[0].lower(), suffix)
        if lang in candidates[key]:
            raise UnityAssetError('Duplicate locale resource path')
        candidates[key][lang] = entry
    groups = []
    for (prefix, suffix), entries in sorted(candidates.items()):
        if len(entries) >= 2:
            group = {'group': suffix if prefix == 'lang' else prefix + '/' + suffix, 'languages': {lang: [entry] for lang, entry in sorted(entries.items())}}
            if suffix in base:
                group['reference'] = base[suffix]
            groups.append(group)
    indicators = []
    assemblies = data / 'ScriptingAssemblies.json'
    if assemblies.is_file() and assemblies.stat().st_size <= 1024 * 1024:
        metadata = assemblies.read_text(encoding='utf-8-sig', errors='replace').lower()
        if 'unity.localization' in metadata:
            indicators.append('Unity Localization assembly is present; serialized StringTable is not supported.')
        if 'unity.addressables' in metadata:
            indicators.append('Addressables assembly is present; Addressables content is not supported.')
    aa = data / 'StreamingAssets' / 'aa'
    if aa.is_dir() and any(p.is_file() and p.name.lower().startswith('catalog') for p in aa.iterdir()):
        indicators.append('An Addressables catalog is present; Addressables content is not supported.')
    return {'data_dir': data.name, 'groups': groups, 'indicators': indicators,
            'exclusions': ['Serialized StringTable and Addressables are not supported.',
                           'Bundles, sidecars and unrelated TextAssets are not included.',
                           'Text payload format must be validated separately.']}


def _source(root, asset):
    root = Path(root).resolve(strict=True)
    rel = str(asset['file']).replace('\\', '/')
    parts = rel.split('/')
    if len(parts) != 2 or parts[0] in ('.', '..') or parts[1].lower() != 'resources.assets':
        raise UnityAssetError('Unsafe serialized asset path')
    path = (root / parts[0] / parts[1]).resolve(strict=True)
    if path.parent.parent != root:
        raise UnityAssetError('Serialized asset outside game root')
    return path


def capture_assets(root, assets):
    files = {}
    result = []
    for asset in assets:
        path = _source(root, asset)
        if path not in files:
            files[path] = _file(path)
        obj = files[path].objects.get(int(asset['path_id']))
        if obj is None or obj.type.name != 'TextAsset':
            raise UnityAssetError('TextAsset path ID not found')
        payload = obj.read().m_Script
        result.append(payload if isinstance(payload, bytes) else payload.encode('utf-8', 'surrogateescape'))
    return result


def capture_asset(root, asset):
    return capture_assets(root, [asset])[0]


def _externals(f):
    return [(x.path, x.guid, x.type, getattr(x, 'asset_path', None)) for x in f.externals]


def verify_asset(source_file, destination_file, replacements):
    replacements = {int(k): bytes(v) for k, v in replacements.items()}
    source, output = _file(source_file), _file(destination_file)
    if _externals(source) != _externals(output):
        raise UnityAssetError('External references changed')
    if set(source.objects) != set(output.objects):
        raise UnityAssetError('Object path IDs changed')
    if not set(replacements).issubset(source.objects):
        raise UnityAssetError('Replacement path ID missing')
    for path_id, original in source.objects.items():
        changed = output.objects[path_id]
        if (original.class_id, original.type_id) != (changed.class_id, changed.type_id):
            raise UnityAssetError('Object type changed')
        if path_id not in replacements:
            if original.get_raw_data() != changed.get_raw_data():
                raise UnityAssetError('Untouched object payload changed: %s' % path_id)
        else:
            if original.type.name != 'TextAsset':
                raise UnityAssetError('Replacement is not a TextAsset')
            before, after = original.read(), changed.read()
            if (before.m_Name != after.m_Name or before.m_PathName != after.m_PathName or
                after.m_Script.encode('utf-8', 'surrogateescape') != replacements[path_id]):
                raise UnityAssetError('TextAsset content or metadata mismatch')
    return True


def repack_asset(source_file, destination_file, replacements):
    source_path = Path(source_file).resolve(strict=True)
    dest_path = Path(destination_file).resolve()
    if source_path == dest_path:
        raise UnityAssetError('Destination must differ from the original file')
    replacements = {int(k): bytes(v) for k, v in replacements.items()}
    if not replacements:
        raise UnityAssetError('No TextAsset replacements supplied')
    f = _file(source_path)
    for path_id, payload in replacements.items():
        obj = f.objects.get(path_id)
        if obj is None or obj.type.name != 'TextAsset':
            raise UnityAssetError('Replacement path ID is not a TextAsset')
        value = obj.read()
        value.m_Script = payload.decode('utf-8', 'surrogateescape')
        value.save()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.unity-', suffix='.assets', dir=str(dest_path.parent))
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(f.save())
        verify_asset(source_path, temp, replacements)
        os.replace(temp, dest_path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return str(dest_path)
