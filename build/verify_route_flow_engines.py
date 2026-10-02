"""Small compatibility fixture; no user game, story, model, or GPU is run."""
from pathlib import Path
import json
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'))
from engine import interpreter_args
from story_hints import write_support

CHECK=r'''
init -1 python:
    config.save_directory = None
    config.sound = False
    config.developer = False

init 1500 python:
    import renpy as _engine
    def _rpt_flow_check():
        import json
        namespace = dict(trust=2, stats={'love':3})
        assert _rpt_hint_condition('trust + 1 >= 3', namespace) is True
        assert _rpt_hint_condition('stats["love"] < 0', namespace) is False
        assert _rpt_hint_condition('custom_game_function()', namespace) is None
        reverted = {u'호감도':3}
        assert _rpt_hint_condition(u'stats["호감도"] == 3',dict(stats=reverted)) is True
        store.trust = 2
        entry = dict(hints=[
            dict(text=u'conditional',base_text=u'pass',guards=['trust + 1 >= 3'],color='#66DD88',bold=False),
            dict(text=u'hidden',guards=['trust > 9'],color='#FF7777',bold=False),
            dict(text=u'unknown',guards=['custom_game_function()'],color='#66DD88',bold=True)])
        shown = _rpt_hint_current(entry)
        assert [h['text'] for h in shown['hints']] == ['pass','unknown']
        assert trust == 2
        assert renpy.has_screen('_rpt_hint_choice')
        assert callable(_rpt_hint_menu)
        class Hero(object):
            pass
        hero = Hero()
        hero.trust = 4
        assert _rpt_hint_condition('hero.trust > 2',dict(hero=hero)) is True
        with open(config.basedir+'/result.json','w') as stream:
            json.dump(dict(version=_engine.version,checks=8,game_executed=False),stream)
        print('RPT_FLOW_OK')
        return False
    _engine.arguments.register_command('rpt-flow-check',_rpt_flow_check)

label start:
    $ raise Exception('Story must not execute')
'''

results=[]
for version in ('7.3.5','7.4.11','7.5.3','7.6.0','7.7.3','7.8.7','8.0.3','8.1.0','8.2.3','8.3.7','8.4.1','8.5.3'):
    sdk=ROOT/'build/renpy-validation'/('renpy-'+version+'-sdk')
    fixture=ROOT/'build/renpy-validation/route-flow'/version
    write_support(fixture,{'language':'korean'},{'answers':[],'routes':[]},fixture)
    (fixture/'game/script.rpy').write_text(CHECK,encoding='utf-8')
    interpreters=sorted((sdk/'lib').glob('*windows*/python.exe'),key=lambda p:'x86_64' not in str(p))
    command=interpreter_args(interpreters[0])+[str(sdk/'renpy.py'),str(fixture),'rpt-flow-check']
    env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',RENPY_RENDERER='sw',
             RENPY_PATH_TO_SAVES=str(fixture/'saves'),PYTHONDONTWRITEBYTECODE='1')
    completed=subprocess.run(command,cwd=fixture,env=env,capture_output=True,timeout=45,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    output=(completed.stdout+completed.stderr).decode('utf-8','replace')
    (fixture/'check.log').write_text(output,encoding='utf-8')
    success=completed.returncode==0 and 'RPT_FLOW_OK' in output
    print(version, 'PASS' if success else output[-3000:],flush=True)
    results.append({'version':version,'passed':success})
    if not success:break
(ROOT/'build/route-flow-compatibility.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
raise SystemExit(0 if len(results)==12 and all(r['passed'] for r in results) else 1)
