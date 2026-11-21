import io,json,tempfile,unittest
from zipfile import ZipFile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import translation
from engine import save_json
from replacements import effective_cache
from translation_review import apply_review,build_review


class ManualTranslationCacheTests(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);project=Path(temp.name)
        rows=[dict(id='a',source='こんにちは',kind='dialogue',usage='dialogue',file='sample.rpy',line=0,start=0,end=2)]
        cfg=dict(engine='rpgmaker',language='korean',source_language='japanese',model='synthetic',num_ctx=16384)
        save_json(project/'data/catalog.json',rows)
        return project,rows,cfg

    def test_manual_raw_values_survive_cache_and_effective_overlays(self):
        for value in ('','  untouched\n日本語 <tag> \\ANY[4]  '):
            with self.subTest(value=value):
                project,rows,cfg=self.fixture()
                review=build_review(rows,{'a':dict(status='source_fallback',text=rows[0]['source'])})
                apply_review(project,cfg,review,dict(fingerprint=review['fingerprint'],entries=[dict(id='a',apply=True,text=value)]))
                save_json(project/'replacements.json',{'replacements':{'untouched':'MODIFIED'}})
                self.assertEqual(translation.cache(project,dict(cfg,model='different-model'))['a']['text'],value)
                self.assertEqual(effective_cache(project,cfg)['a']['text'],value)

    def test_renpy_manual_literal_packages_with_reference_enabled(self):
        from packaging import package

        project,rows,cfg=self.fixture()
        cfg=dict(cfg,engine='renpy',source_language='english',show_reference=True)
        row=rows[0]
        value='  직접 입력한 "인용문"\n50% 그대로  '
        review=build_review(rows,{'a':dict(status='source_fallback',text=row['source'])})
        apply_review(project,cfg,review,dict(fingerprint=review['fingerprint'],entries=[dict(id='a',apply=True,text=value)]))
        cache_file=project/'data/translations.jsonl'
        original_cache=cache_file.read_bytes()
        asset='game/tl/korean/sample.rpy'
        rendered=project/'staging'/asset
        template=project/'data/templates/sample.rpy'
        template.parent.mkdir(parents=True)
        template.write_text('""\n',encoding='utf-8')
        save_json(project/'data/validation.json',{'entries':1,'errors':[]})
        metadata={'show_reference':True,'reference_guard':True}
        with patch('display_policy.install'),patch('packaging.verify_source',return_value={'checked':True}), \
             patch('translation.display_metadata',return_value=metadata), \
             patch('patch_validation.validate_final',side_effect=AssertionError('manual content must not be rejected')), \
             redirect_stdout(io.StringIO()):
            translation.render(project,cfg)
            self.assertEqual(translation.rendered_text(rendered.read_text(encoding='utf-8').strip(),row),value)
            package(project,cfg)
            with ZipFile(next((project/'output').glob('*.zip'))) as archive:
                self.assertEqual(archive.read(asset),rendered.read_bytes())
            rendered.write_text('"changed"\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Rendered file is stale: a'):
                package(project,cfg)
        self.assertEqual(cache_file.read_bytes(),original_cache)


if __name__=='__main__':unittest.main()
