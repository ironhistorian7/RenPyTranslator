"""Invented text/temp projects only; preparation/model/processes are mocked."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import project_update as update
from translation import cache, fingerprint
from automatic import settings


CFG = {'language': 'korean', 'source_language': 'english', 'model': 'synthetic',
       'num_ctx': 16384, 'automatic_version': 1}


def row(uid, text, *, file='script.rpy', block='start_12345678', speaker='speaker', usage='dialogue', kind='dialogue'):
    return {'id': uid, 'source': text, 'kind': kind, 'usage': usage, 'file': file,
            'block': block, 'speaker': speaker}


def translated(rows, values=None, **extra):
    values = values or {}
    return {r['id']: dict(id=r['id'], source=r['source'], text=values.get(r['id'], '한국어 번역'),
                          model='synthetic', fingerprint=fingerprint(CFG), **extra) for r in rows}


class MatcherTests(unittest.TestCase):
    def match(self, old, new, known=None, **cfg):
        return update.reuse_translations(old, new, translated(old) if known is None else known, dict(CFG, **cfg))

    def test_insertions_changed_ids_and_deleted_lines(self):
        old = [row('a', 'First line.'), row('b', 'Second line.'), row('c', 'Removed line.')]
        new = [row('a2', 'First line.'), row('new', 'Inserted line.'), row('b2', 'Second line.')]
        items, report = self.match(old, new)
        self.assertEqual([r['id'] for r in items], ['a2', 'b2'])
        self.assertEqual(report['reused'], 2)
        self.assertEqual(report['new_or_changed'], 1)
        self.assertEqual(report['deleted_source_occurrences'], 1)
        self.assertNotIn('c', [r['id'] for r in items])

    def test_speaker_usage_source_and_functional_tokens_are_exact(self):
        old = [row('a', 'Hello [person].')]
        for new in (row('a', 'Hello [other].'), row('a', 'Hello [person].', speaker='other'),
                    row('a', 'Hello [person].', usage='choice'), row('a', 'Hello there.')):
            items, report = self.match(old, [new], translated(old, {'a': '안녕 [person].'}))
            self.assertEqual(items, [])
            self.assertEqual(report['remaining'], 1)

    def test_file_rename_named_scope_and_wholly_relocated_ui(self):
        old = [row('a', 'First line.'), row('b', 'Second line.')]
        new = [row('a2', 'First line.', file='renamed.rpy', block='start_87654321'),
               row('b2', 'Second line.', file='renamed.rpy', block='start_87654321'),
               row('c', 'Added line.', file='renamed.rpy', block='start_87654321')]
        items, _ = self.match(old, new)
        self.assertEqual(len(items), 2)
        old_ui = [row('menu', 'Open', kind='string', usage='ui_label', block='strings')]
        new_ui = [dict(old_ui[0], id='menu2', file='newscreen.rpy')]
        self.assertEqual(len(self.match(old_ui, new_ui)[0]), 1)

    def test_repeated_different_values_use_unchanged_neighbor_anchors(self):
        old = [row('a', 'Opening.'), row('x', 'Come here.'), row('b', 'Middle.'),
               row('y', 'Come here.'), row('c', 'Closing.')]
        new = [row('a2', 'Opening.'), row('insert', 'New line.'), row('x2', 'Come here.'),
               row('b2', 'Middle.'), row('y2', 'Come here.'), row('c2', 'Closing.')]
        values = {'x': '이리 오세요.', 'y': '이리 와.'}
        items, report = self.match(old, new, translated(old, values))
        by_id = {r['id']: r['text'] for r in items}
        self.assertEqual(by_id['x2'], '이리 오세요.')
        self.assertEqual(by_id['y2'], '이리 와.')
        self.assertEqual(report['ambiguous'], 0)

    def test_recycled_pointer_does_not_choose_ambiguous_repetition(self):
        old = [row('0', 'Come here.'), row('1', 'Come here.')]
        new = [row('0', 'Come here.'), row('1', 'Come here.'), row('2', 'Come here.')]
        items, report = self.match(old, new, translated(old, {'0': '이리 와.', '1': '이리 오세요.'}))
        # At most edge-neighbor-proven entries may transfer; a recycled ID alone
        # must not assign the old value to the inserted middle occurrence.
        self.assertNotIn('1', [r['id'] for r in items])
        self.assertGreaterEqual(report['ambiguous'], 1)

    def test_identical_repeated_values_can_be_reused(self):
        old = [row('x', 'Come here.'), row('y', 'Come here.')]
        new = [row('n'+str(i), 'Come here.') for i in range(50)]
        items, report = self.match(old, new)
        self.assertEqual(len(items), 50)
        self.assertEqual(report['ambiguous'], 0)

    def test_different_named_scope_does_not_reuse_a_single_matching_line(self):
        old = [row('a', 'Come here.'), row('b', 'Old context.')]
        new = [row('c', 'Come here.', block='other_12345678'), row('d', 'Different context.', block='other_12345678')]
        self.assertEqual(self.match(old, new)[0], [])
        self.assertEqual(self.match([old[0]], [dict(old[0], id='other', block='other_12345678')])[0], [])

    def test_rpg_relocated_event_requires_two_distinct_exact_anchors(self):
        old = [row('a', 'Come here.', file='data/Map001.json', block='events[1].pages[0].list:0:0')]
        new = [dict(old[0], id='a2', file='data/Map002.json', block='events[2].pages[0].list:0:0')]
        self.assertEqual(self.match(old, new, engine='rpgmaker')[0], [])
        old.append(row('b', 'Follow me.', file='data/Map001.json', block='events[1].pages[0].list:0:0'))
        new.append(dict(old[1], id='b2', file='data/Map002.json', block='events[2].pages[0].list:0:0'))
        self.assertEqual(len(self.match(old, new, engine='rpgmaker')[0]), 2)

    def test_failed_invalid_and_original_story_values_stay_pending(self):
        old = [row('a', 'Hello [person].'), row('b', 'Old sentence.'), row('c', 'Original sentence.')]
        known = translated(old, {'a': '안녕.', 'c': 'Original sentence.'})
        known['b']['status'] = 'source_fallback'; known['b']['text'] = known['b']['source']
        items, report = self.match(old, old, known)
        self.assertEqual(items, [])
        self.assertEqual(report['unavailable_previous'], 3)

    def test_rpg_offsets_boundary_numbers_and_www_rename(self):
        old = [row('a', 'First line.', file='data/Map001.json', block='events[1].pages[0].list:2:0'),
               row('b', 'Second line.', file='data/Map001.json', block='events[1].pages[0].list:2:0')]
        new = [dict(old[0], id='a2', block='events[1].pages[0].list:7:0'),
               row('new', 'Added line.', file='data/Map001.json', block='events[1].pages[0].list:7:0'),
               dict(old[1], id='b2', block='events[1].pages[0].list:7:0')]
        self.assertEqual(len(self.match(old, new, engine='rpgmaker')[0]), 2)
        relocated = [dict(r, id=r['id']+'new', file='www/'+r['file']) for r in old]
        self.assertEqual(len(self.match(old, relocated, engine='rpgmaker')[0]), 2)


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.old = self.root/'data/projects/Old'
        (self.old/'data/templates').mkdir(parents=True); (self.old/'staging/game').mkdir(parents=True)
        self.source = self.root/'NewVersion'; self.source.mkdir()
        self.cfg = dict(CFG, source=str(self.root/'OldVersion'))
        save_json(self.old/'project.json', self.cfg)
        self.rows = [row('a', 'Hello [person].'), row('b', 'Second sentence.')]
        save_json(self.old/'data/catalog.json', self.rows)
        self.save_cache(self.old, translated(self.rows, {'a': '안녕 [person].', 'b': '두 번째 문장.'}))
        save_json(self.old/'data/recovered-translations.json', {'a': {'id': 'a', 'source': self.rows[0]['source'], 'before': '안녕 [person].', 'text': '반가워 [person].'}})
        save_json(self.old/'data/reviewed-translations.json', {'Second sentence.': '검수한 문장.'})
        save_json(self.old/'names.json', {'overrides': {'Invented': '가상'}})
        save_json(self.old/'replacements.json', {'replacements': {'old': 'new'}})
        save_json(self.old/'data/tool-options.json', {'show_reference': False})
        save_json(self.old/'data/hints.json', {'old_location': True})
        self.new_rows = [dict(self.rows[0], id='a2'), row('c', 'Added sentence.'), dict(self.rows[1], id='b2')]

    def save_cache(self, project, items):
        (project/'data/translations.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in items.values()), encoding='utf-8')

    def snapshots(self):
        return {p.relative_to(self.old).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.old.rglob('*') if p.is_file()}

    def prepare(self, project, cfg, engine):
        (project/'data/templates').mkdir(parents=True, exist_ok=True)
        (project/'staging/game').mkdir(parents=True, exist_ok=True)
        save_json(project/'data/catalog.json', self.new_rows)

    @contextlib.contextmanager
    def mocked(self, **overrides):
        with patch('project_update.ROOT', self.root), \
             patch('game_engine.detect', return_value={'engine': 'renpy', 'supported': True}), \
             patch('project_update._source_signature', return_value='signature'), \
             patch('project_update._prepare_revision', side_effect=overrides.get('prepare', self.prepare)) as prepared, \
             contextlib.redirect_stdout(io.StringIO()):
            yield prepared

    def test_effective_cache_recovery_reviewed_preserved_and_resume_no_overwrite(self):
        before = self.snapshots()
        with self.mocked() as prepared:
            target, cfg, report = update.update_project(self.old, self.cfg, self.source)
            known = cache(target, settings(target, cfg))
            self.assertEqual(known['a2']['text'], '반가워 [person].')
            self.assertEqual(known['b2']['text'], '검수한 문장.')
            self.assertEqual(report['remaining'], 1)
            self.assertFalse((target/'data/hints.json').exists())
            self.assertEqual(update.read(target/'names.json', {}), update.read(self.old/'names.json', {}))
            self.assertEqual(update.read(target/'data/tool-options.json', {}), {'show_reference': False})
            with (target/'data/translations.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(id='c', source='Added sentence.', text='추가한 문장.', model='synthetic', fingerprint=fingerprint(settings(target, cfg))))+'\n')
            again, _, _ = update.update_project(self.old, self.cfg, self.source)
            self.assertEqual(again, target)
            self.assertEqual(prepared.call_count, 1)
            self.assertEqual(cache(target, settings(target, cfg))['c']['text'], '추가한 문장.')
            update.mark_complete(target)
            self.assertEqual(update.read(target/'data/update.json', {})['status'], 'complete')
            self.assertEqual(update.update_project(self.old, self.cfg, self.source)[0], target)
        self.assertEqual(self.snapshots(), before)

    def test_prepare_cancellation_resumes_same_new_revision(self):
        before = self.snapshots()
        with self.mocked(prepare=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt): update.update_project(self.old, self.cfg, self.source)
        with self.mocked() as prepared:
            target, _, report = update.update_project(self.old, self.cfg, self.source)
            self.assertEqual(prepared.call_count, 1)
            self.assertEqual(report['reused'], 2)
            self.assertEqual(update.read(target/'data/update.json', {})['status'], 'pending')
        self.assertEqual(self.snapshots(), before)

    def test_new_source_revision_and_same_path_registry_keep_old_snapshot(self):
        from automatic import source_key
        key = source_key(self.source.resolve())
        save_json(self.root/'data/projects/index.json', {key: self.old.name})
        before = self.snapshots()
        with self.mocked():
            target, _, _ = update.update_project(self.old, self.cfg, self.source)
            self.assertEqual(update.read(self.root/'data/projects/index.json', {})[key], target.name)
            prior = update.read(target/'data/update.json', {})['previous_registry_project']
            self.assertIn(self.old.name, prior.values())
            with patch('project_update._source_signature', return_value='changed-signature'):
                other, _, _ = update.update_project(self.old, self.cfg, self.source)
            self.assertNotEqual(other, target)
        self.assertEqual(self.snapshots(), before)

    def test_changed_language_does_not_reuse_old_translation(self):
        with self.mocked():
            _, _, report = update.update_project(self.old, dict(self.cfg, source_language='japanese'), self.source)
        self.assertEqual(report['reused'], 0)

    def test_wrong_engine_or_missing_baseline_stops_before_preparation(self):
        with self.mocked() as prepared, patch('game_engine.detect', return_value={'engine': 'rpgmaker', 'supported': True}):
            with self.assertRaisesRegex(ValueError, '같은 지원 엔진'): update.update_project(self.old, self.cfg, self.source)
            prepared.assert_not_called()
        (self.old/'data/catalog.json').unlink()
        with self.mocked() as prepared:
            with self.assertRaisesRegex(ValueError, '기존 번역 작업 자료'): update.update_project(self.old, self.cfg, self.source)
            prepared.assert_not_called()


class RPGRevisionTests(unittest.TestCase):
    def test_real_text_only_mv_mz_preparation_and_pointer_remap(self):
        from test_rpgmaker import fixture
        import rpgmaker_project
        from translation import read_catalog, protect
        for variant in ('MV', 'MZ'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); old_source = root/'OldGame'; new_source = root/'NewGame'
                fixture(old_source, variant); fixture(new_source, variant)
                old = root/'data/projects/Old'; (old/'data').mkdir(parents=True)
                cfg = dict(CFG, engine='rpgmaker', source=str(old_source))
                save_json(old/'project.json', cfg)
                with contextlib.redirect_stdout(io.StringIO()), \
                     patch('translation.request', side_effect=AssertionError('No network/model requests')), \
                     patch('hy_backend.runtime', side_effect=AssertionError('No model runtime')), \
                     patch('engine.owned_run', side_effect=AssertionError('No game/process execution')):
                    rpgmaker_project.prepare(old, cfg)
                    old_rows = read_catalog(old)
                    records = []
                    for source in old_rows:
                        _, tokens = protect(source['source'], cfg)
                        records.append(dict(id=source['id'], source=source['source'],
                                            text='기존 번역'+''.join(tokens),
                                            model='synthetic', fingerprint=fingerprint(cfg)))
                    (old/'data/translations.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records), encoding='utf-8')
                    before = {p.relative_to(old).as_posix(): p.read_bytes() for p in old.rglob('*') if p.is_file()}
                    path = new_source/'data/Map001.json'; doc = update.read(path, {})
                    commands = doc['events'][1]['pages'][0]['list']
                    commands[:0] = [dict(commands[0]), {'code': 401, 'indent': 0, 'parameters': ['Inserted message.']}]
                    save_json(path, doc)
                    with patch('project_update.ROOT', root):
                        target, active, report = update.update_project(old, cfg, new_source)
                        new_rows = read_catalog(target)
                        effective = cache(target, settings(target, active))
                        self.assertEqual(report['remaining'], 1)
                        self.assertGreater(report['reused'], 5)
                        self.assertEqual(next(r for r in new_rows if r['source'] == 'Inserted message.')['id'] in effective, False)
                        moved = next(r for r in new_rows if r['source'].startswith('Hello, '))
                        prior = next(r for r in old_rows if r['source'] == moved['source'])
                        self.assertNotEqual(moved['id'], prior['id'])
                        self.assertIn(moved['id'], effective)
                    after = {p.relative_to(old).as_posix(): p.read_bytes() for p in old.rglob('*') if p.is_file()}
                    self.assertEqual(before, after)

    def test_content_signature_detects_same_size_timestamp_update(self):
        from test_rpgmaker import fixture
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)/'Game'; fixture(source)
            info = {'engine': 'rpgmaker'}
            before = update._source_signature(source, info)
            path = source/'data/System.json'; stat = path.stat()
            raw = path.read_bytes(); self.assertIn(b'Test game', raw)
            path.write_bytes(raw.replace(b'Test game', b'Next game'))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertEqual(path.stat().st_size, stat.st_size)
            self.assertEqual(path.stat().st_mtime_ns, stat.st_mtime_ns)
            self.assertNotEqual(update._source_signature(source, info), before)


class NameInputReuseTests(unittest.TestCase):
    SCRIPT = '''default player_name = "Mira"
default nickname = "buddy"
label start:
    $ player_name = renpy.input("Your name?", default="Mira")
    $ nickname = renpy.input("Call me what?", default="buddy")
'''

    def setUp(self):
        from name_hints import discover
        from name_translation import collect
        from identity_policy import signature, VERSION
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.old = self.root/'old'; self.new = self.root/'new'
        for project in (self.old, self.new):
            (project/'data').mkdir(parents=True); (project/'staging/game').mkdir(parents=True)
            save_json(project/'project.json', CFG)
            save_json(project/'data/catalog.json', [row('dialogue', 'A greeting.')])
        (self.old/'staging/game/old.rpy').write_text(self.SCRIPT, encoding='utf-8')
        (self.new/'staging/game/renamed.rpy').write_text('\n\n'+self.SCRIPT, encoding='utf-8')
        self.metadata = discover({'old.rpy': self.SCRIPT}, [row('dialogue', 'A greeting.')])
        save_json(self.old/'data/name-hints.json', self.metadata)
        inventory = collect({'old.rpy': self.SCRIPT}, [row('dialogue', 'A greeting.')], self.metadata)
        types = {name: {'kind': 'name' if any(e.get('kind') == 'name default' for e in evidence) else 'address',
                        'signature': signature(evidence), 'version': VERSION} for name, evidence in inventory.items()}
        save_json(self.old/'data/identity-types.json', types)
        save_json(self.old/'data/name-transliterations.json', {'names': {'Mira': '미라'}, 'attempted': ['Mira'], 'sources': inventory})
        self.prompts = {'Your name?': '이름은 무엇인가요?', 'Call me what?': '어떻게 부를까요?'}
        save_json(self.old/'data/name-input-prompts.json', self.prompts)
        defaults = {item['key']: {'source': item['default'], 'text': '미라' if item['kind'] == 'name' else '친구',
                                 'kind': item['kind'], 'variable': item['variable']} for item in self.metadata['inputs']}
        save_json(self.old/'data/input-defaults.json', defaults)

    def test_moved_inputs_rekey_values_and_unchanged_names_phase_calls_no_model(self):
        from name_translation import run
        old_bytes = {p.relative_to(self.old).as_posix(): p.read_bytes() for p in self.old.rglob('*') if p.is_file()}
        counts = update._reuse_name_inputs(self.old, self.new, CFG)
        self.assertEqual(counts, {'identity_types': 2, 'name_spellings': 1, 'input_prompts': 2, 'input_defaults': 2})
        old_keys = set(update.read(self.old/'data/input-defaults.json', {}))
        new_keys = set(update.read(self.new/'data/input-defaults.json', {}))
        self.assertTrue(old_keys.isdisjoint(new_keys))
        with patch('name_translation.request', side_effect=AssertionError('No repeated name/input request')), \
             patch('hy_backend.runtime', side_effect=AssertionError('No model runtime')), \
             contextlib.redirect_stdout(io.StringIO()):
            _, report = run(self.new, dict(CFG, _names_only=True))
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(old_bytes, {p.relative_to(self.old).as_posix(): p.read_bytes() for p in self.old.rglob('*') if p.is_file()})

    def test_changed_identity_evidence_and_input_contract_are_not_reused(self):
        (self.new/'staging/game/renamed.rpy').write_text(self.SCRIPT.replace('buddy', 'pal').replace('Your name?', 'Your first name?'), encoding='utf-8')
        save_json(self.new/'data/catalog.json', [dict(row('dialogue', 'A greeting.'), speaker_name='Mira')])
        counts = update._reuse_name_inputs(self.old, self.new, CFG)
        self.assertEqual(counts['name_spellings'], 0)
        self.assertEqual(counts['input_prompts'], 1)
        self.assertEqual(counts['input_defaults'], 0)
        self.assertFalse((self.new/'data/name-transliterations.json').exists())

    def test_conflicting_default_values_are_not_selected_by_file_order(self):
        from name_hints import input_key
        extra = dict(self.metadata['inputs'][1], line=50)
        extra['key'] = input_key(extra)
        self.metadata['inputs'].append(extra)
        save_json(self.old/'data/name-hints.json', self.metadata)
        defaults = update.read(self.old/'data/input-defaults.json', {})
        defaults[extra['key']] = {'source': 'buddy', 'text': '벗', 'kind': 'address', 'variable': 'nickname'}
        save_json(self.old/'data/input-defaults.json', defaults)
        counts = update._reuse_name_inputs(self.old, self.new, CFG)
        self.assertEqual(counts['input_defaults'], 1)
        self.assertEqual(set(r['text'] for r in update.read(self.new/'data/input-defaults.json', {}).values()), {'미라'})


if __name__ == '__main__': unittest.main()
