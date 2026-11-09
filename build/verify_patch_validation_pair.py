"""Paired validation/recovery proof with invented code, never a user game."""
import hashlib
import json
import shutil
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
sys.path.insert(0, str(ROOT/'build'))
from engine import save_json, manifest
from workspace_files import create, metadata, RECORD
from patch_validation import run
from verify_patch_validation import SOURCE


def main():
    root = ROOT/'build/patch-validation-paired'/time.strftime('%Y%m%d-%H%M%S')
    sdk = ROOT/'build/renpy-validation/renpy-8.0.3-sdk'
    summaries = []
    for kind in ('legacy_layout', 'original_missing_menu'):
        project = root/kind
        project.mkdir(parents=True)
        (project/'data').mkdir()
        create(project, sdk)
        original = project/'invented-original'
        (original/'game').mkdir(parents=True)
        source = SOURCE
        if kind == 'legacy_layout':
            source += '\nscreen credits():\n    text "Credits fixture."\n'
        (original/'game/script.rpy').write_text(source, encoding='utf-8')
        (project/'staging/game/script.rpy').write_text(source, encoding='utf-8')
        # Give the independent source fixture the exact engine/runtime too, so
        # source-authenticity is proven instead of trusting two prepared copies.
        for path in (project/'staging').rglob('*'):
            if path.is_file() and path.name != '_rpt_original_scripts.rpa':
                relative = path.relative_to(project/'staging')
                target = original/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        state = metadata(project)
        state.update(source_root=str(original), source_game=str(original/'game'), archives=[], loose_assets=[])
        save_json(project/'data'/RECORD, state)
        save_json(project/'data/source-manifest.json', manifest(original))
        cfg = dict(source=str(original), language='korean', model='not-used', textbox_scale=None)
        save_json(project/'project.json', cfg)
        save_json(project/'data/catalog.json', [dict(id='fixture', source='Fixture.', kind='string', file='fixture.rpy', line=3)])
        target = project/'staging/game/tl/korean/fixture.rpy'
        target.parent.mkdir(parents=True)
        target.write_text('translate korean strings:\n\n    old "Fixture."\n    new "Translated fixture."\n', encoding='utf-8')
        cache = project/'data/translations.jsonl'
        cache.write_text('preserved synthetic cache\n', encoding='utf-8')
        before = hashlib.sha256(cache.read_bytes()).hexdigest()
        if kind == 'legacy_layout':
            (project/'staging/game/zz_rpt_layout.rpy').write_text('# Generated textbox option; old fixture.\ninit 1050 python:\n    _rpt_layout_style = style.say_window.ysize\n', encoding='utf-8')
        started = time.monotonic()
        report = run(project, cfg, mode='compile' if kind=='legacy_layout' else 'render')
        unchanged = hashlib.sha256(cache.read_bytes()).hexdigest() == before
        original_unchanged = (original/'game/script.rpy').read_text(encoding='utf-8') == source
        good = (report['status']=='passed' and len(report['repairs'])==1
                if kind=='legacy_layout' else report['status']=='failed' and report['attribution']=='original' and not report['repairs'])
        summary = dict(kind=kind, status=report['status'], attribution=report['attribution'], repairs=report['repairs'],
                       cache_unchanged=unchanged, original_unchanged=original_unchanged,
                       report=report['report'], seconds=round(time.monotonic()-started,3), passed=good and unchanged and original_unchanged)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    save_json(root/'summary.json', summaries)
    return 0 if all(item['passed'] for item in summaries) else 1


if __name__ == '__main__':
    raise SystemExit(main())
