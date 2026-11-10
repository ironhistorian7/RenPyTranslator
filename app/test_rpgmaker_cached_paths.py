"""Synthetic saved-project output regression; no model or actual game is used."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rpgmaker_project as adapter
from engine import save_json
from test_rpgmaker import fixture
from translation import fingerprint, protect


class CachedJSONPathTests(unittest.TestCase):
    def test_legacy_output_keeps_ids_cache_and_original_fallback(self):
        with tempfile.TemporaryDirectory(prefix='rpt-json-path-') as temporary:
            root=Path(temporary);source=root/'source';project=root/'project'
            (project/'data').mkdir(parents=True)
            fixture(source,'MZ')
            key='Synthetic...key[0]!'
            fallback='Other.key'
            table={'phrases':{key:{'ja':'Synthetic custom label'},
                              fallback:{'ja':'Synthetic kept original'}}}
            save_json(source/'data/TranslationTable.json',table)
            cfg=dict(source=str(source),engine='rpgmaker',source_language='english',
                     language='korean',model='synthetic',num_ctx=4096,
                     batch_size=1,parallel=1,context_txt_enabled=False)
            with contextlib.redirect_stdout(io.StringIO()):adapter.prepare(project,cfg)
            catalog=project/'data/catalog.json'
            saved_project=project/'data/rpgmaker-project.json'
            rows=json.loads(catalog.read_text(encoding='utf-8'))
            upstream=json.loads(saved_project.read_text(encoding='utf-8'))
            custom=[r for r in rows if Path(r['file']).name=='TranslationTable.json']
            self.assertEqual(len(custom),2)
            legacy={key:f'phrases.{key}.ja',fallback:f'phrases.{fallback}.ja'}
            by_id={e['id']:e for e in upstream['entries']}
            for row in custom:
                literal=key if row['source']=='Synthetic custom label' else fallback
                row['location']['ptr']=legacy[literal]
                by_id[row['id']]['locations'][0]['ptr']=legacy[literal]
            upstream['stats'].pop('rpt_path_format',None)
            save_json(catalog,rows);save_json(saved_project,upstream)
            cache=[]
            for row in rows:
                _,tokens=protect(row['source'],cfg)
                kept=row['source']=='Synthetic kept original'
                item=dict(id=row['id'],source=row['source'],
                          text=row['source'] if kept else '항목'+''.join(tokens),
                          model='synthetic',fingerprint=fingerprint(cfg))
                if kept:item.update(status='source_fallback',error='synthetic prior failure')
                cache.append(item)
            cachefile=project/'data/translations.jsonl'
            cachefile.write_text(''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in cache),encoding='utf-8')
            preserved={p:p.read_bytes() for p in (catalog,saved_project,cachefile)}
            originals={p:p.read_bytes() for p in source.rglob('*') if p.is_file()}
            # Rebuild old scope metadata as well, even when its prior audit is absent.
            (project/'data/rpg-context-scopes.json').unlink(missing_ok=True)
            from rpgmaker_scope import refresh
            with patch('translation.request',side_effect=AssertionError('No model request')), \
                 patch('runtime_recovery.run',side_effect=AssertionError('No model runtime')), \
                 contextlib.redirect_stdout(io.StringIO()):
                refresh(project,cfg)
                adapter.render(project,cfg)
            report=json.loads((project/'data/rpg-output.json').read_text(encoding='utf-8'))
            rendered=json.loads((Path(report['output'])/'data/TranslationTable.json').read_text(encoding='utf-8'))
            self.assertEqual(rendered['phrases'][key]['ja'],'항목')
            self.assertEqual(rendered['phrases'][fallback]['ja'],table['phrases'][fallback]['ja'])
            self.assertEqual(report['failed_original_kept'],1)
            for path,before in preserved.items():self.assertEqual(path.read_bytes(),before,path.name)
            for path,before in originals.items():self.assertEqual(path.read_bytes(),before,path.name)


if __name__=='__main__':unittest.main()
