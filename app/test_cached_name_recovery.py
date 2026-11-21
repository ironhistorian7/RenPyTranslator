"""Use real cache loading and resume; no engine or model is started."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import translation


class CachedNameRecoveryTests(unittest.TestCase):
    def test_false_fallback_uses_eligible_history_without_retranslation(self):
        for engine in ('renpy','rpgmaker','kirikiri'):
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as temp:
                project=Path(temp)
                cfg=dict(engine=engine,model='synthetic',source_language='japanese',language='korean',num_ctx=16384,
                         _person_names={'ミカ':'미카'},_rpg_name_groups=(('ミカ','미카'),))
                row=dict(id='a',source='ミカは？',kind='dialogue',usage='dialogue',file='synthetic',block='one',dialect='kag')
                save_json(project/'data/catalog.json',[row])
                prior=dict(id='a',source=row['source'],text='미카 미카?',model=cfg['model'],fingerprint=translation.fingerprint(cfg))
                latest=dict(prior,text=row['source'],status='source_fallback',error="a: ['no Korean translation']; output='미카 미카?'")
                path=project/'data/translations.jsonl'
                path.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in (prior,latest)),encoding='utf-8')
                before=path.read_bytes()
                with patch('name_translation.mapping',return_value=cfg['_person_names']), \
                     patch('name_review.configuration',side_effect=lambda project,settings,rows:settings), \
                     patch('translation.request',side_effect=AssertionError('history repair must not call model')), \
                     redirect_stdout(io.StringIO()):
                    item=translation.cache(project,cfg)['a']
                    self.assertEqual(item['text'],prior['text'])
                    self.assertEqual(item['status'],'failure_recovered')
                    translation.translate(project,cfg)
                self.assertEqual(path.read_bytes(),before)

    def test_ineligible_old_fingerprint_cannot_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            cfg=dict(engine='renpy',model='synthetic',language='korean',source_language='japanese',num_ctx=16384)
            row=dict(id='a',source='ミカは？',kind='dialogue',usage='dialogue',file='synthetic',block='one')
            save_json(project/'data/catalog.json',[row])
            prior=dict(id='a',source=row['source'],text='미카?',model='different',fingerprint='ineligible')
            latest=dict(prior,text=row['source'],model=cfg['model'],fingerprint=translation.fingerprint(cfg),
                        status='source_fallback',error='no Korean translation')
            (project/'data/translations.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in (prior,latest)),encoding='utf-8')
            self.assertEqual(translation.cache(project,cfg)['a']['status'],'source_fallback')


if __name__=='__main__':unittest.main()
