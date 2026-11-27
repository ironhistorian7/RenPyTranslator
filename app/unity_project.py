"""Conservative Unity ResourceManager TextAsset localization adapter.

Eligible TextAssets have a recognized locale path or a verified base text.
Translation and packaging use the project's copy of the serialized data.
"""
from collections import defaultdict
from contextlib import ExitStack, nullcontext
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from engine import save_json


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _text(raw):
    return raw.decode('utf-8-sig')


_CATALOG_VERSION = 2
_LOCALES = {'english':'en', 'japanese':'ja', 'en':'en', 'ja':'ja'}
_ENGLISH_WORDS = frozenset('a an and are can for have i in is it my not of on she that the this to was we with you your'.split())


def _english_display_text(text, spans):
    """Corroborate an unnamed base when explicit English resources also exist."""
    visible = ' '.join(text[span.start:span.end] for span in spans)
    letters = [ch for ch in visible if ch.isalpha()]
    latin = sum(ch.isascii() for ch in letters)
    cues = set(re.findall(r'[a-z]+', visible.lower())) & _ENGLISH_WORDS
    return latin >= 30 and latin >= 0.9 * len(letters) and len(cues) >= 3


def _catalog(source, locale, *, snapshot=None):
    from unity_assets import UnityAssetError, inspect_unity, capture_assets
    from unity_text_formats import extract
    inventory = inspect_unity(source, asset_root=snapshot) if snapshot else inspect_unity(source)
    groups = inventory['groups']
    explicit_english = any('en' in group['languages'] for group in groups)
    rows, assets = [], []
    exclusions = list(inventory.get('exclusions', []))
    descriptors = {}
    for group in groups:
        for candidates in group['languages'].values():
            for asset in candidates:
                descriptors[(asset['file'],asset['path_id'])] = asset
        asset = group.get('reference')
        if asset:
            descriptors[(asset['file'],asset['path_id'])] = asset
    payloads = dict(zip(descriptors, capture_assets(snapshot or source, list(descriptors.values()))))

    def payload(asset):
        return payloads[(asset['file'],asset['path_id'])]

    for group in groups:
        languages = group['languages']
        selected = [k for k in languages if k == locale or k.startswith(locale+'-')]
        base = group.get('reference')
        use_base = not selected and locale == 'en' and explicit_english and base is not None
        if use_base:
            asset = base
            companions = [items[0] for items in languages.values() if len(items) == 1]
        elif len(selected) == 1 and len(languages[selected[0]]) == 1:
            asset = languages[selected[0]][0]
            companions = ([base] if base else []) + [
                items[0] for lang,items in languages.items() if lang != selected[0] and len(items) == 1]
        else:
            exclusions.append({'group':group['group'],'reason':'source locale is missing or ambiguous'})
            continue
        try:
            raw = payload(asset)
            text = _text(raw)
        except UnicodeError:
            exclusions.append({'group':group['group'],'reason':'unsupported text encoding'})
            continue
        reference, kind = None, 'key_value'
        spans = [] if use_base else extract(text)
        if not spans:
            for candidate_kind in ('command','json','csv','key_value'):
                for companion in companions:
                    try:
                        candidate = _text(payload(companion))
                    except UnicodeError:
                        continue
                    trial = extract(text, reference=candidate, kind=candidate_kind)
                    if trial:
                        reference, spans, kind = candidate, trial, candidate_kind
                        break
                if spans:
                    break
        if not spans or use_base and not _english_display_text(text, spans):
            reason = 'base English text or matching format not confirmed' if use_base else 'unsupported text syntax'
            exclusions.append({'group':group['group'],'reason':reason})
            continue
        key = asset['file']+'\0'+str(asset['path_id'])
        if any(a['key'] == key for a in assets):
            raise UnityAssetError('중복 Unity TextAsset: '+key)
        for index,span in enumerate(spans):
            value = text[span.start:span.end]
            uid = hashlib.sha256((key+'\0'+str(index)+'\0'+value).encode('utf-8')).hexdigest()[:20]
            usage = {'label':'ui_label','name':'name_label'}.get(span.kind,span.kind)
            rows.append({'id':uid,'source':value,'kind':'dialogue' if span.kind=='dialogue' else 'string',
                         'usage':usage,'file':asset['file'],'unity_format':kind,
                         'block':group['group'],'speaker':'unknown','source_file':asset['resource_path'],
                         'source_line':text[:span.start].count('\n')+1,'location':{'asset':key,'index':index}})
        assets.append({'key':key,'asset':asset,'kind':kind,'reference':reference,'count':len(spans),
                       'sha256':hashlib.sha256(raw).hexdigest(), 'base_locale':use_base})
    if not rows:
        note = '선택한 언어에서 지원되는 Unity 현지화 텍스트를 찾지 못했습니다.'
        if inventory.get('indicators'):
            note += ' String Table·Addressables 형식은 현재 지원 범위 밖입니다.'
        raise UnityAssetError(note)
    if len({r['id'] for r in rows}) != len(rows):
        raise UnityAssetError('Unity 항목 ID가 중복되었습니다.')
    return dict(data_dir=inventory['data_dir'],source_language=locale,assets=assets,exclusions=exclusions),rows


def _write_catalog(data, manifest, rows):
    save_json(data/'unity-manifest.json',manifest)
    save_json(data/'inspection.json',{'engine':'unity','entries':len(rows),'assets':len(manifest['assets']),
              'files':len(manifest['files']),'exclusions':manifest['exclusions'],'original_modified':False})
    save_json(data/'catalog.json',rows)
    save_json(data/'catalog-format.json',{'engine':'unity','version':_CATALOG_VERSION})
    print(f"Unity localization: {len(rows)} entries in {len(manifest['assets'])} TextAssets; excluded: {sum(isinstance(item,dict) for item in manifest['exclusions'])}",flush=True)


def prepare(project, cfg):
    from unity_assets import UnityAssetError
    project = Path(project)
    data = project/'data'
    data.mkdir(parents=True, exist_ok=True)
    snapshot = data/'unity-source'
    if (data/'catalog.json').exists():
        raise ValueError('기존 Unity 작업 자료를 덮어쓰지 않습니다.')
    source = Path(cfg['source'])
    manifest, rows = _catalog(source, _LOCALES[cfg.get('source_language','english')])
    files = {item['asset']['file'] for item in manifest['assets']}
    manifest_files = {}
    with tempfile.TemporaryDirectory(prefix='unity-source-',dir=data) as temp:
        staging = Path(temp)/'snapshot'
        for rel in sorted(files):
            src = (source/rel).resolve()
            if not src.is_relative_to(source.resolve()) or not src.is_file():
                raise UnityAssetError('잘못된 Unity 원본 경로: '+rel)
            dst = staging/rel
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(src,dst)
            manifest_files[rel] = _hash(dst)
        if not snapshot.exists():
            staging.rename(snapshot)
        elif any(not (snapshot/rel).is_file() or _hash(snapshot/rel)!=digest
                 for rel,digest in manifest_files.items()):
            raise UnityAssetError('이전에 준비한 Unity 원문과 선택한 게임이 다릅니다.')
    manifest.update(version=1,files=manifest_files)
    cfg.update(engine='unity',engine_label='Unity',game_name=source.name)
    save_json(project/'project.json',cfg)
    _write_catalog(data,manifest,rows)


def upgrade_english_catalog(project, cfg):
    """Append discovered base text using the existing immutable resource snapshot."""
    from unity_assets import UnityAssetError
    data = project/'data'
    marker = data/'catalog-format.json'
    if (_LOCALES.get(cfg.get('source_language','english')) != 'en'
            or not (data/'catalog.json').is_file()
            or marker.is_file() and _read(marker).get('version',0) >= _CATALOG_VERSION):
        return
    current = _verified(project)
    if current['source_language'] != 'en':
        raise UnityAssetError('기존 Unity 프로젝트의 원문 언어를 확인하세요.')
    prepared, discovered = _catalog(Path(cfg['source']), 'en', snapshot=data/'unity-source')
    existing = _read(data/'catalog.json')
    fresh = {row['id']:row for row in discovered}
    for row in existing:
        match = fresh.get(row['id'])
        if not match or any(row[k] != match[k] for k in ('source','source_file','location')):
            raise UnityAssetError('기존 Unity 항목과 원본 리소스 위치가 다릅니다.')
    if any(item['asset']['file'] not in current['files'] for item in prepared['assets']):
        raise UnityAssetError('작업 사본 밖의 Unity 에셋은 기존 프로젝트에 추가할 수 없습니다.')
    known_ids = {row['id'] for row in existing}
    added = [row for row in discovered if row['id'] not in known_ids]
    prepared.update(version=current['version'],files=current['files'])
    _write_catalog(data,prepared,existing+added)
    if added:
        print(f'Unity English source: {len(added)} base text entries added; saved translations retained',flush=True)


def _verified(project):
    manifest = _read(project/'data/unity-manifest.json'); root = project/'data/unity-source'
    for rel, digest in manifest['files'].items():
        p = (root/rel).resolve()
        if not p.is_relative_to(root.resolve()) or not p.is_file() or _hash(p) != digest: raise ValueError('Unity 작업 원문이 없거나 변경되었습니다: '+rel)
    return manifest


def _replacements(project, cfg, manifest, rows, known, *, rejected=None, rejected_manual=None):
    from unity_assets import capture_assets
    from unity_text_formats import extract, replace
    from replacements import load_rules, replace_visible, rendered_symbols
    locale = {'english':'en','japanese':'ja','en':'en','ja':'ja'}.get(cfg.get('source_language','english'))
    if locale != manifest['source_language']: raise ValueError('기존 Unity 프로젝트의 원문 언어가 선택한 언어와 다릅니다. 기존 작업을 유지합니다.')
    missing = [r['id'] for r in rows if r['id'] not in known]
    if missing: raise ValueError(f'아직 번역하지 않은 항목이 {len(missing)}개 있습니다. 기존 번역을 유지하고 이어서 번역하세요.')
    by_asset = defaultdict(list)
    for row in rows: by_asset[row['location']['asset']].append(row)
    result = defaultdict(dict); rules = load_rules(project/'replacements.json')
    payloads = capture_assets(project/'data/unity-source',[item['asset'] for item in manifest['assets']])
    for item, raw in zip(manifest['assets'], payloads):
        if hashlib.sha256(raw).hexdigest() != item['sha256']: raise ValueError('Unity TextAsset 원문이 변경되었습니다: '+item['key'])
        text = _text(raw); spans = extract(text,reference=item['reference'],kind=item['kind'])
        if len(spans) != item['count']: raise ValueError('Unity 텍스트 구조가 변경되었습니다: '+item['key'])
        changes = {}
        manual_indices = set()
        item_ids = {}
        for row in by_asset[item['key']]:
            idx = row['location']['index']
            if idx >= len(spans) or text[spans[idx].start:spans[idx].end] != row['source']:
                raise ValueError('Unity 카탈로그와 원문이 일치하지 않습니다: '+row['id'])
            entry = known[row['id']]; value = entry['text']
            manual = entry.get('status') == 'manual_translation'
            if not manual: value = replace_visible(value,rules)[0]
            value = rendered_symbols(row,entry,cfg,text=value)
            if value == row['source']:
                continue
            changes[idx] = value
            item_ids[idx] = row['id']
            if manual: manual_indices.add(idx)
        if changes:
            invalid = []
            output = replace(text,spans,changes,reference=item['reference'],kind=item['kind'],
                             manual_indices=manual_indices,rejected=invalid)
            for idx in invalid:
                skipped = rejected_manual if idx in manual_indices else rejected
                if skipped is not None: skipped.append(item_ids[idx])
            if output == text:
                continue
            encoded = (b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'') + output.encode('utf-8')
            result[item['asset']['file']][item['asset']['path_id']] = encoded
    return result


def _validate(project, output, manifest, replacements):
    from unity_assets import verify_asset
    if not replacements: raise ValueError('변경된 Unity 텍스트가 없어 패치를 만들 수 없습니다.')
    for rel, values in replacements.items():
        target = output/rel
        if not target.is_file(): raise ValueError('Unity 패치 파일이 없습니다: '+rel)
        verify_asset(project/'data/unity-source'/rel,target,values)
    return {'status':'passed','files':sorted(replacements),'assets':sum(map(len,replacements.values()))}


def render(project, cfg, *, rows=None, known=None):
    from translation import read_catalog,cache
    from unity_assets import repack_asset
    from output_paths import destination
    project = Path(project); manifest = _verified(project)
    rows = read_catalog(project) if rows is None else rows; known = cache(project,cfg) if known is None else known
    from job_events import warning
    rejected, manual = [], []
    replacements = _replacements(project,cfg,manifest,rows,known,rejected=rejected,rejected_manual=manual)
    rejected_path = project/'data/unity-rejected.json'
    save_json(rejected_path,{'ids':rejected,'manual_ids':manual,'reason':'protected Unity text structure','original_kept':True})
    if rejected:
        warning('unity_structure_rejected','Unity 형식이 맞지 않아 원문을 유지했습니다.',item_ids=rejected,
                log_path=rejected_path,detail='미번역 복구로 다시 처리할 수 있습니다.')
    if manual:
        warning('unity_manual_structure','변수 또는 구분자가 맞지 않는 수동 입력의 원문을 유지했습니다.',
                item_ids=manual,log_path=rejected_path,detail='입력 내용은 저장되어 있습니다. 미번역 복구에서 다시 확인할 수 있습니다.')
    if not replacements: raise ValueError('변경된 Unity 텍스트가 없어 패치를 만들 수 없습니다.')
    output,label = destination(project,cfg)
    with tempfile.TemporaryDirectory(prefix='unity-export-',dir=project/'data') as temp:
        stage = Path(temp)
        for rel,values in replacements.items():
            target=stage/rel; target.parent.mkdir(parents=True,exist_ok=True)
            repack_asset(project/'data/unity-source'/rel,target,values)
        # repack_asset has already validated every serialized object and reference.
        report = {'status':'passed','files':sorted(replacements),'assets':sum(map(len,replacements.values()))}
        slot = {'en':'영어', 'ja':'일본어'}[manifest['source_language']]
        (stage/'APPLY.txt').write_text(
            '이 폴더의 *_Data 파일을 동일한 버전의 게임 폴더에 복사하세요. 원본 파일을 보관하세요.\n'
            f'게임의 언어 메뉴에서 {slot}를 선택하면 적용된 한국어 번역이 표시됩니다.\n'
            '게임 실행 및 한국어 글꼴 표시는 별도 확인이 필요합니다.\n',encoding='utf-8')
        try:
            project_link = os.path.relpath(project, output)
        except ValueError:
            project_link = str(project.resolve())
        save_json(stage/'project-link.json',{'project':project_link,'engine':'unity'})
        archive=stage/(label+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as z:
            for rel in sorted(replacements): z.write(stage/rel,rel)
            z.write(stage/'APPLY.txt','APPLY.txt')
        output.mkdir(parents=True,exist_ok=True)
        for rel in list(sorted(replacements))+['APPLY.txt','project-link.json',archive.name]:
            dest=output/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.unity-',suffix='.tmp',dir=dest.parent)
            os.close(fd)
            staged = Path(name)
            try:
                shutil.copy2(stage/rel,staged)
                staged.replace(dest)
            finally:
                staged.unlink(missing_ok=True)
    report.update(output=str(output),archive=str(output/archive.name),original_kept=len(rejected)+len(manual),
                  rejected_ids=rejected,manual_rejected_ids=manual)
    save_json(project/'data/unity-output.json',report)
    save_json(project/'data/patch-validation.json',report)
    print('Output folder: '+str(output),flush=True)
    return report


def static_check(project,cfg):
    from translation import read_catalog,cache
    project=Path(project); manifest=_verified(project)
    record=_read(project/'data/unity-output.json'); output=Path(record['output'])
    replacements=_replacements(project,cfg,manifest,read_catalog(project),cache(project,cfg))
    report=_validate(project,output,manifest,replacements); report.update(output=str(output))
    save_json(project/'data/patch-validation.json',report)
    return report


def main(args,parser,info):
    from automatic import resolve_project,settings
    from task_plan import plan
    from output_paths import apply_options
    from translate_game import project_lock
    from translation import translate,cache,read_catalog
    from model_store import require_job_model
    from diagnostics import stage
    from retranslation import comparison, complete
    jobs=plan(args.tasks) if args.command=='tasks' else [args.command]
    if args.command=='repair':
        if args.fix != 'failed': raise ValueError('Unity는 실패 항목 재시도만 지원합니다.')
        jobs=['failed']
    unsupported=set(jobs)-{'run','retranslate','failed','check','prepare','status','catalog','verify-source','translate','sample'}
    if unsupported: raise ValueError('Unity에서 지원하지 않는 작업: '+', '.join(sorted(unsupported)))
    project,base=resolve_project(args.source,args.project,args.model,None,engine='unity')
    if args.source_language is not None:
        from source_language import normalize
        chosen=normalize(args.source_language)
        manifest_path=project/'data/unity-manifest.json'
        locale={'english':'en','japanese':'ja'}[chosen]
        if manifest_path.is_file() and _read(manifest_path)['source_language'] != locale:
            raise ValueError('기존 Unity 프로젝트의 원문 언어가 선택한 언어와 다릅니다. 기존 작업을 유지합니다.')
        base['source_language']=chosen
    if getattr(args,'runtime_id',None) is not None: base['runtime_id']=args.runtime_id
    base.update(engine='unity',engine_label='Unity')
    need_model=bool(set(jobs)&{'run','retranslate','translate','sample','failed'})
    if need_model:
        base['model']=require_job_model(jobs,args.model or base.get('model'),runtime_id=base.get('runtime_id'))
        from model_runtime import model_session
        session=model_session()
    else: session=nullcontext()
    with session, ExitStack() as locks:
        locks.enter_context(project_lock(project))
        if set(jobs)&{'run','retranslate','translate','sample','prepare'} and (project/'data/catalog.json').is_file():
            stage('Unity prepare',project,base)
            upgrade_english_catalog(project,base)
        if 'retranslate' in jobs:
            project = comparison(project,base)
            locks.enter_context(project_lock(project))
            base = _read(project/'project.json')
            jobs = ['run' if j == 'retranslate' else j for j in jobs]
        else:
            save_json(project/'project.json',base)
        cfg=apply_options(project,settings(project,base),args.output,args.suffix,args.textbox_scale,False)
        if jobs==['check']:
            stage('Unity patch check',project,cfg)
            static_check(project,cfg);print('Unity patch check passed',flush=True);return
        if not (project/'data/catalog.json').is_file():
            if not set(jobs)&{'run','prepare'}: raise ValueError('먼저 Unity 번역을 진행하세요.')
            stage('Unity prepare',project,cfg)
            prepare(project,cfg)
        manifest = _verified(project)
        locale = {'english':'en','japanese':'ja','en':'en','ja':'ja'}.get(cfg.get('source_language','english'))
        if locale != manifest['source_language']: raise ValueError('기존 Unity 프로젝트의 원문 언어가 선택한 언어와 다릅니다. 기존 작업을 유지합니다.')
        if jobs==['prepare']: return
        if jobs==['catalog']: print(project/'data/catalog.json');return
        if jobs==['verify-source']: print('Unity source snapshot verified');return
        if jobs==['status']:
            rows=read_catalog(project);known=cache(project,cfg)
            print(json.dumps({'engine':'unity','total':len(rows),'cached':sum(r['id'] in known for r in rows)}));return
        if need_model:
            from gpu_policy import configure
            cfg=configure(cfg)
        if set(jobs)&{'run','translate','sample'}:
            stage('Unity translation',project,cfg)
            translate(project,cfg,'sample' in jobs)
        if 'failed' in jobs:
            if not (project/'data/translations.jsonl').is_file(): raise ValueError('기존 번역 기록이 없습니다.')
            from failed_repair import run
            run(project,cfg)
            rejected_path=project/'data/unity-rejected.json'
            rejected=_read(rejected_path).get('ids',[]) if rejected_path.is_file() else []
            if rejected:
                from translation import translate_subset
                current=cache(project,cfg)
                retry=[uid for uid in rejected if current.get(uid,{}).get('status') != 'manual_translation']
                translate_subset(project,cfg,retry,force=True)
        if jobs==['sample']: return
        rows=read_catalog(project);known=cache(project,cfg)
        if set(jobs)&{'run','translate','failed'}:
            from translation_review import run as review
            review(project,cfg,rows=rows,known=known,review_sentences=True);known=cache(project,cfg)
        stage('Unity patch output',project,cfg)
        render(project,cfg,rows=rows,known=known)
        complete(project)
