"""Independent comparison runs using the already prepared game snapshot."""
from datetime import datetime
import json
import os
import shutil

from engine import save_json
from workspace_files import clone

# Extraction inputs only: no translations, corrections, terminology memory,
# failure recovery or ownership of the previous output directory.
PREPARED = (
    'templates', 'recovered-scripts', 'catalog.json', 'catalog-format.json',
    'static-screen-literals.json', 'source-outline.json', 'source-manifest.json',
    'stage-manifest.json', 'inspection.json', 'extracted-scripts.json',
    'recovered-source-files.json', 'source-input-scope.json', 'tool-options.json',
)


def read(path, default):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def comparison(project, cfg):
    """Caller holds the original project's lock. Resume unfinished same-model runs."""
    project = project.resolve()
    if not all((project / name).exists() for name in ('data/catalog.json', 'data/templates', 'staging/game')):
        raise ValueError('재번역할 준비 자료가 없습니다. 먼저 번역을 실행하거나 기존 프로젝트/결과 폴더를 선택하세요.')
    model = cfg['model']
    indexfile = project / 'data/retranslation-runs.json'
    index = read(indexfile, {})
    previous = index.get(model)
    target = None
    if previous:
        candidate = (project / previous).resolve()
        if candidate.parent != project.parent or candidate == project:
            raise ValueError('Invalid comparison project path')
        state = read(candidate / 'data/retranslation.json', {})
        if state.get('model') == model and state.get('status') in ('copying', 'pending'):
            target = candidate
    if target is None:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        target = project.with_name(project.name + '-retranslate-' + stamp)
        target.mkdir()
        state = {'model': model, 'status': 'copying', 'created': stamp,
                 'original_project': os.path.relpath(project, target)}
        save_json(target / 'data/retranslation.json', state)
        index[model] = os.path.relpath(target, project)
        save_json(indexfile, index)
    statefile = target / 'data/retranslation.json'
    state = read(statefile, {})
    if state['status'] == 'copying':
        print('재번역 준비: 기존 작업 사본을 복사합니다. 번역 캐시는 가져오지 않습니다.', flush=True)
        # Real copies, never hardlinks: compilation must not alter the original.
        # Interrupted copies can repeat; translation begins only after completion.
        clone(project, target)
        for name in PREPARED:
            src = project / 'data' / name
            dst = target / 'data' / name
            if src.is_dir(): shutil.copytree(src, dst, dirs_exist_ok=True)
            elif src.is_file(): shutil.copy2(src, dst)
        for name in ('replacements.json', 'names.json'):
            if (project / name).exists(): shutil.copy2(project / name, target / name)
        newcfg = {key: value for key, value in cfg.items() if not key.startswith('_')}
        newcfg.pop('imported_cache_fingerprint', None)
        newcfg.pop('output_root', None)
        newcfg['comparison_label'] = '재번역-' + state['created']
        save_json(target / 'project.json', newcfg)
        state['status'] = 'pending'
        save_json(statefile, state)
    print('Comparison project: ' + str(target), flush=True)
    print('기존 번역과 결과를 보존합니다. 완료한 재번역 항목은 이어서 사용합니다.', flush=True)
    return target


def complete(project):
    path = project / 'data/retranslation.json'
    state = read(path, {})
    if state:
        state['status'] = 'complete'
        save_json(path, state)
