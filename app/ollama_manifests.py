"""Read the portable Ollama store without starting a daemon or loading weights.

Ollama 0.40 uses manifests-v2/ollama.com and manifest-list references. Older
stores use manifests/registry.ollama.ai. Keep their naming and blob graph in
one place; callers must not infer blob ownership from one directory alone.
"""
import hashlib
import json
import os
from pathlib import Path
import re


MODEL_LAYER = 'application/vnd.ollama.image.model'
MANIFEST_TYPES = {'application/vnd.docker.distribution.manifest.v2+json',
                  'application/vnd.ollama.manifest.list.v2+json',
                  'application/vnd.oci.image.manifest.v1+json',
                  'application/vnd.oci.image.index.v1+json'}
PUBLIC_HOSTS = {'registry.ollama.ai', 'ollama.com'}
_DIGEST = re.compile(r'sha256:[a-fA-F0-9]{64}')
_RUNNERS = {'llamacpp': 0, 'ggml': 1, 'mlx': 2, '': 3}


class ManifestError(ValueError):
    pass


def alias_from_parts(parts):
    """Use Ollama's short public names, preserving private registry namespaces."""
    if len(parts) < 4:
        raise ManifestError('모델 등록 경로를 읽을 수 없습니다.')
    host, namespace = parts[:2]
    model = '/'.join(parts[2:-1])
    prefix = '' if host.lower() in PUBLIC_HOSTS else host+'/'
    if prefix or namespace != 'library':
        prefix += namespace+'/'
    return prefix+model+':'+parts[-1]


class ManifestStore:
    def __init__(self, root, checked=None, runtime_version=None):
        self.root = Path(root)
        self.checked = checked or self._inside
        version = re.match(r'^v?(\d+)\.(\d+)\.(\d+)', str(runtime_version or ''))
        # v0.40 introduced the v2 store. An explicitly older daemon cannot see
        # it, even when the UI's combined inventory contains the same alias.
        self.legacy_runtime = bool(version and tuple(map(int, version.groups())) < (0, 40, 0))
        self.runtime_version = runtime_version
        self._names = None
        self._documents = {}
        self._models = {}

    def _inside(self, path):
        path = Path(path)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ManifestError('모델 등록 경로가 저장 폴더 밖에 있습니다.')
        for parent in (path, *path.parents):
            if parent == self.root:
                break
            if parent.is_symlink() or parent.is_junction():
                raise ManifestError('연결된 모델 등록 경로는 지원하지 않습니다.')
        return path

    def names(self):
        """Return all named paths, v2 first, merging the two public host names."""
        if self._names is not None:
            return self._names
        names = {}
        for directory in ('manifests-v2', 'manifests'):
            base = self.root/directory
            if not base.exists():
                continue
            self.checked(base)
            for current, dirs, files in os.walk(base, followlinks=False):
                allowed = []
                for name in sorted(dirs):
                    try:
                        self.checked(Path(current)/name)
                        allowed.append(name)
                    except (OSError, ValueError):
                        continue
                dirs[:] = allowed
                for filename in sorted(files):
                    path = Path(current)/filename
                    try:
                        self.checked(path)
                        alias = alias_from_parts(path.relative_to(base).parts)
                    except (OSError, ValueError):
                        continue
                    names.setdefault(alias, []).append(path)
        self._names = names
        return names

    def paths(self):
        return {name: paths[0] for name, paths in self.names().items()}

    def model_path(self, name):
        paths = self.names().get(name, [])
        if self.legacy_runtime:
            paths = [path for path in paths if path.is_relative_to(self.root/'manifests')]
        if not paths:
            message = (self.missing_runtime_message(name, self.runtime_version) if name in self.names()
                       else '선택한 모델의 로컬 등록 파일을 찾을 수 없습니다: '+str(name))
            raise ManifestError(message)
        return paths[0]

    def blob(self, digest):
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ManifestError('모델 등록 정보의 파일 식별자가 올바르지 않습니다.')
        return self.checked(self.root/'blobs'/digest.lower().replace(':', '-'))

    def _read(self, path):
        path = self.checked(path)
        if path not in self._documents:
            try:
                # A reference must be a small JSON document, never weight data.
                if path.stat().st_size > 8*1024*1024:
                    raise ManifestError('모델 등록 JSON의 크기가 너무 큽니다: '+path.name)
                raw = path.read_bytes()
                document = json.loads(raw)
            except (UnicodeError, ValueError) as exc:
                raise ManifestError('모델 등록 정보를 읽을 수 없습니다: '+path.name) from exc
            if not isinstance(document, dict):
                raise ManifestError('모델 등록 정보가 올바르지 않습니다: '+path.name)
            self._documents[path] = document, 'sha256:'+hashlib.sha256(raw).hexdigest()
        return self._documents[path]

    def _tree(self, document, references, leaves, active, *, strict, metadata=None):
        metadata = metadata or {}
        if not isinstance(document, dict):
            raise ManifestError('모델 등록 정보의 하위 항목이 올바르지 않습니다.')
        inherited = {key: document.get(key) or metadata.get(key, '') for key in ('runner', 'format')}
        # List entries can be either inline manifests or references to JSON blobs.
        if document.get('digest') and 'config' not in document and 'layers' not in document and 'manifests' not in document:
            path = self.blob(document['digest'])
            digest = document['digest'].lower()
            references.add(digest)
            if digest in active:
                raise ManifestError('모델 등록 정보의 참조가 순환합니다.')
            try:
                child, _ = self._read(path)
            except FileNotFoundError:
                if strict:
                    raise ManifestError('하위 모델 등록 파일이 없습니다: '+path.name) from None
                return
            return self._tree(child, references, leaves, active | {digest}, strict=strict, metadata=inherited)
        if 'manifests' in document:
            children = document['manifests']
            if not isinstance(children, list) or not children:
                raise ManifestError('모델 등록 목록이 비어 있거나 올바르지 않습니다.')
            for child in children:
                self._tree(child, references, leaves, active, strict=strict, metadata=inherited)
            return
        config, layers = document.get('config'), document.get('layers')
        if not isinstance(config, dict) or not isinstance(layers, list):
            raise ManifestError('모델 등록 정보에 구성 또는 가중치 목록이 없습니다.')
        for asset in (config, *layers):
            if not isinstance(asset, dict):
                raise ManifestError('모델 등록 정보의 파일 목록이 올바르지 않습니다.')
            self.blob(asset.get('digest'))
            references.add(asset['digest'].lower())
            # Legacy downgrade anchors can retain manifest blobs as layers.
            if asset.get('mediaType') in MANIFEST_TYPES:
                self._tree({'digest': asset['digest']}, references, [], active,
                           strict=strict)
        leaves.append(dict(document, **inherited))

    def _path_tree(self, path, *, strict):
        document, digest = self._read(path)
        references, leaves = set(), []
        if path.is_relative_to(self.root/'manifests-v2'):
            references.add(digest)  # v2 named files are copies of manifest blobs on Windows.
        self._tree(document, references, leaves, set(), strict=strict)
        return leaves, references

    def model(self, name):
        """Resolve the active Windows runner and its flat config/layers view."""
        if name in self._models:
            return self._models[name]
        path = self.model_path(name)
        document, _ = self._read(path)
        if self.legacy_runtime and 'manifests' in document:
            raise ManifestError('선택한 Ollama '+str(self.runtime_version)+
                                '에서 모델 등록 목록을 읽을 수 없습니다. 최신 추론 런타임을 선택해 주세요.')
        leaves, _ = self._path_tree(path, strict=False)
        # Same platform preference as Ollama on Windows. Missing reference blobs
        # can fall through to another runner; malformed documents cannot.
        candidates = [leaf for leaf in leaves if str(leaf.get('runner', '')).lower() in _RUNNERS]
        if not candidates:
            raise ManifestError('이 모델에 사용할 수 있는 로컬 실행 형식이 없습니다: '+str(name))
        manifest = min(candidates, key=lambda leaf: _RUNNERS[str(leaf.get('runner', '')).lower()])
        weight = next((layer for layer in manifest['layers'] if layer.get('mediaType') == MODEL_LAYER), None)
        if weight is None:
            raise ManifestError('선택한 모델에 GGUF 가중치 정보가 없습니다: '+str(name))
        assets = [manifest['config'], *manifest['layers']]
        ready = all(self.blob(asset['digest']).is_file() and
                    (not asset.get('size') or self.blob(asset['digest']).stat().st_size == asset['size'])
                    for asset in assets)
        result = dict(name=name, path=path, manifest=manifest, weight=weight, ready=ready)
        self._models[name] = result
        return result

    def references(self, name):
        """Include every variant and shadowed legacy anchor before deleting."""
        result = set()
        for path in self.names().get(name, []):
            _, references = self._path_tree(path, strict=True)
            result.update(references)
        return result

    def deletion_plan(self, name):
        paths = self.names().get(name)
        if not paths:
            raise ManifestError('설치된 모델을 찾을 수 없습니다.')
        candidates = self.references(name)
        shared = set()
        for other in self.names():
            if other != name:
                shared.update(self.references(other))
        return tuple(paths), candidates-shared

    def missing_runtime_message(self, name, version):
        paths = self.names().get(name, [])
        if paths and all(path.is_relative_to(self.root/'manifests-v2') for path in paths):
            return ('선택한 Ollama '+str(version or '')+'에서 새 형식으로 등록된 모델을 찾지 못했습니다. '
                    'AI 모델 탭에서 최신 추론 런타임을 선택해 주세요.')
        return 'Model is not installed in the tool model store: '+str(name)
