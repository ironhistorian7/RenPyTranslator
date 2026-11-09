"""Small writable engine/script workspace; original media stays at its input root."""
import json
import os
from pathlib import Path, PurePosixPath
import pickle
import shutil
import zlib

from engine import save_json, select_windows_runtime

CODE = {'.rpy', '.rpyc', '.rpym', '.rpymc', '.py', '.pyc', '.pyo', '.rpe'}
INPUTS = CODE | {'.pyd', '.dll', '.json', '.txt', '.csv', '.tsv', '.ini', '.cfg', '.xml',
                 '.yaml', '.yml', '.toml', '.ttf', '.otf', '.ttc', '.otc'}
SCRIPTS = {'.rpy', '.rpyc', '.rpym', '.rpymc'}
RECORD = 'workspace-files.json'
SCRIPT_ARCHIVE = '_rpt_original_scripts.rpa'
SKIP_DIRS = {'cache', 'saves', '__pycache__', '.git', 'context_test'}


def metadata(project):
    path = project / 'data' / RECORD
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def plan(source, require_runtime=True):
    """Inspect names/sizes only; no image/audio/video contents or other roots."""
    source = Path(source)
    runtime = select_windows_runtime(source).parent.name if require_runtime else None
    selected = []; assets = []; archives = []; skipped_bytes = 0; skipped_count = 0
    for folder, dirs, files in os.walk(source, followlinks=False):
        relative = Path(folder).relative_to(source)
        parts = relative.parts
        dirs[:] = [d for d in dirs if d.casefold() not in SKIP_DIRS]
        if parts == ('lib',) and runtime is not None:
            # Shared stdlib remains; other platform runtimes do not.
            dirs[:] = [d for d in dirs if not any(p in d.casefold() for p in
                         ('windows', 'linux', 'mac', 'darwin', 'android', 'ios')) or d == runtime]
        if not parts:
            dirs[:] = [d for d in dirs if d in ('game', 'renpy', 'lib')]
        for name in dirs:
            path=Path(folder)/name
            if path.is_symlink() or path.is_junction():
                raise ValueError('Linked workspace input: '+str(path))
        for name in sorted(files):
            path = Path(folder) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError('Linked workspace input: ' + str(path))
            rel = path.relative_to(source).as_posix()
            suffix = path.suffix.lower()
            if parts and parts[0] in ('renpy', 'lib'):
                selected.append(rel)
            elif parts and parts[0] == 'game':
                if suffix == '.rpa':
                    archives.append(rel)
                    skipped_bytes += path.stat().st_size; skipped_count += 1
                elif suffix in INPUTS:
                    selected.append(rel)
                else:
                    assets.append(path.relative_to(source / 'game').as_posix())
                    skipped_bytes += path.stat().st_size; skipped_count += 1
            elif name.casefold() not in ('log.txt','traceback.txt','errors.txt') and suffix in CODE | {'.exe', '.dll', '.json', '.txt', '.ini', '.cfg'}:
                selected.append(rel)
    return dict(version=1, mode='scripts_and_runtime', source_root=str(source.resolve()),
                source_game=str((source / 'game').resolve()), copied_files=selected,
                loose_assets=assets, archives=archives, copied_count=len(selected),
                skipped_count=skipped_count, skipped_bytes=skipped_bytes,
                runtime_directory=runtime, status='copying')


def input_paths(selection):
    # Archive bytes are immutable inputs even though they are not copied.
    return set(selection['copied_files']) | set(selection['archives'])


def input_ignore(paths):
    parents = {p for name in paths for p in
               (str(parent) for parent in PurePosixPath(name).parents) if p != '.'}
    return lambda name: name not in paths and name not in parents


def script_archive(paths, game):
    """Retain original compiled nodes in a small RPA, never a media archive copy."""
    target = game / SCRIPT_ARCHIVE
    temporary = target.with_suffix('.rpa.tmp')
    index = {}
    with temporary.open('wb') as stream:
        stream.write(b'RPA-3.0 0000000000000000 00000000\n')
        for path in sorted(paths):
            if path.suffix.lower() not in SCRIPTS:
                continue
            payload = path.read_bytes()
            index[path.relative_to(game).as_posix()] = [(stream.tell(), len(payload))]
            stream.write(payload)
        offset = stream.tell()
        stream.write(zlib.compress(pickle.dumps(index, protocol=2)))
        stream.seek(0)
        stream.write(('RPA-3.0 %016x 00000000\n' % offset).encode('ascii'))
    temporary.replace(target)
    return sorted(index)


def create(project, source, selection=None):
    """New/unfinished preparation only. Never shrink an existing user project."""
    from engine import extract_scripts
    selection = selection or plan(source)
    stage = project / 'staging'; game = stage / 'game'
    game.mkdir(parents=True, exist_ok=True)
    save_json(project / 'data' / RECORD, selection)
    for name in selection['copied_files']:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    extracted = []; seen = set()
    # Match the loader: later archive names have priority; loose files win.
    for name in sorted(selection['archives'], reverse=True):
        if Path(name).name == SCRIPT_ARCHIVE:
            # A comparison of a small workspace must retain its original nodes.
            extracted += extract_scripts(source / name, game, extensions=INPUTS)
        else:
            extracted += extract_scripts(source / name, game, seen, extensions=INPUTS)
    original_scripts = script_archive(extracted, game)
    # Reference inputs are assets only. Runtime bridge excludes external code.
    selection.update(status='ready', original_archive_scripts=original_scripts,
                     script_archive_bytes=(game / SCRIPT_ARCHIVE).stat().st_size)
    save_json(project / 'data' / RECORD, selection)
    print('Workspace: %d files copied; %d assets/archives not copied (%.2f GiB); script archive %.2f MiB' %
          (len(selection['copied_files']), selection['skipped_count'],
           selection['skipped_bytes'] / 2**30, selection['script_archive_bytes'] / 2**20), flush=True)
    return extracted


def command(project, launcher, args):
    """Legacy full workspaces keep their existing command unchanged."""
    data = metadata(project)
    if data is None:
        return [str(launcher), str(project / 'staging'), *args]
    assets = Path(data['source_game'])
    if (data['loose_assets'] or data['archives']) and not assets.is_dir():
        raise FileNotFoundError('Workspace assets are read from the original folder; restore it: ' + str(assets))
    helper = project / 'data' / 'workspace-bootstrap.py'
    helper.write_text(Path(__file__).with_name('workspace_runtime.py').read_text(encoding='utf-8'), encoding='utf-8')
    return [str(helper), str(project / 'data' / RECORD), str(launcher), str(project / 'staging'), *args]


def clone(project, target):
    """Comparison copies prepared code only; no duplicate original media."""
    source = project / 'staging'
    existing = metadata(project)
    selection=plan(source,require_runtime=(source/'lib').exists())
    extracted = create(target, source, selection)
    if existing:
        # The reference remains the prepared input snapshot's media root.
        current = metadata(target)
        current.update(source_root=existing['source_root'], source_game=existing['source_game'],
                       loose_assets=existing['loose_assets'], archives=existing['archives'],
                       original_archive_scripts=existing['original_archive_scripts'])
        save_json(target / 'data' / RECORD, current)
        # No recovered source is needed for compiled original archive entries.
        # Reuse the input project's exact compact archive, not its generated nodes.
        shutil.copy2(source / 'game' / SCRIPT_ARCHIVE, target / 'staging/game' / SCRIPT_ARCHIVE)
        for path in extracted:
            if path.suffix.lower() in SCRIPTS and path.exists():
                path.unlink()


def external_archives(project):
    data = metadata(project)
    if not data:
        return []
    return [Path(data['source_root']) / name for name in data['archives']
            if Path(name).name != SCRIPT_ARCHIVE]


def media_names(project):
    """Filename/index evidence remains available after eliminating media copies."""
    data = metadata(project)
    if not data:
        return [], []
    from story_media import asset_index
    names, warnings = asset_index(Path(data['source_game']))
    return names, warnings
