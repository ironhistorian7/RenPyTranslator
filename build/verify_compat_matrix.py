"""Real SDK runtime tests. Invented fixtures only, product sources remain unchanged."""
from pathlib import Path
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
SDKROOT = ROOT / 'build/renpy-validation'
OUT = SDKROOT / 'compat-20260922-input-policy'
sys.path.insert(0, str(ROOT/'app'))
from engine import interpreter_args, save_json
from packaging import install_support
from story_hints import write_support
from name_translation import install_runtime

def constant(path, name):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))

BASE = constant(ROOT/'build/verify_patch_engines.py', 'CHECK')
BASE = BASE.replace("    _engine.arguments.register_command('rpt-patch-check', _rpt_validation_command)", '')

EXTRA = r'''
screen input(prompt):
    vbox:
        text prompt id "prompt"
        input id "input"

translate korean strings:
    old "Other choice"
    new "다른 선택"
    old "Code?"
    new "암호?"

label rpt_test_anchor:
    return

init 1600 python:
    def _compat_command():
        import json, traceback
        results = []
        renpy.game.context().init_phase = False
        renpy.game.context().current = 'rpt_test_anchor'
        renpy.game.context().rollback = True
        renpy.game.log.begin(force=True)
        _preferences.language = 'korean'
        def check(name, fn):
            try:
                detail = fn()
                results.append(dict(name=name, status='pass', detail=detail))
            except Exception:
                trace = traceback.format_exc()
                if isinstance(trace, bytes): trace = trace.decode('utf-8','replace')
                results.append(dict(name=name, status='fail', traceback=trace))
            with open(config.basedir + '/compat-result.json','w') as f:
                json.dump(dict(version=_engine.version, checks=results),f,ensure_ascii=True,indent=2)
            print('COMPAT %s %s' % (name,results[-1]['status']))

        check('font_reference_size_josa_layout', _rpt_validation_command)
        _preferences.language = 'korean'

        def label_checks():
            store._rpt_choices['Cached choice'] = u'캐시 선택'
            assert _rpt_hint_label('Cached choice') == u'캐시 선택'
            assert _rpt_hint_label('Other choice') == u'다른 선택'
            assert _rpt_hint_label('Unknown choice') == 'Unknown choice'
            filename,line = renpy.get_filename_line()
            e = dict(file=filename,line=line,source='Other choice',values=['Bear'])
            store._rpt_hint_index['answers'] = {line:[e]}
            assert _rpt_hint_match('answers',u'다른 선택') is e
            return dict(exports_alias=hasattr(renpy,'translate_string'), location=[filename,line])
        check('real_translation_api_cached_and_fallback',label_checks)

        def variables(which='korean'):
            store.nickname = 'Bear'
            assert renpy.substitute(u'[nickname!q]') == 'Bear'
            if which == 'reference':
                assert renpy.substitute(u'[rpt_reference_name(nickname)!q]') == 'Bear'
                return 'English reference interpolation'
            if which == 'josa':
                store.nickname = u'민수'
                assert renpy.substitute(u"[rpt_josa(nickname, u'은/는')]") == u'는'
                return 'Name particle interpolation'
            assert renpy.substitute(u'[rpt_display_name(nickname)!q]') == u'베어', repr((renpy.substitute(u'[rpt_display_name(nickname)!q]'),rpt_display_name(store.nickname),_preferences.language,config.new_substitutions))
            assert renpy.substitute(u'[rpt_reference_name(nickname)!q]') == 'Bear'
            store.nickname = u'새별명'
            assert renpy.substitute(u'[rpt_display_name(nickname)!q]') == u'새별명'
            store.nickname = 'bear'
            assert rpt_display_name(store.nickname) == u'베어'
            _preferences.language = None
            try: assert rpt_display_name('Bear') == 'Bear'
            finally: _preferences.language = 'korean'
            return 'Real interpolation and language switching'
        check('name_interpolation_and_english_reference',variables)
        check('english_reference_interpolation',lambda:variables('reference'))
        check('particle_interpolation',lambda:variables('josa'))

        def scoped_fields():
            scope = dict(aliases=['Bear'], nickname=u'베어', ordinary=17)
            assert renpy.substitute(u'[rpt_display_name(aliases[0])!q]',scope=scope) == u'베어'
            assert renpy.substitute(u'[rpt_reference_name(nickname)!q]',scope=scope) == 'Bear'
            assert renpy.substitute(u'[ordinary:03d]',scope=scope) == '017'
            assert renpy.substitute(u'[rpt_display_name(nickname)!q]',scope=dict(nickname='{b}Wolf{/b}')) == '{{b}Wolf{{/b}'
            if hasattr(_engine.config,'interpolate_exprs'):
                before = config.interpolate_exprs
                config.interpolate_exprs = False
                try:
                    assert renpy.substitute(u'[rpt_display_name(aliases[0])!q]',scope=scope) == u'베어'
                finally: config.interpolate_exprs = before
            return 'Local scope, indexed fields, quoting, ordinary formatting and expression-disabled mode'
        check('field_scope_conversions_and_plain_variables',scoped_fields)

        def inputs(positional=False, custom=False, answer=False, positional_length=False):
            filename,line = renpy.get_filename_line()
            store._rpt_name_inputs = dict(inputs=[dict(file=filename,line=line,prompt='Nickname?')], prompts={'Nickname?':u'별명을 입력하세요.'})
            if answer: store._rpt_name_inputs['inputs'] = []
            store._rpt_hint_index['answers'] = {line:[dict(file=filename,line=line,source='Code?',values=['Bear'])]} if answer else {}
            previous = ui.interact
            captured = {}
            def interact(**kwargs):
                screen = renpy.get_screen('input')
                assert screen is not None
                screen.update()
                widget = renpy.get_widget('input','input')
                captured['prompt'] = screen.scope['prompt']
                captured['default'] = widget.content
                if answer:
                    assert u'암호?' in captured['prompt'] and u'정답: Bear' in captured['prompt']
                    assert widget.content == 'Bear'
                elif positional and not positional_length:
                    assert widget.content == 'Bear'
                else:
                    assert captured['prompt'] == u'별명을 입력하세요.'
                    assert widget.content == u'베어'
                return u'새별명' if custom else widget.content
            ui.interact = interact
            try:
                if positional_length:
                    result = renpy.input('Nickname?', 'Bear', None, None, 20, with_none=False)
                elif positional:
                    result = renpy.input('Nickname?', 'Bear', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz', '{}', 20, with_none=False)
                else:
                    result = renpy.input('Code?' if answer else 'Nickname?', default='Bear', with_none=False)
                assert result == (u'새별명' if custom else 'Bear'), repr(result)
                captured['returned'] = result
                return captured
            finally:
                ui.interact = previous
                renpy.hide_screen('input')
        check('input_default_real_screen',inputs)
        check('input_custom_value_real_screen',lambda:inputs(custom=True))
        check('input_positional_constraints',lambda:inputs(positional=True))
        check('input_positional_length',lambda:inputs(positional_length=True))
        check('answer_prompt_real_screen',lambda:inputs(answer=True))
        def semantic_fields():
            store._rpt_name_map['Kenzie'] = u'켄지'
            store._rpt_pronunciations = {'kenzie':u'켄'}
            store._rpt_name_inputs = dict(variables={'nickname':dict(kind='address',values={'dude':u'친구'})})
            scope = dict(nickname='dude',person='Kenzie')
            assert renpy.substitute(u"[rpt_display_name(nickname, u'nickname')!q]",scope=scope) == u'친구'
            assert renpy.substitute(u"[rpt_josa(nickname, u'이/가', u'nickname')]",scope=scope) == u'가'
            assert renpy.substitute(u"[rpt_josa(person, u'이/가')]",scope=scope) == u'가'
            assert renpy.substitute(u'[rpt_reference_name(nickname)!q]',scope=scope) == 'dude'
            assert rpt_display_name('Bear','nickname') == 'Bear'
            return 'Address display, original reference, stale pronunciation override and contextual fields'
        check('semantic_fields_and_final_name_particles',semantic_fields)

        def semantic_input(kind,custom=False):
            filename,line=renpy.get_filename_line()
            original = 'secret' if kind=='answer' else ('dude' if kind=='address' else 'hello')
            translated = u'친구' if kind=='address' else u'안녕'
            question = 'What should I call you?' if kind=='address' else ('Password?' if kind=='answer' else 'Your greeting?')
            ko = u'어떻게 부를까요?' if kind=='address' else (u'암호를 입력하세요.' if kind=='answer' else u'인사말을 입력하세요.')
            entry=dict(file=filename,line=line,statement_line=line,prompt=question,default=original,kind=kind,variable='nickname',translated_default=translated)
            store._rpt_name_inputs=dict(inputs=[entry],prompts={question:ko},variables={})
            store._rpt_hint_index['answers']={line:[dict(file=filename,line=line,source=question,values=['secret'])]} if kind=='answer' else {}
            previous=ui.interact
            captured={}
            def interact(**kwargs):
                screen=renpy.get_screen('input'); screen.update()
                widget=renpy.get_widget('input','input')
                captured.update(prompt=screen.scope['prompt'],default=widget.content)
                assert ko in captured['prompt']
                assert widget.content == ('secret' if kind=='answer' else translated)
                if kind=='answer':
                    assert u'정답: secret' in captured['prompt']
                return u'내 입력' if custom else widget.content
            ui.interact=interact
            try:
                result=renpy.input(prompt=question,default=original,with_none=False)
                assert result == (u'내 입력' if custom else original)
                return captured
            finally:
                ui.interact=previous
                renpy.hide_screen('input')
        check('address_default_and_prompt',lambda:semantic_input('address'))
        check('address_custom_input_unchanged',lambda:semantic_input('address',True))
        check('general_text_default_and_prompt',lambda:semantic_input('text'))
        check('answer_default_preserved_with_localized_prompt_hint',lambda:semantic_input('answer'))
        # No display loop runs in this command; discard the autosave screenshot request.
        _engine.display.interface.bgscreenshot_needed = False
        print('COMPAT_MATRIX_DONE')
        return False
    _engine.arguments.register_command('rpt-compat',_compat_command)
'''

def checked_code(code):
    # SDK Python 2 launches in optimized mode. Never allow assert stripping to
    # turn runtime checks into false passes. These calls run with -O as well.
    code = code.replace("try: assert rpt_display_name('Bear') == 'Bear'", "try:\n                assert rpt_display_name('Bear') == 'Bear'")
    def replace(match):
        node = ast.parse(match.group(2)).body[0]
        args = ast.unparse(node.test)
        args += ', ' + (ast.unparse(node.msg) if node.msg else repr(match.group(2)))
        return match.group(1) + '_compat_require(' + args + ')'
    code = re.sub(r'^(\s*)(assert [^\n]+)', replace, code, flags=re.M)
    return '''init -2 python:
    def _compat_require(condition, message):
        if not condition:
            raise AssertionError(message)
''' + code

def run(version):
    sdk=SDKROOT/('renpy-'+version+'-sdk')
    project=OUT/version
    stage=project/'staging'
    game=stage/'game'
    game.mkdir(parents=True,exist_ok=True)
    (game/'script.rpy').write_text(checked_code(BASE+EXTRA),encoding='utf-8')
    save_json(project/'data/catalog.json',[])
    save_json(project/'data/name-hints.json',{'version':2,'names':{},'inputs':[]})
    save_json(project/'data/name-transliterations.json',{'names':{'Bear':'베어','India':'인디아'}})
    cfg=dict(language='korean',model='unused',_reference_guard=True)
    install_support(project,cfg)
    install_runtime(project,cfg)
    write_support(project,cfg,{'answers':[],'routes':[]},stage)
    interpreters=sorted((sdk/'lib').glob('*windows*/python.exe'),key=lambda p:'x86_64' not in str(p))
    env={k:v for k,v in os.environ.items() if k.upper() not in ('PYTHONHOME','PYTHONPATH','PYTHONOPTIMIZE')}
    env.update(SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',RENPY_RENDERER='sw',RENPY_PATH_TO_SAVES=str(project/'saves'),PYTHONDONTWRITEBYTECODE='1')
    command=interpreter_args(interpreters[0])+[str(sdk/'renpy.py'),str(stage),'rpt-compat']
    start=time.monotonic()
    try:
        result=subprocess.run(command,cwd=stage,env=env,capture_output=True,timeout=45,creationflags=subprocess.CREATE_NO_WINDOW)
        output=(result.stdout+result.stderr).decode('utf-8','replace')
        exitcode=result.returncode
    except subprocess.TimeoutExpired as e:
        output=((e.stdout or b'')+(e.stderr or b'')).decode('utf-8','replace')+'\nTIMEOUT'
        exitcode='timeout'
    (project/'validation-output.txt').write_text(output,encoding='utf-8')
    report=dict(version=version,exitcode=exitcode,seconds=round(time.monotonic()-start,2),command=command)
    rp=stage/'compat-result.json'
    if rp.exists():
        try: report.update(json.loads(rp.read_text(encoding='utf-8')))
        except ValueError: report['report_error']='Incomplete engine JSON; see validation-output.txt'
    save_json(project/'report.json',report)
    print(version,exitcode,output[-5000:],flush=True)
    return report

if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'app').glob('*') if p.is_file()}
    versions=sys.argv[1:] or ['7.3.5','7.4.11','7.5.3','7.6.0','7.7.3','7.8.7','8.0.3','8.1.0','8.2.3','8.3.7','8.4.1','8.5.3']
    saved=OUT/'matrix.json'
    reports=[r for r in json.loads(saved.read_text(encoding='utf-8')) if '.'.join(r['version'].split()[1].split('.')[:3]) not in versions] if sys.argv[1:] and saved.exists() else []
    for v in versions:
        reports.append(run(v))
        save_json(OUT/'matrix.json',reports)
    after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'app').glob('*') if p.is_file()}
    assert before==after,'Product files changed during test'
    save_json(OUT/'source-hashes.json',before)
