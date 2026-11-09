"""Compare isolated original/translated workspaces; repair proven tool defects only.

Reports describe the checks actually run. They do not certify arbitrary quests,
save-dependent Python, or all game branches. No local model is involved.
"""
from collections import Counter
from datetime import datetime, timezone
import ast
import hashlib
import io
import json
import os
import pickletools
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import struct
import time
import traceback
import uuid
import zlib

from engine import save_json


OWNED_SCRIPTS = frozenset(('zz_rpt_korean.rpy', 'zz_rpt_names.rpy', 'zz_rpt_layout.rpy',
                           'zz_rpt_reference.rpy', 'zz_rpt_hints.rpy', 'zz_rpt_language.rpy'))
PROBE_FILE = 'zzzz_rpt_validation_probe.rpy'
GENERATED_WRAPPER = re.compile(r'\{rpt_ref(?:=0\.55)?\}|\{/rpt_ref\}')
_ARTIFACT = re.compile(r'<(?:rpt|rtpt)\d+\s*/>|\[/?target_id(?:=[^\]]*)?\]')
_FRAME = re.compile(r'File "([^"]+)", line (\d+)')
_SHOWMENU = re.compile(r'\bShowMenu\(\s*(["\'])([^"\'\r\n]+)\1\s*[,)]')


def _read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else default


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _safe(root, relative):
    name = PurePosixPath(str(relative).replace('\\', '/'))
    if name.is_absolute() or '..' in name.parts or ':' in str(name):
        raise ValueError('Invalid validation path: ' + str(relative))
    target = (root / str(name)).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('Validation path escaped workspace')
    return target


def validate_final(row, text, cfg):
    """One cheap final-output check; do not compare repeated bilingual variables."""
    if cfg.get('engine') == 'rpgmaker':
        return []  # RPG control codes are checked by its existing adapter.
    errors = []
    source = row.get('source', '')
    if source.strip() and not text.strip():
        errors.append('empty rendered text')
    if _ARTIFACT.search(text) and not _ARTIFACT.search(source):
        errors.append('unrestored protocol marker in rendered text')
    wrappers = GENERATED_WRAPPER.findall(text)
    opens = sum(not value.startswith('{/') for value in wrappers)
    closes = sum(value.startswith('{/') for value in wrappers)
    if opens != closes:
        errors.append('generated reference wrapper mismatch')
    # Validate newly introduced numeric sizes only, leaving custom game tags alone.
    original = Counter(re.findall(r'\{size=([^{}]*)\}', source))
    for value, count in Counter(re.findall(r'\{size=([^{}]*)\}', text)).items():
        if count <= original[value] or value == '_rpt_reference_55':
            continue
        if not re.fullmatch(r'(?:[+\-]\d+|\d+|\*(?:\d+(?:\.\d*)?|\.\d+))', value):
            errors.append('invalid generated size: ' + value)
        elif not value.startswith(('+', '-')) and float(value.lstrip('*')) <= 0:
            errors.append('nonpositive generated size: ' + value)
    return list(dict.fromkeys(errors))


def _owned(relative, language):
    rel = PurePosixPath(str(relative).replace('\\', '/'))
    if rel.parts[:3] == ('game', 'tl', language):
        return True
    name = rel.name
    return len(rel.parts) == 2 and rel.parts[0] == 'game' and (
        name in OWNED_SCRIPTS or name.endswith('.rpyc') and name[:-1] in OWNED_SCRIPTS)


def _snapshot(project, cfg, target):
    """Clone only prepared code/runtime, retaining original read-only asset links."""
    from workspace_files import clone
    target.mkdir(parents=True, exist_ok=False)
    (target / 'data').mkdir()
    clone(project, target)
    # Runtime cfg can carry Events, callbacks and sets. This file describes only
    # the inspection project, never the live worker state or request payload.
    public = {key: cfg[key] for key in ('source', 'language', 'source_language', 'engine',
                                       'model', 'textbox_scale', 'show_reference',
                                       'language_corner', 'language_margin') if key in cfg}
    save_json(target / 'project.json', public)
    return target


def _baseline(project, workspace, cfg):
    """Remove generated patch; restore overlapping original loose inputs by hash."""
    stage = workspace / 'staging'
    language = cfg.get('language', 'korean')
    manifest = _read(project / 'data/source-manifest.json', {})
    evidence = {'confident': bool(manifest), 'issues': [], 'restored': [], 'removed': []}
    for path in list((stage / 'game').rglob('*')):
        if path.is_file() and _owned(path.relative_to(stage).as_posix(), language):
            rel = path.relative_to(stage).as_posix()
            path.unlink()
            evidence['removed'].append(rel)
    original_root = Path(cfg.get('source') or '')
    for relative, record in manifest.items():
        if not _owned(relative, language):
            continue
        # A translator support script present in the input is an already-patched
        # baseline. It cannot establish who introduced a later exception.
        original_name = PurePosixPath(relative).name
        if original_name in OWNED_SCRIPTS or original_name.endswith('.rpyc') and original_name[:-1] in OWNED_SCRIPTS:
            evidence['issues'].append({'kind': 'source_already_patched', 'file': relative})
            continue
        source = _safe(original_root, relative)
        expected = record.get('sha256') if isinstance(record, dict) else record
        if not source.is_file() or _digest(source) != expected:
            evidence['issues'].append({'kind': 'original_input_unavailable_or_changed', 'file': relative})
            continue
        dest = _safe(stage, relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        evidence['restored'].append(relative)
    evidence['confident'] = evidence['confident'] and not evidence['issues']
    return evidence


def _original_code_hashes(workspace, language):
    stage = workspace / 'staging'
    result = {}
    for root in (stage / 'game', stage / 'renpy', stage / 'lib'):
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if not path.is_file() or any(part in ('cache', 'saves', '__pycache__') for part in path.relative_to(stage).parts):
                continue
            rel = path.relative_to(stage).as_posix()
            if _owned(rel, language) or path.name == PROBE_FILE:
                continue
            # Runtime + original script bytes determine comparison equivalence.
            if path.suffix.lower() in ('.rpy', '.rpyc', '.rpym', '.rpymc', '.py', '.pyo', '.pyc', '.rpa', '.exe', '.dll', '.pyd'):
                result[rel] = _digest(path)
    return result


def _target_hints(payload, suffix, state):
    """Only menu-target identifiers, not AST execution or story extraction."""
    try:
        if suffix in ('.rpy', '.rpym', '.py'):
            values = [payload.decode('utf-8-sig')]
            declarations = set(re.findall(r'^\s*(?:screen|label)\s+([A-Za-z_]\w*)', values[0], re.M))
            state['present'].update(declarations & state['requested'])
        elif suffix in ('.rpyc', '.rpymc'):
            blocks = []
            if payload.startswith(b'RENPY RPC2'):
                position = 10
                while True:
                    slot, start, length = struct.unpack_from('<III', payload, position)
                    position += 12
                    if slot == 0:
                        break
                    if slot == 1:
                        blocks.append(zlib.decompress(payload[start:start+length]))
            else:
                blocks = [zlib.decompress(payload)]
            values = []
            for block in blocks:
                for _, value, _ in pickletools.genops(block):
                    if isinstance(value, bytes):
                        value = value.decode('utf-8', 'replace')
                    if isinstance(value, str):
                        if value in state['requested']:
                            state['present'].add(value)
                        values.append(value)
        else:
            return
        if any(re.search(r'\b(?:define_screen|load_string)\s*\(', value) for value in values):
            state['dynamic'] = True
    except Exception:
        state['complete'] = False


def _archive_code(path, targets=None):
    """Read only code payloads with a data-only RPA index, never game pickle AST."""
    from engine import IndexUnpickler
    from workspace_files import CODE
    with path.open('rb') as stream:
        header = stream.readline(80).split()
        if not header or header[0] not in (b'RPA-2.0', b'RPA-3.0'):
            raise ValueError('Unsupported code archive: ' + path.name)
        offset = int(header[1], 16)
        key = int(header[2], 16) if header[0] == b'RPA-3.0' else 0
        stream.seek(offset)
        index = IndexUnpickler(io.BytesIO(zlib.decompress(stream.read())), encoding='bytes').load()
        result = {}
        for raw, entries in index.items():
            name = raw.decode('utf-8') if isinstance(raw, bytes) else raw
            name = PurePosixPath(name)
            if name.is_absolute() or '..' in name.parts or ':' in str(name) or '\\' in str(name):
                raise ValueError('Unsafe archive member')
            if name.suffix.lower() not in CODE:
                continue
            chunks = []
            for entry in entries:
                start, length = entry[0] ^ key, entry[1] ^ key
                prefix = (entry[2] if len(entry) > 2 else b'') or b''
                if isinstance(prefix, str):
                    prefix = prefix.encode('latin-1')
                if len(prefix) > length:
                    raise ValueError('Invalid RPA prefix')
                stream.seek(start)
                block = stream.read(length - len(prefix))
                if len(block) != length - len(prefix):
                    raise ValueError('Truncated code archive')
                chunks.append(prefix + block)
            payload = b''.join(chunks)
            result[name.as_posix()] = hashlib.sha256(payload).hexdigest()
            if targets is not None:
                _target_hints(payload, name.suffix.lower(), targets)
        return result


def _source_authenticity(project, cfg, menu_targets=None):
    """A matching pair of damaged prepared copies is not an original baseline."""
    from workspace_files import CODE, SCRIPTS, SCRIPT_ARCHIVE
    source = Path(cfg.get('source') or '')
    stage = project / 'staging'
    language = cfg.get('language', 'korean')
    manifest = _read(project / 'data/source-manifest.json', {})
    issues = []
    expected = {}
    archive_count = 0
    requested = {item['target'] for item in menu_targets or []}
    targets = dict(requested=requested | {name+'_screen' for name in requested}, present=set(), complete=True, dynamic=False)
    try:
        compact_path = stage / 'game' / SCRIPT_ARCHIVE
        compact = _archive_code(compact_path) if compact_path.is_file() else {}
        code_suffixes = CODE | {'.exe', '.dll', '.pyd'}
        for name in sorted(manifest, reverse=True):
            if PurePosixPath(name).suffix.lower() != '.rpa':
                continue
            archive = _safe(source, name)
            members = _archive_code(archive, targets)
            if not members:
                continue  # Index only; no media bytes/hash are read.
            record = manifest[name]
            digest = record.get('sha256') if isinstance(record, dict) else record
            if _digest(archive) != digest:
                issues.append(dict(kind='source_code_archive_changed', file=name))
                continue
            archive_count += 1
            for member, sha in members.items():
                relative = 'game/' + member
                basename = PurePosixPath(member).name
                if basename in OWNED_SCRIPTS or basename.endswith('.rpyc') and basename[:-1] in OWNED_SCRIPTS:
                    issues.append(dict(kind='source_archive_already_patched', file=relative))
                # Same archive priority and loose-file override as preparation.
                if relative not in manifest and relative not in expected:
                    expected[relative] = sha
        loose_count = 0
        runtime_proof = False
        for name, record in manifest.items():
            if PurePosixPath(name).suffix.lower() not in code_suffixes or _owned(name, language):
                continue
            suffix = PurePosixPath(name).suffix.lower()
            if suffix in ('.rpyc', '.rpymc', '.pyc', '.pyo') and name[:-1] in manifest:
                continue  # Generated bytecode is checked through its source.
            digest = record.get('sha256') if isinstance(record, dict) else record
            original = _safe(source, name)
            if not original.is_file() or _digest(original) != digest:
                issues.append(dict(kind='source_code_unavailable_or_changed', file=name))
            elif name.startswith('game/'):
                _target_hints(original.read_bytes(), suffix, targets)
            expected[name] = digest
            loose_count += 1
            runtime_proof = runtime_proof or name.startswith(('renpy/', 'lib/'))
        # The manifest was written by our preparation code too. Independently
        # inventory the explicit source's loose code names so a capture omission
        # cannot be mistaken for an original missing definition.
        source_game = source / 'game'
        from workspace_files import SKIP_DIRS
        for directory, dirs, files in os.walk(source_game, followlinks=False):
            dirs[:] = [name for name in dirs if name.casefold() not in SKIP_DIRS]
            for filename in files:
                path = Path(directory) / filename
                relative = 'game/' + path.relative_to(source_game).as_posix()
                suffix = path.suffix.lower()
                if suffix not in CODE | {'.rpa'} or _owned(relative, language):
                    continue
                if relative in manifest:
                    continue
                if suffix in ('.rpyc', '.rpymc', '.pyc', '.pyo') and relative[:-1] in manifest:
                    continue
                if suffix == '.rpa' and not _archive_code(path):
                    continue
                issues.append(dict(kind='source_code_missing_from_capture_manifest', file=relative))
        if not runtime_proof:
            issues.append(dict(kind='runtime_manifest_missing'))
        for name, digest in expected.items():
            target = _safe(stage, name)
            archived = compact.get(name[5:]) if name.startswith('game/') else None
            if not (target.is_file() and _digest(target) == digest) and archived != digest:
                issues.append(dict(kind='prepared_original_code_missing_or_changed', file=name))
        allowed_compact = {name[5:]: sha for name, sha in expected.items()
                           if name.startswith('game/') and PurePosixPath(name).suffix.lower() in SCRIPTS}
        for name, digest in compact.items():
            if allowed_compact.get(name) != digest:
                issues.append(dict(kind='unexpected_prepared_archive_code', file='game/' + name))
        for path in (stage/'game').rglob('*'):
            if not path.is_file() or path.suffix.lower() not in CODE:
                continue
            name = path.relative_to(stage).as_posix()
            if _owned(name, language) or any(part in ('cache', 'saves', '__pycache__') for part in path.parts):
                continue
            if name not in expected and not (path.suffix.lower() in ('.rpyc', '.rpymc', '.pyc', '.pyo') and name[:-1] in expected):
                issues.append(dict(kind='unexpected_prepared_loose_code', file=name))
        return dict(verified=not issues and bool(expected), issues=issues,
                    code_files=len(expected), loose_code_files=loose_count, code_archives=archive_count,
                    target_hints_complete=targets['complete'], dynamic_targets=targets['dynamic'],
                    targets_present=sorted(targets['present']))
    except Exception as exc:
        return dict(verified=False, issues=issues+[dict(kind='source_proof_unavailable', error_type=type(exc).__name__, error=str(exc))])


def _menu_targets(project):
    from automatic import script_sources
    found = []
    for name, source in script_sources(project).items():
        for number, line in enumerate(source.splitlines(), 1):
            if line.lstrip().startswith('#'):
                continue
            for match in _SHOWMENU.finditer(line):
                found.append(dict(target=match[2], file=name, line=number))
    return found


def _render_rows(project, cfg, failures=None):
    from translation import read_catalog, rendered_text
    rows = []
    files = {}
    for row in read_catalog(project):
        relative = row.get('file', '')
        try:
            if relative not in files:
                path = _safe(project / 'staging/game/tl' / cfg.get('language', 'korean'), relative)
                files[relative] = path.read_text(encoding='utf-8-sig').splitlines()
            text = rendered_text(files[relative][row['line']], row)
        except (OSError, IndexError, ValueError, SyntaxError) as exc:
            if failures is None:
                raise ValueError('Rendered validation input is missing/stale: ' + row['id']) from exc
            failures.append(dict(id=row['id'], file=relative, line=row.get('line', 0) + 1,
                                 errors=['Rendered input unavailable: ' + type(exc).__name__ + ': ' + str(exc)]))
            continue
        rows.append(dict(id=row['id'], file=relative, line=row['line'] + 1,
                         kind=row.get('kind'), source=row['source'], text=text,
                         literal_index=row.get('literal_index', 0)))
    return rows


def _install_probe(workspace, rows, menus, language):
    from app_paths import resource
    helper = resource('validation_probe_runtime.py').read_text(encoding='utf-8')
    source = ("# Isolated validation only.\ninit 999 python:\n"
              "    config.label_overrides['_main_menu'] = '_rpt_validation_entry'\n"
              "    config.label_overrides['start'] = '_rpt_validation_entry'\n" +
              ''.join('    ' + line + '\n' for line in helper.splitlines()) +
              "\nscreen _rpt_validation_return(value=None):\n"
              "    timer .05 action Return(value)\n\n"
              "label _rpt_validation_entry:\n"
              "    $ _rpt_probe_main()\n"
              "    $ renpy.quit()\n")
    (workspace / 'staging/game' / PROBE_FILE).write_text(source, encoding='utf-8')
    save_json(workspace / 'data/probe-input.json', dict(rows=rows, menu_targets=menus, language=language))


def _parse_events(path):
    events = []
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    events.append(dict(check='probe_output', status='failed', error_type='InvalidProbeOutput', error='Incomplete probe record'))
    return events


def _execute(workspace, operation, timeout=180):
    """Run one engine command in its own folder; never run the source EXE."""
    from engine import find_launcher, select_windows_runtime, interpreter_args, owned_run
    from workspace_files import command
    stage = workspace / 'staging'
    launcher = find_launcher(stage)
    interpreter = select_windows_runtime(stage)
    log = workspace / 'data' / (operation + '.log')
    events = workspace / 'data/probe-events.jsonl'
    # Only this invocation's isolated products count. Earlier compile failures
    # and first-attempt events must not poison a successful repair recheck.
    for product in (stage / 'traceback.txt', stage / 'errors.txt', events):
        if product.is_file():
            product.unlink()
    args = [] if operation == 'render' else [operation]
    env = dict(os.environ, RENPY_PATH_TO_SAVES=str(workspace / 'data/saves'),
               RENPY_MULTIPERSISTENT=str(workspace / 'data/multipersistent'),
               RENPY_SKIP_SPLASHSCREEN='1', RENPY_DISABLE_FULLSCREEN='1',
               PYTHONDONTWRITEBYTECODE='1', RPT_VALIDATION_INPUT=str(workspace / 'data/probe-input.json'),
               RPT_VALIDATION_EVENTS=str(events))
    env.pop('RENPY_AUTO_LOAD', None)
    started = time.monotonic()
    result = dict(check=operation, status='passed', log=str(log), events=[], returncode=None, timeout_seconds=timeout)
    try:
        with log.open('w', encoding='utf-8') as stream:
            process = owned_run([*interpreter_args(interpreter), *command(workspace, launcher, args)],
                                cwd=stage, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result['returncode'] = process.returncode
        if process.returncode:
            result.update(status='failed', error_type='EngineExit', error='Engine exit ' + str(process.returncode))
    except subprocess.TimeoutExpired:
        result.update(status='failed', error_type='Timeout', error='Validation timed out')
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
    result['seconds'] = round(time.monotonic() - started, 3)
    if operation in ('render', 'lint'):
        result['events'] = _parse_events(events)
        if operation == 'render' and not any(event.get('check') == 'complete' for event in result['events']):
            result['status'] = 'failed'
            result.setdefault('error_type', 'ProbeIncomplete')
            result.setdefault('error', 'The rendering probe did not complete')
        if any(event.get('status') == 'failed' for event in result['events']):
            result['status'] = 'failed'
        if operation == 'lint' and not any(event.get('check') == 'lint_capture' and event.get('status') == 'passed' for event in result['events']):
            result.update(status='incomplete', error_type='LintCaptureUnavailable', error='Lint diagnostics could not be captured')
    errorfile = stage / 'traceback.txt'
    if errorfile.is_file():
        result['traceback'] = errorfile.read_text(encoding='utf-8-sig', errors='replace')
        result['status'] = 'failed'
    parsefile = stage / 'errors.txt'
    if parsefile.is_file():
        result['parse_errors'] = parsefile.read_text(encoding='utf-8-sig', errors='replace')
        result.setdefault('traceback', result['parse_errors'])
        result['status'] = 'failed'
    return result


def _failures(checks):
    found = []
    for check in checks:
        events = [e for e in check.get('events', []) if e.get('status') == 'failed']
        if events:
            for event in events:
                found.append(dict(event, operation=check['check']))
        elif check.get('status') == 'failed':
            found.append(check)
    return found


def _signature(failure):
    # Normalize only workspace prefixes/timestamps, never the actual missing
    # variable/label/tag. Similar exceptions at different operations differ.
    trace = failure.get('traceback', '')
    frames = [(str(PurePosixPath(path.replace('\\', '/'))).rsplit('/staging/', 1)[-1], int(line))
              for path, line in _FRAME.findall(trace)]
    error = failure.get('error', '')
    if trace:
        lines = [line.strip() for line in trace.splitlines() if line.strip()]
        if lines:
            errors = [line for line in lines if re.match(r'^(?:[\w.]+Error|[\w.]*Exception):', line)]
            error = errors[-1] if errors else lines[-1]
    diagnostic = trace
    for path, _ in _FRAME.findall(trace):
        diagnostic = diagnostic.replace(path, str(PurePosixPath(path.replace('\\', '/'))).rsplit('/staging/', 1)[-1])
    diagnostic = '\n'.join(line for line in diagnostic.splitlines()
                           if not re.match(r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\w{3}\s+\d', line.strip()))
    diagnostic_digest = hashlib.sha256(diagnostic.encode('utf-8')).hexdigest() if diagnostic else None
    return (failure.get('operation', failure.get('check')), failure.get('check'), failure.get('id'), failure.get('file'), failure.get('line'),
            failure.get('target'), failure.get('error_type'), error, tuple(frames), diagnostic_digest)


def _patch_frame(failure, language):
    paths = [path.replace('\\', '/') for path, _ in _FRAME.findall(failure.get('traceback', ''))]
    direct = failure.get('file', '').replace('\\', '/')
    paths.append(direct)
    return any(any(path.endswith('/' + name) or path == name for name in OWNED_SCRIPTS) or
               '/game/tl/' + language + '/' in '/' + path for path in paths)


def classify(baseline_checks, patched_checks, evidence):
    """Attribute exact matched outcomes, preserving unknown/mixed failures."""
    original = _failures(baseline_checks)
    patched = _failures(patched_checks)
    equivalent = (evidence.get('confident') and evidence.get('code_equal') and evidence.get('probe_equal')
                  and evidence.get('source_authenticity', {}).get('verified'))
    original_signatures = {_signature(item) for item in original}
    successes = {(event.get('check'), event.get('id'), event.get('target'))
                 for check in baseline_checks for event in check.get('events', [])
                 if event.get('status') == 'passed'}
    items = []
    for failure in patched:
        matched = _signature(failure) in original_signatures
        if not equivalent:
            owner, reason = 'unknown', 'Original snapshot equivalence was not established'
        elif matched:
            trace = failure.get('traceback', '').replace('\\', '/')
            original_source = evidence.get('source_authenticity', {})
            target = failure.get('target')
            structural_menu = (failure.get('check') == 'menu_target' and failure.get('error_type') == 'MissingMenuTarget'
                               and original_source.get('target_hints_complete') and not original_source.get('dynamic_targets')
                               and target not in original_source.get('targets_present', [])
                               and (target or '')+'_screen' not in original_source.get('targets_present', []))
            input_failure = (failure.get('operation', failure.get('check')) in ('compile', 'lint')
                             and PROBE_FILE not in trace and 'validation_probe_runtime' not in trace
                             and (bool(_FRAME.search(trace)) or failure.get('error_type') == 'LintIssue' and failure.get('file')))
            original_init = (failure.get('check') == 'render' and PROBE_FILE not in trace
                             and 'validation_probe_runtime' not in trace and bool(_FRAME.search(trace))
                             and not re.search(r'workspace[-_]bootstrap|workspace_runtime|could(?:n.t| not) (?:find|open)|FileNotFoundError', trace, re.I))
            if structural_menu or input_failure or original_init:
                owner, reason = 'original', 'The same original target or startup/compile code fails without the patch'
            else:
                owner, reason = 'unknown', 'The shared failure may be caused by the probe or unavailable game state'
        elif ((not original or (failure.get('check'), failure.get('id'), failure.get('target')) in successes)
              and (_patch_frame(failure, evidence.get('language', 'korean'))
                   or failure.get('known_generated_rule')
                   or failure.get('check') == 'text' and failure.get('id') in evidence.get('changed_text_ids', []))):
            owner, reason = 'ours', 'The matching original check succeeds; only the tool-owned code or rendered text differs'
        else:
            owner, reason = 'unknown', 'Different outcomes require a causal check; no repair was inferred'
        items.append(dict(failure, attribution=owner, reason=reason))
    owners = {item['attribution'] for item in items}
    if not patched:
        overall = 'none' if not original else 'unknown'
    elif len(owners) == 1:
        overall = owners.pop()
    else:
        overall = 'mixed'
    return dict(attribution=overall, failures=items, baseline_failures=original)


def _font_path_repairs(workspace, cfg, message):
    """Only correct a failed owned metadata path to an existing bundled font."""
    language = cfg.get('language', 'korean')
    path = workspace / 'staging/game/tl' / language / '_rpt_presentation.json'
    data = _read(path, {})
    fonts = data.get('fonts', {})
    names = {'NanumSquareNeo-Regular.ttf', 'NanumSquareNeo-Bold.ttf',
             'NanumPenScript-Regular.ttf', 'NotoSansCJKkr-Regular.otf'}
    changes = []
    for section, key in (('preferred', 'regular'), ('preferred', 'hand'), (None, 'fallback'), (None, 'bold')):
        mapping = fonts.get(section, {}) if section else fonts
        value = mapping.get(key)
        if not isinstance(value, str) or value not in message:
            continue
        basename = PurePosixPath(value.replace('\\', '/')).name
        canonical = 'tl/' + language + '/fonts/' + basename
        try:
            current_exists = _safe(workspace / 'staging/game', value).is_file()
        except ValueError:
            current_exists = False  # Do not inspect an old absolute/external path.
        if basename in names and value != canonical and not current_exists and _safe(workspace / 'staging/game', canonical).is_file():
            changes.append((mapping, key, canonical))
    return path, data, changes


def _legacy_reference_text(text, source):
    prefix = '\n{size=*0.55}{cps=0}'
    if prefix in source or text.count(prefix) != 1:
        return None
    suffix = re.search(r'\{/cps\}\{/size\}((?:\{(?:nw|done)(?:=[^{}]*)?\})*)$', text)
    if not suffix:
        return None
    return text[:suffix.start()].replace(prefix, '\n{rpt_ref=0.55}{cps=0}', 1) + '{/cps}{/rpt_ref}' + suffix[1]


def _mark_known_causes(workspace, cfg, checks, rows):
    by_id = {row['id']: row for row in rows}
    for check in checks:
        failures = [event for event in check.get('events', []) if event.get('status') == 'failed']
        if not failures and check.get('status') == 'failed':
            failures = [check]
        for item in failures:
            message = item.get('error', '') + '\n' + item.get('traceback', '')
            if _font_path_repairs(workspace, cfg, message)[2]:
                item['known_generated_rule'] = 'owned_font_path'
            row = by_id.get(item.get('id'))
            if row and ('size' in message or 'invalid literal for int' in message) and _legacy_reference_text(row['text'], row['source']) is not None:
                item['known_generated_rule'] = 'legacy_reference_wrapper'


def _repair_candidates(workspace, cfg, failures, rows=None):
    """Exact legacy tool output only. Never repair labels, variables or quests."""
    game = workspace / 'staging/game'
    proposals = []
    by_id = {row['id']: row for row in rows or []}
    for item in failures:
        if item.get('attribution') != 'ours':
            continue
        message = item.get('error', '') + '\n' + item.get('traceback', '')
        metadata, document, changes = _font_path_repairs(workspace, cfg, message)
        if changes:
            before = _digest(metadata)
            for mapping, key, value in changes:
                mapping[key] = value
            save_json(metadata, document)
            proposals.append(dict(file=metadata.relative_to(workspace / 'staging').as_posix(), rule='owned_font_path', before_sha256=before))
        row = by_id.get(item.get('id'))
        if row and item.get('known_generated_rule') == 'legacy_reference_wrapper':
            support = workspace / 'staging/game/zz_rpt_reference.rpy'
            replacement = _legacy_reference_text(row['text'], row['source'])
            if replacement is not None and support.is_file() and '_rpt_reference_sizes' in support.read_text(encoding='utf-8-sig'):
                from translation import QUOTED, quote
                path = _safe(game / 'tl' / cfg.get('language', 'korean'), row['file'])
                lines = path.read_text(encoding='utf-8-sig').splitlines(keepends=True)
                index = row['line'] - 1
                match = list(QUOTED.finditer(lines[index]))[row.get('literal_index', 0)]
                before = _digest(path)
                lines[index] = lines[index][:match.start()] + quote(replacement) + lines[index][match.end():]
                path.write_text(''.join(lines), encoding='utf-8')
                row['text'] = replacement
                proposals.append(dict(file=path.relative_to(workspace / 'staging').as_posix(), rule='legacy_reference_wrapper', before_sha256=before))
        layout = game / 'zz_rpt_layout.rpy'
        if ('ysize' in message and ('AttributeError' in message or item.get('error_type') == 'AttributeError')
                and layout.is_file() and 'style.say_window.ysize' in layout.read_text(encoding='utf-8-sig')):
            from layout_policy import install
            before = {p: p.read_bytes() for p in (layout, game / 'zz_rpt_korean.rpy') if p.is_file()}
            install(workspace, cfg)
            for path, raw in before.items():
                if path.read_bytes() != raw:
                    proposals.append(dict(file=path.relative_to(workspace / 'staging').as_posix(), rule='legacy_layout_style_read',
                                          before_sha256=hashlib.sha256(raw).hexdigest()))
        hints = game / 'zz_rpt_hints.rpy'
        if 'translate_string' in message and hints.is_file():
            text = hints.read_text(encoding='utf-8-sig')
            old = "globals().get('_rpt_choices', {}).get(caption, renpy.translate_string(caption))"
            if old in text and 'def _rpt_validation_translate(' not in text:
                # Only replace this historically emitted expression, retaining
                # the engine's real string translation rather than swallowing it.
                helper = ("    def _rpt_validation_translate(value):\n"
                          "        from renpy.translation import translate_string\n"
                          "        return translate_string(value)\n")
                marker = re.search(r'^init\s+\d+\s+python:\s*\n', text, re.M)
                if marker:
                    text = text[:marker.end()] + helper + text[marker.end():]
                    text = text.replace(old, "globals().get('_rpt_choices', {}).get(caption, _rpt_validation_translate(caption))")
                    old_sha = _digest(hints)
                    hints.write_text(text, encoding='utf-8')
                    proposals.append(dict(file='game/zz_rpt_hints.rpy', rule='legacy_hint_translation_api', before_sha256=old_sha))
    grouped = {}
    for item in proposals:
        if item['file'] in grouped:
            item['before_sha256'] = grouped[item['file']]['before_sha256']
        grouped[item['file']] = item
    return list(grouped.values())


def _commit_repairs(project, workspace, proposals, artifact):
    """Copy proven owned-file fixes atomically; restore every file on failure."""
    from atomic_files import replace_cache_file
    before = {}
    bytecode_before = {}
    for item in proposals:
        rel = item['file']
        if not _owned(rel, 'korean') and PurePosixPath(rel).name not in OWNED_SCRIPTS:
            raise ValueError('Automatic repair cannot modify a game-owned file')
        target = _safe(project / 'staging', rel)
        if item.get('before_sha256') and _digest(target) != item['before_sha256']:
            raise RuntimeError('Patch changed during validation; automatic repair was not applied: ' + rel)
        before[target] = target.read_bytes()
    backup = artifact / 'repair-backup'
    for target, raw in before.items():
        rel = target.relative_to(project / 'staging')
        dest = backup / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    try:
        for item in proposals:
            target = _safe(project / 'staging', item['file'])
            temporary = target.with_suffix(target.suffix + '.validation.tmp')
            temporary.write_bytes(_safe(workspace / 'staging', item['file']).read_bytes())
            replace_cache_file(temporary, target)
            derived = Path(str(target) + 'c')
            if target.suffix == '.rpy' and derived.is_file():
                # Stale compiled code must not survive a source change. Copy it
                # to the backup before removing only this tool-derived cache.
                dest = backup / derived.relative_to(project / 'staging')
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(derived, dest)
                bytecode_before[derived] = derived.read_bytes()
                derived.unlink()
    except BaseException:
        for target, raw in before.items():
            target.write_bytes(raw)
        for target, raw in bytecode_before.items():
            target.write_bytes(raw)
        raise


def _rpg_check(project, cfg, artifact, mode='static'):
    snapshot = project / 'data/rpg-source'
    base = project / 'staging'
    report = _read(project / 'data/rpg-output.json', {})
    files = report.get('files', [])
    control_pairs = 0
    js_checked = 0
    js_available = False
    js_checker = 'unavailable'
    json_checked = 0
    if not files:
        result = dict(status='incomplete', attribution='unknown', failures=[],
                      reason='No prepared RPG Maker output is available')
    else:
        checks = []
        from runtime_assets import runtime_executable
        node = runtime_executable('node')
        js_available = node.is_file()
        checker_environment = dict(os.environ)
        if js_available:
            js_checker = 'node'
        else:
            # Electron already ships Node. Run only its parser, without loading
            # the GUI app or requiring a separate system/development Node.
            from app_paths import ROOT
            electron = ROOT / '_desktop/RenPyTranslator-UI.exe'
            if electron.is_file():
                node = electron
                checker_environment['ELECTRON_RUN_AS_NODE'] = '1'
                checker_environment.pop('RPT_TOOL_ROOT', None)
                js_available = True
                js_checker = 'electron_node'
        def js_error(path):
            if not path.is_file():
                return dict(error_type='MissingFile', error='JavaScript input is unavailable')
            try:
                from native_process import run as native_run
                proc = native_run([str(node), '--check', str(path)], capture_output=True, timeout=20,
                                  env=checker_environment,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except Exception as exc:
                return dict(error_type=type(exc).__name__, error=str(exc))
            if proc.returncode:
                text = proc.stderr.decode('utf-8', 'replace')
                message = next((line.strip() for line in text.splitlines() if 'SyntaxError:' in line), 'JavaScript syntax check failed')
                location = re.search(r':(\d+)\s*$', text.splitlines()[0] if text.splitlines() else '')
                return dict(error_type='JavaScriptSyntaxError', error=message, line=int(location[1]) if location else None)
            return None
        def controls(document, original, name, pointer=''):
            nonlocal control_pairs
            if isinstance(document, dict):
                if isinstance(document.get('_rpt_original'), str) and isinstance(document.get('_rpt_translation'), str):
                    from rpgmaker_tokens import validate
                    control_pairs += 1
                    problems = validate(document['_rpt_original'], document['_rpt_translation'])
                    if problems:
                        same = isinstance(original, dict) and all(original.get(k) == document[k] for k in ('_rpt_original', '_rpt_translation'))
                        checks.append(dict(file=name, pointer=pointer, attribution='original' if same else 'ours',
                                           error_type='RPGControlMismatch', error='; '.join(problems)))
                for key, value in document.items():
                    controls(value, original.get(key) if isinstance(original, dict) else None, name, pointer+'/'+str(key))
            elif isinstance(document, list):
                for index, value in enumerate(document):
                    controls(value, original[index] if isinstance(original, list) and index < len(original) else None, name, pointer+'/'+str(index))
        for name in files:
            path = _safe(base, name)
            if path.suffix.lower() == '.js' and js_available:
                original = _safe(snapshot, name)
                before = js_error(original) if original.is_file() else None
                after = js_error(path)
                js_checked += 1
                if after:
                    owner = 'original' if before == after and original.is_file() and path.is_file() and _digest(original) == _digest(path) else 'ours' if before is None and (original.is_file() or path.name == 'RPT_Bilingual.js') else 'unknown'
                    checks.append(dict(file=name, attribution=owner, **after))
                continue
            if path.suffix.lower() != '.json':
                continue
            a = _safe(snapshot, name)
            json_checked += 1
            before = after = None
            original_doc = translated_doc = None
            try:
                original_doc = json.loads(a.read_text(encoding='utf-8-sig'))
            except Exception as exc:
                before = dict(error_type=type(exc).__name__, error=str(exc))
            try:
                translated_doc = json.loads(path.read_text(encoding='utf-8-sig'))
            except Exception as exc:
                after = dict(error_type=type(exc).__name__, error=str(exc))
            if after:
                same_bytes = a.is_file() and path.is_file() and _digest(a) == _digest(path)
                checks.append(dict(file=name, attribution='original' if before == after and same_bytes else 'ours' if before is None else 'unknown', **after))
            elif translated_doc is not None:
                controls(translated_doc, original_doc, name)
        requires_js = any(PurePosixPath(name).suffix.lower() == '.js' for name in files)
        complete = bool(json_checked or js_checked) and (not requires_js or js_available)
        result = dict(status='failed' if checks else 'passed' if complete else 'incomplete', attribution='mixed' if len({c['attribution'] for c in checks}) > 1 else checks[0]['attribution'] if checks else 'none', failures=checks)
    result.update(engine='rpgmaker', mode='static', requested_mode=mode, scope='rpgmaker_static',
                  coverage={'json': json_checked > 0, 'json_files': json_checked, 'runtime': 'not_run', 'plugin_syntax': 'checked' if js_checked else 'not_available',
                            'javascript_files': js_checked, 'javascript_checker': js_checker, 'control_pairs': control_pairs},
                  repairs=[], report=str(artifact / 'report.json'))
    save_json(artifact / 'report.json', result)
    save_json(project / 'data/patch-validation.json', result)
    return result


def run(project, cfg, mode='render', auto_fix=True):
    """Explicit user check. No inference, extraction or translation is started."""
    project = Path(project).resolve(strict=True)
    if mode not in ('static', 'compile', 'render'):
        raise ValueError('Validation mode must be static, compile or render')
    identity = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
    artifact = project / 'data/validation-runs' / identity
    artifact.mkdir(parents=True)
    if cfg.get('engine') == 'rpgmaker' or (project / 'data/rpgmaker-project.json').is_file():
        return _rpg_check(project, cfg, artifact, mode)
    rows = []
    final_errors = []
    result = dict(version=1, engine='renpy', mode=mode, scope='renpy_final_text' if mode=='static' else 'renpy_isolated_runtime',
                  status='incomplete', attribution='unknown',
                  report=str(artifact / 'report.json'), repairs=[], final_text_errors=final_errors,
                  coverage={'compile': False, 'lint': False, 'startup': False, 'screens': [],
                            'text_items': len(rows), 'dynamic_interpolation': 'not_evaluated',
                            'quest_branches': 'not_run', 'save_states': 'isolated'})
    try:
        rows = _render_rows(project, cfg, final_errors)
        final_errors.extend(dict(id=row['id'], file=row['file'], line=row['line'], errors=errors)
                            for row in rows if (errors := validate_final(row, row['text'], cfg)))
        result['coverage']['text_items'] = len(rows)
        if mode == 'static':
            result.update(status='failed' if final_errors else 'passed', attribution='unknown' if final_errors else 'none',
                          failures=[dict(item, attribution='unknown') for item in final_errors])
            save_json(artifact / 'report.json', result)
            save_json(project / 'data/patch-validation.json', result)
            print('Patch check: ' + result['status'] + '; report: ' + str(artifact / 'report.json'), flush=True)
            return result
        baseline = _snapshot(project, cfg, artifact / 'baseline')
        patched = _snapshot(project, cfg, artifact / 'patched')
        evidence = _baseline(project, baseline, cfg)
        menus = _menu_targets(project)
        evidence['source_authenticity'] = _source_authenticity(project, cfg, menus)
        language = cfg.get('language', 'korean')
        a = _original_code_hashes(baseline, language)
        b = _original_code_hashes(patched, language)
        evidence.update(code_equal=a == b, language=language, original_code_files=len(a),
                        changed_text_ids=[row['id'] for row in rows if row['text'] != row['source']])
        original_rows = [dict(row, text=row['source']) for row in rows]
        _install_probe(baseline, original_rows, menus, None)
        _install_probe(patched, rows, menus, language)
        original_input = _read(baseline / 'data/probe-input.json', {})
        translated_input = _read(patched / 'data/probe-input.json', {})
        def recipe(document):
            return dict(menu_targets=document.get('menu_targets'),
                        rows=[{key: row.get(key) for key in ('id', 'file', 'line', 'kind', 'source', 'literal_index')}
                              for row in document.get('rows', [])])
        evidence.update(probe_equal=(recipe(original_input) == recipe(translated_input)
                                     and _digest(baseline/'staging/game'/PROBE_FILE) == _digest(patched/'staging/game'/PROBE_FILE)),
                        allowed_variant_differences=['translation_language', 'rendered_text'])
        operations = ['compile', 'lint'] + (['render'] if mode == 'render' else [])
        baseline_checks = []
        patched_checks = []
        for operation in operations:
            timeout = min(1800, max(180, int(len(rows)*.03)+60)) if operation == 'render' else 180
            print('Patch check: baseline '+operation+(' ('+str(len(rows))+' text items)' if operation=='render' else ''), flush=True)
            baseline_checks.append(_execute(baseline, operation, timeout))
            print('Patch check: patched '+operation+(' ('+str(len(rows))+' text items)' if operation=='render' else ''), flush=True)
            patched_checks.append(_execute(patched, operation, timeout))
            if operation == 'compile' and any(check['status'] == 'failed' for check in (baseline_checks[-1], patched_checks[-1])):
                break
        _mark_known_causes(patched, cfg, patched_checks, rows)
        attribution = classify(baseline_checks, patched_checks, evidence)
        result.update(attribution, baseline=baseline_checks, patched=patched_checks, evidence=evidence)
        failures = result['failures']
        # Run known rules once in the isolated patched workspace. A complete
        # paired recheck must pass before any tool-owned source is committed.
        proposals = _repair_candidates(patched, cfg, failures, rows) if auto_fix else []
        if proposals:
            _install_probe(patched, rows, menus, language)
            retry_checks = [_execute(patched, operation, min(1800, max(180, int(len(rows)*.03)+60)) if operation=='render' else 180) for operation in operations]
            result['repair_recheck'] = retry_checks
            if (all(check['status'] == 'passed' for check in retry_checks) and not final_errors and evidence['confident']
                    and evidence['code_equal'] and evidence['source_authenticity']['verified']):
                _commit_repairs(project, patched, proposals, artifact)
                result['repairs'] = [dict(item, status='applied') for item in proposals]
                result['patched'] = retry_checks
                result['failures'] = []
            else:
                result['repairs'] = [dict(item, status='rejected') for item in proposals]
        selected_checks = result['patched']
        complete = len(selected_checks) == len(operations)
        success = complete and all(check['status'] == 'passed' for check in selected_checks) and not final_errors
        proof_ready = (evidence['confident'] and evidence['code_equal'] and evidence.get('probe_equal')
                       and evidence['source_authenticity']['verified'])
        result['status'] = ('passed' if success and proof_ready else
                            'incomplete' if not failures and not final_errors else 'failed')
        result['coverage'].update(compile=any(c['check'] == 'compile' for c in selected_checks),
                                  lint=any(c['check'] == 'lint' for c in selected_checks),
                                  startup=any(e.get('check') == 'startup' and e.get('status') == 'passed' for c in selected_checks for e in c.get('events', [])),
                                  screens=[e['check'][7:] for c in selected_checks for e in c.get('events', []) if e.get('check', '').startswith('screen:') and e.get('status') == 'passed'])
    except Exception as exc:
        result.update(status='failed', attribution='unknown', internal_error=dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc()))
    save_json(artifact / 'report.json', result)
    save_json(project / 'data/patch-validation.json', result)
    print('Patch check: ' + result['status'] + '; report: ' + str(artifact / 'report.json'), flush=True)
    return result
