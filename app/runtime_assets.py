"""Pinned portable assets. No dependence on a machine-wide installation."""
import json
from pathlib import Path


def load_lock(root=None):
    from app_paths import ROOT
    path = Path(root or ROOT) / 'runtime-lock.json'
    # Fixture roots can resolve the shipped versions without needing a full bundle.
    if not path.is_file():
        path = ROOT / 'runtime-lock.json'
    return json.loads(path.read_text(encoding='utf-8'))


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Asset path must stay below tool root: ' + str(relative))
    return path


def runtime_directory(name, root=None):
    from app_paths import ROOT
    return inside(root or ROOT, load_lock(root)[name]['directory'])


def runtime_executable(name, root=None):
    return runtime_directory(name, root) / load_lock(root)[name]['executable']
