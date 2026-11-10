"""Age-based cleanup of diagnostic logs throughout this portable application."""
import os
from pathlib import Path
import re
import stat
import time


RETENTION_DAYS = 7
_LOG_SUFFIX = re.compile(r'\.log(?:\.\d+|\.old|\.bak)?$', re.I)
_SKIP_DIRECTORIES = frozenset({
    '.git', '.venv', '__pycache__', 'node_modules', 'models', 'runtimes', 'app', 'desktop',
    'vendor', '_internal', '_desktop', 'portable', 'dist', 'backups',
    'saves', 'game', 'renpy', 'lib', 'images', 'audio', 'video', 'movies', 'fonts',
})
_TRACE_FILES = frozenset({
    'translation-requests.jsonl', 'metrics.jsonl', 'runtime-recovery.jsonl',
    'scene-errors.jsonl', 'context-history.jsonl', 'probe-events.jsonl',
})
_TRACE_REPORTS = frozenset({
    'last-model-response.json', 'last-invalid-model-response.json',
    'context-report.json', 'context-report.failed-repair.json',
    'scene-diagnostics.json', 'patch-validation.json',
})
_RENPY_LOGS = frozenset({'log.txt', 'traceback.txt', 'errors.txt'})
_DIAGNOSTIC_DIRECTORIES = frozenset({'logs', 'errors', 'runtime-diagnostics'})
_PRESERVED_FILES = frozenset({
    'translations.jsonl', 'translations.jsonl.tmp', 'recovered-translations.json',
    'reviewed-translations.json', 'project.json', 'project-link.json',
    'settings.json', 'desktop-settings.json', 'catalog.json', 'failed-items.json',
})


def _linked(path):
    return path.is_symlink() or path.is_junction()


def _root(path):
    root = Path(path).resolve(strict=True)
    if not root.is_dir() or root == root.parent:
        raise ValueError('Log cleanup requires an application directory')
    return root


def is_log(relative):
    """Recognize logs, not translation state or arbitrary JSON/JSONL files."""
    relative = Path(relative)
    name = relative.name.casefold()
    if name in _PRESERVED_FILES:
        return False
    if (_LOG_SUFFIX.search(name) or relative.suffix.casefold() in ('.evtx', '.wer')
            or name in _RENPY_LOGS or name in _TRACE_FILES or name in _TRACE_REPORTS):
        return True
    parents = {part.casefold() for part in relative.parts[:-1]}
    if parents & _DIAGNOSTIC_DIRECTORIES:
        return (relative.suffix.casefold() in ('.json', '.jsonl', '.ndjson', '.txt', '.dmp')
                or name.endswith(('.json.tmp', '.jsonl.tmp')))
    if 'validation-runs' in parents:
        return name in ('report.json', 'lint.txt')
    return False


def _safe_file(root, path):
    """Recheck containment and links immediately before removing one file."""
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if _linked(current):
            raise ValueError('Linked log path is outside cleanup scope')
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError('Log path escaped the application directory')
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Log cleanup only removes regular files')
    return info


def expired_logs(root, *, now=None, errors=None):
    """Return eligible file paths using names and metadata only; read no logs."""
    root = _root(root)
    cutoff = (time.time() if now is None else now) - RETENTION_DAYS * 86400
    errors = [] if errors is None else errors
    candidates = []

    def failed(error):
        errors.append({'path': str(getattr(error, 'filename', root)), 'error': str(error)})

    for directory, folders, files in os.walk(root, topdown=True, followlinks=False, onerror=failed):
        parent = Path(directory)
        folders[:] = [name for name in folders
                      if name.casefold() not in _SKIP_DIRECTORIES and not _linked(parent / name)]
        for name in files:
            path = parent / name
            if not is_log(path.relative_to(root)) or _linked(path):
                continue
            try:
                if _safe_file(root, path).st_mtime <= cutoff:
                    candidates.append(path)
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as error:
                errors.append({'path': str(path.relative_to(root)), 'error': str(error)})
    return candidates


def cleanup_logs(root=None, *, now=None):
    """Remove expired diagnostic files globally; busy files never stop translation."""
    if root is None:
        from app_paths import ROOT
        root = ROOT
    root = _root(root)
    now = time.time() if now is None else now
    cutoff = now - RETENTION_DAYS * 86400
    result = {'deleted': 0, 'bytes': 0, 'errors': []}
    for path in expired_logs(root, now=now, errors=result['errors']):
        try:
            info = _safe_file(root, path)
            # A running writer may have refreshed the log after discovery.
            if info.st_mtime > cutoff:
                continue
            path.unlink()
            result['deleted'] += 1
            result['bytes'] += info.st_size
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            result['errors'].append({'path': str(path.relative_to(root)), 'error': str(error)})
    if result['deleted'] or result['errors']:
        print('Log cleanup: '+str(result['deleted'])+' expired files removed; '
              +str(len(result['errors']))+' files skipped', flush=True)
    return result
