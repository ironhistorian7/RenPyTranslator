"""Verify the generated distribution only. No game or model inference is run."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

root=Path(__file__).resolve().parent.parent
archive=root/'RenPyTranslator-full.zip'
with zipfile.ZipFile(archive) as z:
    names=z.namelist()
    prefix='RenPyTranslator/'
    assert all(n.startswith(prefix) for n in names)
    assert not any(n.startswith(prefix+p) for n in names for p in ('projects/','project/','data/projects/','.venv/'))
    manifests=[n for n in names if '/manifests/' in n]
    assert len(manifests)==1 and manifests[0].endswith('rpt-hymt2-7b/q6_k')
    model=json.loads(z.read(manifests[0]))
    assert prefix+'_internal/layout_runtime.py' in names
    assert prefix+'_internal/size_runtime.py' in names
    assert prefix+'_internal/hints_runtime.py' in names
    assert prefix+'_internal/hints_layout.py' in names
    assert prefix+'_internal/hints_screen.rpy' in names
    assert prefix+'_desktop/RenPyTranslator-UI.exe' in names
    assert prefix+'_desktop/resources/app/main.js' in names
    assert not any('/node_modules/' in n or n.startswith(prefix+'desktop/') for n in names)
    for item in [model['config']]+model['layers']:
        name=prefix+'models/ollama/blobs/'+item['digest'].replace(':','-')
        assert z.getinfo(name).file_size==item['size']
    print('ZIP layout and model asset sizes OK; no game/project data.',flush=True)
    # Move both application runtimes to a separate path, keeping models idle.
    with tempfile.TemporaryDirectory(prefix='portable 한글 smoke-',dir=root/'build') as temp:
        destination=Path(temp).resolve()
        assert destination.is_relative_to((root/'build').resolve())
        for name in names:
            rel=name[len(prefix):]
            if rel.startswith(('_internal/','_desktop/','vendor/fonts/','vendor/unrpyc/')) or rel in ('RenPyTranslator-cli.exe','RenPyTranslator.exe','runtimes/ollama-0.34.2/ollama.exe'):
                target=(destination/rel).resolve();assert target.is_relative_to(destination)
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
        env=dict(os.environ,PATH=str(Path(os.environ['SystemRoot'])/'System32'),
                 PYTHONHOME=str(destination/'no-system-python'),PYTHONPATH=str(destination/'no-system-python'))
        for args in (['--self-check'],['repair','--project','DO-NOT-READ'],['tasks','--project','DO-NOT-READ'],['--unrpyc','--help']):
            result=subprocess.run([str(destination/'RenPyTranslator-cli.exe')]+args,cwd=destination,
                                  env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            assert result.returncode==0,result.stdout+result.stderr
            print(result.stdout if args==['--self-check'] else 'Portable CLI OK: '+' '.join(args),flush=True)
        gui=subprocess.run([str(destination/'RenPyTranslator.exe'),'--self-check'],cwd=destination,env=env,timeout=30,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert gui.returncode==0
        print('GUI executable self-check OK at relocated path; system Python paths excluded.',flush=True)
        subprocess.run([str(root/'runtimes/node-v24.21.0-win-x64/node.exe'),str(root/'desktop/test-portable.mjs'),str(destination)],cwd=root/'desktop',env=env,check=True,timeout=60)
        # Exercise new modules in the frozen CLI using an invented, tiny project only.
        fixture=destination/'synthetic-project';scripts=fixture/'data/recovered-scripts';scripts.mkdir(parents=True)
        (fixture/'project.json').write_text(json.dumps({'source':'DO-NOT-READ','language':'korean'}),encoding='utf-8')
        script='label start:\n    $ answer = renpy.input("\\Answer?")\n    if answer == "\\A":\n        jump accepted\n    menu:\n        "\\Accept":\n            $ trust += 1\n            "We meet by the river."\n            jump accepted\nlabel accepted:\n    return\n'
        script=script.replace('            $ trust += 1',
            '            $ visited = {"garden", "hall"}\n'
            '            $ metadata = {"nested": [set(), {1, "two"}], (1, "key"): b"bytes"}\n'
            '            $ trust += 1')
        (scripts/'sample.rpy').write_text(script,encoding='utf-8')
        # Seed an invented completed summary: exercise reuse without inference.
        sys.path.insert(0,str(root/'app'))
        from story_guides import parse
        from scene_hints import branches,scene_payloads,SCENE_VERSION
        choice,unique,_,_=branches(parse({'sample.rpy':script})[0])[0]
        key=scene_payloads(choice,unique,{})[0]['key']
        (fixture/'data/scene-summaries.json').write_text(json.dumps({key:{'summary':'강가에서 만남','model':'synthetic',
            'version':SCENE_VERSION,'status':'detail','choice_ko':'수락한다','evidence':[1]}}),encoding='utf-8')
        command=[str(destination/'RenPyTranslator-cli.exe'),'tasks','--project',str(fixture),'--tasks','answers','routes','--output',str(destination/'synthetic-output')]
        result=subprocess.run(command,
                              cwd=destination,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert result.returncode==0,result.stdout+result.stderr
        assert 'SyntaxWarning' not in result.stdout+result.stderr,result.stdout+result.stderr
        output=destination/'synthetic-output/DO-NOT-READ-kr'
        hints=json.loads((output/'game/tl/korean/rpt_hints/hints.json').read_text(encoding='utf-8'))
        answers=hints['answers']
        assert len(answers)==1 and answers[0]['source']==r'\Answer?' and answers[0]['values']==[r'\A']
        assert len(hints['routes'])==1
        assert any(h['text']=='장면: 강가에서 만남' for h in hints['routes'][0]['hints'])
        report=json.loads((fixture/'output/hints-report.json').read_text(encoding='utf-8'))
        assert report['model_calls']==0 and report['scenes_reused']==1
        assert (output/'game/zz_rpt_hints.rpy').exists()
        assert not (output/'guides').exists()
        assert not (fixture/'data/translations.jsonl').exists()
        result=subprocess.run(command,cwd=destination,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert result.returncode==0,result.stdout+result.stderr
        report=json.loads((fixture/'output/hints-report.json').read_text(encoding='utf-8'))
        assert report['effects_analyzed']==0 and report['model_calls']==0 and report['answers_reused']
        print('Frozen in-game hint tasks, nested set literals and cache reuse OK with synthetic scripts only.',flush=True)
    bad=z.testzip()
    assert bad is None,bad
    print('ZIP CRC verified.',flush=True)
with zipfile.ZipFile(root/'RenPyTranslator-light.zip') as light,zipfile.ZipFile(archive) as full:
    light_names=set(light.namelist());full_names=set(full.namelist())
    assert not any('/models/ollama/' in n for n in light_names)
    assert full_names-light_names=={n for n in full_names if '/models/ollama/' in n}
    assert all(light.getinfo(n).CRC==full.getinfo(n).CRC and light.getinfo(n).file_size==full.getinfo(n).file_size for n in light_names)
    assert light.testzip() is None
    print('LIGHT CRC and identical application assets verified; only bundled model files differ.',flush=True)
report={'zip':str(archive),'bytes':archive.stat().st_size,'checks':['asset allowlist','Hy model sizes','hint and layout runtime resources','relocated bundled Python and Electron','packaged GUI and settings save','CLI help','unrpyc help','synthetic in-game hint patch tasks','ZIP CRC'],
        'game_executed':False,'inference_executed':False,'real_projects_read':False,
        'limitation':'Not tested on a separate clean PC or against a real game.'}
(root/'build/portable-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
