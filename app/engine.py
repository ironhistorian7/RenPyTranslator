"""Read only the explicit input root; mutate only the explicit project root."""
import hashlib
import ast
import io
import json
import os
from pathlib import Path, PurePosixPath
import pickle
import platform
import re
import shutil
import subprocess
import sys
import tokenize
import zlib

from app_paths import ROOT,cli_command

def owned_run(cmd,**kwargs):
    """GUI cancellation can stop only this process and its children."""
    from cancel_runtime import register,unregister
    timeout=kwargs.pop('timeout',None);check=kwargs.pop('check',False)
    proc=subprocess.Popen(cmd,**kwargs);register(proc)
    try:
        try:code=proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            raise
        if check and code:raise subprocess.CalledProcessError(code,cmd)
        return subprocess.CompletedProcess(cmd,code)
    finally:unregister(proc)

def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def manifest(root, ignore=None):
    result = {}
    for directory, dirs, files in os.walk(root,followlinks=False):
        if ignore is not None:
            dirs[:] = [name for name in dirs if not ignore((Path(directory)/name).relative_to(root).as_posix())]
            files = [name for name in files if not ignore((Path(directory)/name).relative_to(root).as_posix())]
        for name in dirs+files:
            path=Path(directory)/name
            if path.is_symlink() or path.is_junction():raise ValueError(f'Refusing linked input: {path}')
        for name in sorted(files):
            path=Path(directory)/name
            result[path.relative_to(root).as_posix()] = {'size': path.stat().st_size, 'sha256': sha(path)}
    return result

class IndexUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise ValueError(f'Non-data archive index: {module}.{name}')

def extract_scripts(archive, destination, archive_seen=None, extensions=None):
    extracted = []
    with archive.open('rb') as f:
        header = f.readline(80).split()
        if header[0] == b'RPA-3.0':
            offset, key = int(header[1], 16), int(header[2], 16)
        elif header[0] == b'RPA-2.0':
            offset, key = int(header[1], 16), 0
        else:
            raise ValueError(f'Unsupported archive format: {archive}')
        f.seek(offset)
        index = IndexUnpickler(io.BytesIO(zlib.decompress(f.read())), encoding='bytes').load()
        for rawname, entries in index.items():
            name = rawname.decode('utf-8') if isinstance(rawname, bytes) else rawname
            rel = PurePosixPath(name)
            if rel.is_absolute() or '..' in rel.parts or ':' in name or '\\' in name:
                raise ValueError(f'Unsafe archive path: {name}')
            if rel.suffix.lower() not in (extensions or {'.rpyc', '.rpymc', '.rpy', '.rpym'}):
                continue
            if archive_seen is not None:
                normalized=name.casefold()
                if normalized in archive_seen:continue  # Earlier loaded archive wins.
                archive_seen.add(normalized)
            target = destination.joinpath(*rel.parts)
            if target.exists():
                continue  # Loose files override archives in Ren'Py.
            payload = []
            for entry in entries:
                start, length = entry[0] ^ key, entry[1] ^ key
                prefix = entry[2] if len(entry) > 2 else b''
                if prefix is None:
                    prefix=b''
                if isinstance(prefix, str):
                    prefix = prefix.encode('latin-1')
                if len(prefix)>length:
                    raise ValueError(f'Invalid archive prefix length: {name}')
                f.seek(start)
                chunk = f.read(length-len(prefix))
                if len(chunk) != length-len(prefix):
                    raise ValueError(f'Truncated archive: {name}')
                payload.append(prefix + chunk)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b''.join(payload))
            extracted.append(target)
    return extracted

def find_launcher(stage):
    """Recognize Ren'Py bootstrap code without importing/executing a launcher.

    Older launchers need not define path_to_gamedir. Tokenization also supports
    Python 2 syntax and encoding declarations without using the host AST parser.
    """
    candidates = []
    unreadable = []
    paths = sorted(p for p in stage.glob('*.py') if p.is_file())
    for path in paths:
        try:
            with tokenize.open(path) as source:
                code = ''.join(token.string for token in tokenize.generate_tokens(source.readline)
                               if token.type not in (tokenize.COMMENT, tokenize.STRING,
                                                     tokenize.NL, tokenize.NEWLINE,
                                                     tokenize.INDENT, tokenize.DEDENT))
        except (OSError, UnicodeError, SyntaxError, tokenize.TokenError) as error:
            unreadable.append(f'{path.name}: {error}')
            continue
        if ('renpy.bootstrap.bootstrap(' in code or
                ('fromrenpyimportbootstrap' in code and 'bootstrap.bootstrap(' in code)):
            candidates.append(path)
    executable_names = {p.stem.casefold() for p in stage.glob('*.exe') if p.is_file()}
    paired = [p for p in candidates if p.stem.casefold() in executable_names]
    if len(paired) == 1:
        return paired[0]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise ValueError('Multiple RenPy launchers found in ' + str(stage) + ': ' +
                         ', '.join(p.name for p in candidates))
    detail = '; unreadable: ' + '; '.join(unreadable) if unreadable else ''
    raise ValueError('No RenPy bootstrap launcher found in ' + str(stage) +
                     '; Python files checked: ' + (', '.join(p.name for p in paths) or '(none)') + detail)


def interpreter_args(interpreter):
    """Use optimized bytecode when the bundled stdlib includes Python 2 site.pyo.

    Python 2's normal importer searches .pyc, while -O selects .pyo. Inspect only
    the runtime's own stdlib locations, never the host Python or game scripts.
    """
    runtime = interpreter.parent
    library = runtime.parent
    stdlib_dirs = (runtime / 'Lib', runtime / 'lib',
                   library / 'pythonlib2.7', library / 'python2.7')
    options = [str(interpreter), '-B']
    if any((directory / 'site.pyo').is_file() for directory in stdlib_dirs):
        options.append('-O')
    return options


def windows_architecture():
    """Detect the OS architecture, including a 32-bit process on 64-bit Windows."""
    aliases={'amd64':'x86_64','x86_64':'x86_64','x64':'x86_64',
             'x86':'x86','i386':'x86','i686':'x86','arm64':'arm64','aarch64':'arm64'}
    for value in (os.environ.get('PROCESSOR_ARCHITEW6432'),
                  os.environ.get('PROCESSOR_ARCHITECTURE'),platform.machine()):
        if value and value.lower() in aliases:return aliases[value.lower()]
    return 'x86_64' if sys.maxsize>2**32 else 'x86'


def select_windows_runtime(stage):
    """Choose a bundled interpreter by architecture, never by glob order."""
    candidates=sorted(p for p in (stage/'lib').glob('*windows*/python.exe') if p.is_file())
    if not candidates:raise ValueError('No bundled Windows runtime found in '+str(stage/'lib'))
    host=windows_architecture()
    preference={'x86_64':('x86_64','x86'),'x86':('x86',),'arm64':('arm64',)}[host]
    aliases={'i386':'x86','i686':'x86','x86':'x86','x86_64':'x86_64',
             'amd64':'x86_64','arm64':'arm64','aarch64':'arm64'}
    groups={};unknown=[]
    for candidate in candidates:
        match=re.search(r'(?:^|[-_])(x86_64|amd64|i686|i386|x86|arm64|aarch64)$',candidate.parent.name.lower())
        if match:groups.setdefault(aliases[match[1]],[]).append(candidate)
        else:unknown.append(candidate)
    for architecture in preference:
        compatible=groups.get(architecture,[])
        if len(compatible)==1:return compatible[0]
        if len(compatible)>1:
            raise ValueError('Multiple bundled Windows runtimes for '+architecture+': '+
                             ', '.join(str(p) for p in compatible))
    # Preserve legacy distributions with one nonstandard directory name.
    if len(candidates)==1 and unknown:return unknown[0]
    raise ValueError('No matching bundled Windows runtime for '+host+': '+', '.join(str(p) for p in candidates))


def engine_command(project, args, logname):
    stage = project / 'staging'
    launcher = find_launcher(stage)
    interpreter = select_windows_runtime(stage)
    print('Bundled Windows runtime: '+str(interpreter.relative_to(stage)),flush=True)
    env = dict(os.environ, RENPY_PATH_TO_SAVES=str(project / 'data' / 'saves'), PYTHONDONTWRITEBYTECODE='1')
    from workspace_files import command
    cmd = [*interpreter_args(interpreter), *command(project, launcher, args)]
    log = project / 'data' / logname
    with log.open('w', encoding='utf-8') as f:
        proc = owned_run(cmd, cwd=stage, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=180,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if proc.returncode:
        raise RuntimeError(f'Engine exit {proc.returncode}: {log}')
    return log

def prepare(project, cfg):
    source = Path(cfg['source']).resolve(strict=True)
    if source == project or source in project.parents or project in source.parents:
        raise ValueError('Project workspace must be outside the source game')
    if not (source / 'game').is_dir() or not (source / 'renpy').is_dir():
        raise ValueError('Expected a RenPy game distribution')
    version_file=source/'game/script_version.txt'
    if version_file.exists():
        version=ast.literal_eval(version_file.read_text(encoding='utf-8-sig'))
        if cfg.get('expected_engine') and list(version) != cfg['expected_engine']:
            raise ValueError(f'Engine mismatch: {version} != {cfg["expected_engine"]}')
    data = project / 'data'; data.mkdir(exist_ok=True)
    baseline = data / 'source-manifest.json'
    from workspace_files import plan,create,metadata,input_paths,input_ignore
    stage = project / 'staging'
    saved_workspace = metadata(project)
    selection = plan(source) if not stage.exists() or saved_workspace and saved_workspace['status'] == 'copying' else None
    if not baseline.exists():
        print('Recording translation input hashes', flush=True)
        if selection is not None:
            paths = input_paths(selection)
            save_json(baseline, manifest(source,ignore=input_ignore(paths)))
            save_json(data/'source-input-scope.json',{'version':1,'paths':sorted(paths)})
        else:
            save_json(baseline, manifest(source))
    else:
        verify_source(project,cfg)
    scripts = []
    if selection is not None:
        print('Copying scripts, settings, fonts and the matching Windows runtime', flush=True)
        scripts = create(project,source,selection)
    elif saved_workspace is None:
        # Existing full snapshots are retained; do not delete any user's files.
        archive_seen=set()
        for archive in sorted((stage / 'game').glob('*.rpa'),reverse=True):
            scripts += extract_scripts(archive, stage / 'game',archive_seen)
    extracted_index=data/'extracted-scripts.json'
    previous=json.loads(extracted_index.read_text(encoding='utf-8')) if extracted_index.exists() else []
    save_json(extracted_index,sorted(set(previous)|{p.relative_to(stage/'game').as_posix() for p in scripts
                 if p.suffix.lower() in ('.rpy','.rpyc','.rpym','.rpymc')}))
    print(f'Extracted {len(scripts)} script files', flush=True)
    compiled=[p for p in (stage/'game').rglob('*') if p.suffix in ('.rpyc','.rpymc')
              and 'tl' not in p.relative_to(stage/'game').parts and not p.with_suffix(p.suffix[:-1]).exists()]
    if compiled:
        recovered_names=[p.with_suffix(p.suffix[:-1]).relative_to(stage/'game').as_posix() for p in compiled]
        recovered_index=data/'recovered-source-files.json'
        previous=json.loads(recovered_index.read_text(encoding='utf-8')) if recovered_index.exists() else []
        save_json(recovered_index,sorted(set(previous)|set(recovered_names)))
        cmd = cli_command()+['--unrpyc','-p','1',*map(str,compiled)]
        with (data / 'decompile.log').open('w', encoding='utf-8') as log:
            owned_run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        missing = [str(p) for p in compiled if not p.with_suffix(p.suffix[:-1]).exists()]
        if missing:
            raise RuntimeError(f'Script recovery failed: {missing}')
    originals = [p for p in (stage / 'game').glob('*.rpy') if not p.name.startswith('zz_rpt_')]
    if cfg.get('expected_scripts') and len(originals) != cfg['expected_scripts']:
        raise ValueError(f'Expected {cfg["expected_scripts"]} scripts; got {len(originals)}')
    print('Generating translation templates with bundled matching engine', flush=True)
    engine_command(project, ['translate', cfg['language'], '--empty'], 'generate.log')
    print('Preparation complete', flush=True)

def seal_stage(project):
    """Verify the patch against archived original scripts, not recovered sources."""
    listing=project/'data/extracted-scripts.json'
    names=json.loads(listing.read_text(encoding='utf-8')) if listing.exists() else []
    stage=project/'staging/game'; recovered=project/'data/recovered-scripts'
    for name in names:
        relatives=[Path(name)]
        if Path(name).suffix in ('.rpyc','.rpymc'):relatives.append(Path(name).with_suffix(Path(name).suffix[:-1]))
        elif Path(name).suffix in ('.rpy','.rpym'):relatives.append(Path(str(name)+'c'))
        for rel in relatives:
            path=stage/rel
            if not path.resolve().is_relative_to(stage.resolve()):raise ValueError('Invalid archived script path')
            if path.exists():
                dest=recovered/rel;dest.parent.mkdir(parents=True,exist_ok=True)
                if dest.exists():
                    if dest.read_bytes()!=path.read_bytes():
                        raise ValueError(f'Recovered source already exists with different bytes: {rel}')
                    path.unlink()
                else:
                    shutil.move(str(path),str(dest))
    generated=project/'data/recovered-source-files.json'
    if generated.exists():
        for rel in json.loads(generated.read_text(encoding='utf-8')):
            path=(stage/rel).resolve()
            if not path.is_relative_to(stage.resolve()):raise ValueError('Invalid recovered source path')
            if path.exists():
                dest=recovered/rel;dest.parent.mkdir(parents=True,exist_ok=True)
                if dest.exists() and dest.read_bytes()!=path.read_bytes():raise ValueError('Recovery conflict')
                path.replace(dest)
    print('Staging now runs original archived scripts plus additive translation patch',flush=True)

def generated_source_path(name, language, baseline_paths):
    """Patch/runtime products are not immutable game inputs.

    Do not ignore arbitrary compiled scripts: compiled-only distributions use
    them as their original story/engine code. Only ignore derived bytecode when
    its source was recorded in the original baseline, which is still checked.
    """
    path = PurePosixPath(name.replace('\\', '/').casefold())
    parts = path.parts
    if path.as_posix() in ('log.txt', 'traceback.txt', 'errors.txt'):
        return True
    if '__pycache__' in parts:
        return True
    if parts[:2] in (('game', 'cache'), ('game', 'saves')):
        return True
    if parts[:2] == ('game', 'context') and path.suffix == '.txt':
        return True  # Optional translation guidance, not executable game input.
    if parts[:3] == ('game', 'tl', language.casefold()):
        return True
    if (len(parts) == 2 and parts[0] == 'game'
            and path.stem in ('zz_rpt_korean', 'zz_rpt_names', 'zz_rpt_layout', 'zz_rpt_reference', 'zz_rpt_hints', 'zz_rpt_language')
            and path.suffix in ('.rpy', '.rpyc')):
        return True
    return path.suffix in ('.rpyc', '.rpymc', '.pyc', '.pyo') and path.as_posix()[:-1] in baseline_paths


def verify_prepared_project(project):
    """Resume against the saved workspace, without touching today's source game."""
    required=('staging/game','data/templates','data/catalog.json')
    missing=[name for name in required if not (project/name).exists()]
    if missing:
        raise ValueError('Prepared project is incomplete: '+', '.join(missing)+
                         '. Existing translations were kept; restore the missing workspace files.')
    result={'checked':False,'unchanged':None,'files':0,'scope':'prepared project snapshot',
            'original_source_accessed':False,
            'reason':'Resuming the saved staging game and templates; current source changes are not imported.'}
    save_json(project/'data/input-verification.json',result)
    return result


def verify_source(project, cfg):
    # A resumed run renders/compiles the saved staging game, not cfg['source'].
    # Keep the explicit verify-source command and new preparation checks strict.
    if cfg.get('_prepared_snapshot'):
        return verify_prepared_project(project)
    old = json.loads((project / 'data/source-manifest.json').read_text(encoding='utf-8'))
    baseline_paths = {name.replace('\\', '/').casefold() for name in old}
    ignored = set()
    def ignore(name):
        if generated_source_path(name, cfg.get('language', 'korean'), baseline_paths):
            ignored.add(name)
            return True
        return False
    # Filter old baselines too; never reset them to today's game or erase caches.
    original = {name:value for name,value in old.items() if not ignore(name)}
    source = Path(cfg['source'])
    if not source.is_dir():raise FileNotFoundError('Original source folder not found: '+str(source))
    scope = project/'data/source-input-scope.json'
    if scope.exists():
        from workspace_files import input_ignore
        scoped_ignore=input_ignore(set(json.loads(scope.read_text(encoding='utf-8'))['paths']))
        current=manifest(source,ignore=lambda name: scoped_ignore(name) or ignore(name))
    else:
        current = manifest(source, ignore=ignore)
    changed = sorted(name for name in set(original) | set(current) if original.get(name) != current.get(name))
    result = {'unchanged': not changed, 'files': len(current), 'changed': changed,
              'scope': 'translation inputs, excluding uncopied media' if scope.exists() else 'original game inputs, excluding translation patch and runtime products',
              'ignored_paths': sorted(ignored)}
    save_json(project / 'data/source-verification.json', result)
    if changed:
        detail = repr(changed[:20]) + (' ...' if len(changed)>20 else '')
        raise RuntimeError('Original source changed: '+detail+'; see data/source-verification.json')
    return result

def play(project):
    stage=project/'staging'
    if list((stage/'game').glob('zz_rpt_qa*')):
        raise ValueError('QA hooks remain in staging; archive them before playing')
    launcher=find_launcher(stage)
    runtime=select_windows_runtime(stage)
    env=dict(os.environ,RENPY_PATH_TO_SAVES=str(project/'data/saves-play'),PYTHONDONTWRITEBYTECODE='1')
    with (project/'data/play.log').open('w',encoding='utf-8') as log:
        from workspace_files import command
        proc=subprocess.Popen([*interpreter_args(runtime),*command(project,launcher,['run'])],cwd=stage,env=env,
            stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    print(f'Staging game launched: PID {proc.pid}. Saves: {project / "data/saves-play"}')
