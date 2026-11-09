"""Run rebuilt portable CLI from a relocated folder on invented text-only data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_rpgmaker import fixture
from translation import fingerprint,protect

ROOT=Path(__file__).resolve().parents[1]


class PortableRPGMakerTests(unittest.TestCase):
    def test_relocated_bundled_cli_no_system_python_node_ollama_or_gameloc(self):
        bundle=ROOT/'dist/RenPyTranslator'
        with tempfile.TemporaryDirectory(prefix='RPG portable ') as tmp:
            root=Path(tmp);portable=root/'Moved tool';portable.mkdir()
            shutil.copy2(bundle/'RenPyTranslator-cli.exe',portable/'RenPyTranslator-cli.exe')
            shutil.copytree(bundle/'_internal',portable/'_internal')
            for area in ('vendor/gameloc','vendor/fonts'):
                shutil.copytree(bundle/area,portable/area)
            shutil.copy2(bundle/'runtime-lock.json',portable/'runtime-lock.json')
            source=root/'SyntheticRPG';fixture(source,'MZ',True)
            before={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
            env=dict(os.environ,PATH=str(Path(os.environ['SystemRoot'])/'System32'))
            env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
            def run(*args):
                result=subprocess.run([str(portable/'RenPyTranslator-cli.exe'),*args],cwd=portable,env=env,capture_output=True,encoding='utf-8',timeout=45)
                self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
                return result.stdout
            run('prepare','--source',str(source))
            project=portable/'data/projects/SyntheticRPG'
            cfg=json.loads((project/'project.json').read_text(encoding='utf-8'))
            rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8'))
            cached=[]
            for row in rows:
                _,tokens=protect(row['source'],cfg)
                cached.append(dict(id=row['id'],source=row['source'],text='번역'+''.join(tokens),model='fixture',fingerprint=fingerprint(cfg)))
            (project/'data/translations.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in cached),encoding='utf-8')
            cachebytes=(project/'data/translations.jsonl').read_bytes()
            run('rebuild','--project',str(project))
            output=portable/'project/SyntheticRPG-kr'
            self.assertTrue((output/'www/js/plugins/RPT_Bilingual.js').is_file())
            self.assertTrue((output/'www/fonts/RPT-NanumSquareNeo.ttf').is_file())
            run('status','--project',str(output));run('rebuild','--project',str(output))
            self.assertEqual(cachebytes,(project/'data/translations.jsonl').read_bytes())
            after={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
            self.assertEqual(before,after)


if __name__=='__main__':unittest.main()
