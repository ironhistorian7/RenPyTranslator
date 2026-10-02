"""Synthetic code-only regression tests: no game files, engines or local models."""
import json
import string
from pathlib import Path
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from name_hints import discover,ensure_metadata
from display_text import compose
from name_translation import install_runtime
from name_translation import run as repair_names
from translation import fingerprint
from engine import save_json
from test_story_hints import runtime as hint_runtime


def name_runtime(value=None, language='korean', location=('story.rpy',10)):
    calls=[]
    def previous(prompt,*args,**kwargs):
        calls.append((prompt,args,kwargs))
        return (args[0] if args else kwargs.get('default','')) if value is None else value
    engine=SimpleNamespace(input=previous,get_filename_line=lambda:location)
    ns={'renpy':engine,'config':SimpleNamespace(replace_text=None),
        '_preferences':SimpleNamespace(language=language),'_rpt_name_language':'korean',
        '_rpt_name_map':{'Bear':'베어','India':'인디아'},
        '_rpt_name_inputs':{'inputs':[{'file':'story.rpy','line':10,'end_line':13,'prompt':'Nickname?'}],
                            'prompts':{'Nickname?':'별명을 입력하세요.'}}}
    load_name_runtime(ns)
    return ns,calls


def load_name_runtime(ns):
    module=ModuleType('renpy.substitutions')
    module.formatter=ns.get('_rpt_name_formatter',string.Formatter())
    with patch.dict('sys.modules',{'renpy.substitutions':module}):
        exec(Path(__file__).with_name('names_runtime.py').read_text(encoding='utf-8'),ns)


class InputNameTests(unittest.TestCase):
    def test_multiline_alias_petname_and_prompt_semantics(self):
        source='''default petname = "Bear"
$ alias = renpy.input("What should I call you?", default="Buddy").strip()
$ nickname = renpy.input(
    "Your nickname?",
    default="Cub"
).strip()
$ address = renpy.input("Your nickname?", default="Bean")
$ answer = renpy.input("Enter the password.", default="Bear")
'''
        hints=discover({'story.rpy':source},[])
        self.assertEqual({k:v['representative'] for k,v in hints['names'].items()},
                         {'petname':'Bear','alias':'Buddy','nickname':'Cub','address':'Bean'})
        self.assertEqual(len(hints['inputs']),4)
        self.assertEqual(hints['inputs'][-1]['kind'],'answer')
        self.assertEqual(hints['inputs'][1]['end_line'],6)

    def test_old_metadata_is_refreshed_once(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);rows=[{'id':'a','source':'Hi [nickname].'}]
            save_json(project/'data/name-hints.json',{'version':1,'names':{}})
            first=ensure_metadata(project,rows,{'s.rpy':'default nickname = "Bear"'})
            self.assertEqual(first['names']['nickname']['representative'],'Bear')
            with patch('automatic.script_sources',side_effect=AssertionError('No repeat scan')):
                self.assertEqual(ensure_metadata(project,rows)['names'],first['names'])

    def test_default_is_localized_but_original_return_value_is_preserved(self):
        ns,calls=name_runtime()
        self.assertEqual(ns['renpy'].input('Nickname?',default='Bear'),'Bear')
        self.assertEqual(calls[0][0],'별명을 입력하세요.')
        self.assertEqual(calls[0][2]['default'],'베어')
        ns,calls=name_runtime()
        self.assertEqual(ns['renpy'].input('Nickname?','bear',None,None,40),'bear')
        self.assertEqual(calls[0][1],('베어',None,None,40))

    def test_positional_allow_exclude_and_length_keep_engine_order(self):
        for allow,exclude,length in (('Bear',None,40),(None,'베어',40),(None,None,1)):
            ns,calls=name_runtime()
            self.assertEqual(ns['renpy'].input('Nickname?','Bear',allow,exclude,length),'Bear')
            self.assertEqual(calls[-1][1],('Bear',allow,exclude,length))

    def test_legacy_field_adapter_preserves_scope_paths_and_other_fields(self):
        ns,_=name_runtime()
        get=ns['_rpt_name_formatter'].get_field
        scope={'person':SimpleNamespace(alias='Bear'),'names':['India'],'value':17}
        self.assertEqual(get('rpt_display_name(person.alias)',(),scope)[0],'베어')
        self.assertEqual(get('rpt_display_name(names[0])',(),scope)[0],'인디아')
        self.assertEqual(get('person.alias',(),scope)[0],'Bear')
        self.assertEqual(get('value',(),scope)[0],17)
        with self.assertRaises(KeyError):get('unrelated(value)',(),scope)

    def test_intermediate_engine_value_scope_carrier_is_preserved(self):
        ns,_=name_runtime()
        class ScopedFormatter(string.Formatter):
            def get_field(self,field,args,kwargs):
                value,used=super().get_field(field,args,kwargs)
                return (value,kwargs),used
        scope={'nickname':'Bear'}
        get=ns['_rpt_name_fields'](ScopedFormatter().get_field)
        (value,returned_scope),used=get('rpt_display_name(nickname)',(),scope)
        self.assertEqual(value,'베어')
        self.assertIs(returned_scope,scope)
        self.assertEqual(used,'nickname')

    def test_custom_input_password_other_language_and_constraints_unchanged(self):
        for typed in ('새 별명','Wolf','bear'):
            ns,_=name_runtime(typed)
            self.assertEqual(ns['renpy'].input('Nickname?',default='Bear'),typed)
        for options in ({'location':('story.rpy',99)},{'language':None}):
            ns,calls=name_runtime(**options)
            self.assertEqual(ns['renpy'].input('Nickname?',default='Bear'),'Bear')
            self.assertEqual(calls[0][2]['default'],'Bear')
        ns,calls=name_runtime()
        self.assertEqual(ns['renpy'].input('Nickname?',default='Bear',allow='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'),'Bear')
        self.assertEqual(calls[0][2]['default'],'Bear')

    def test_variable_only_display_case_and_reference_isolation(self):
        ns,_=name_runtime()
        for spelling in ('Bear','bear','BEAR'):
            self.assertEqual(ns['rpt_display_name'](spelling),'베어')
        self.assertEqual(ns['rpt_display_name']('Wolf'),'Wolf')
        self.assertEqual(ns['config'].replace_text('I saw a bear.'),'I saw a bear.')
        metadata={'names':{'nickname':{}},'reference_guard':True}
        out=compose({'kind':'dialogue','source':'Hi [nickname].'}, {'text':'안녕 [nickname].'},metadata)
        korean,reference=out.split('\n',1)
        self.assertIn('[rpt_display_name(nickname)!q]',korean)
        self.assertNotIn('rpt_display_name',reference)
        self.assertIn('rpt_reference_name(nickname)',reference)
        self.assertEqual(compose({'kind':'dialogue','source':'Hi [nickname].'}, {'text':'안녕 [nickname].'},metadata),out)

    def test_generated_helper_exists_even_without_any_name_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            install_runtime(project,{'language':'korean'})
            code=(project/'staging/game/zz_rpt_names.rpy').read_text(encoding='utf-8')
            self.assertIn('def rpt_display_name',code)
            import textwrap
            compile(textwrap.dedent(code.split('init 1100 python:\n',1)[1]),'<names>','exec')

    def test_repair_only_missing_names_and_prompts_then_reuses_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);source=p/'staging/game/story.rpy';source.parent.mkdir(parents=True)
            source.write_text('default nickname = "Bear"\n$ nickname = renpy.input("Nickname?", default="Bear")\n',encoding='utf-8')
            cfg={'language':'korean','model':'fake','endpoint':'http://localhost'}
            row={'id':'line','source':'Hello [nickname].','kind':'dialogue','file':'story.rpy'}
            save_json(p/'data/catalog.json',[row])
            cache=p/'data/translations.jsonl'
            cache.write_text(json.dumps(dict(row,text='안녕 [nickname].',model='fake',fingerprint=fingerprint(cfg)))+'\n',encoding='utf-8')
            before=cache.read_bytes()
            replies=[{'message':{'content':'{"0":"별명을 입력하세요."}'}},{'message':{'content':'{"0":"곰돌이"}'}}]
            with patch('hy_backend.runtime',return_value=nullcontext(cfg)),patch('name_translation.request',side_effect=replies) as request:
                changed,report=repair_names(p,cfg)
                self.assertEqual(changed,{'line'});self.assertEqual(request.call_count,2)
                self.assertNotIn('Hello',str(request.call_args_list))
            with patch('hy_backend.runtime',side_effect=AssertionError('No repeated model run')):
                changed,report=repair_names(p,cfg)
                self.assertEqual(report['model_calls'],0)
            self.assertEqual(cache.read_bytes(),before)
            install_runtime(p,cfg)
            inputs=json.loads((p/'staging/game/tl/korean/_rpt_name_inputs.json').read_text(encoding='utf-8'))
            self.assertEqual(inputs['prompts']['Nickname?'],'별명을 입력하세요.')

    def test_runtime_reinstall_does_not_nest_label_callbacks(self):
        ns,calls=name_runtime()
        old=ns['config'].replace_text;old_input=ns['renpy'].input
        old_field=ns['_rpt_name_formatter'].get_field
        load_name_runtime(ns)
        self.assertIs(ns['config'].replace_text,old)
        self.assertIs(ns['renpy'].input,old_input)
        self.assertIs(ns['_rpt_name_formatter'].get_field,old_field)
        self.assertEqual(ns['config'].replace_text('Other'),'Other')

    def test_hints_and_name_input_wrappers_coexist(self):
        ns,calls=name_runtime()
        ns['_rpt_hint_data']={'language':'korean','answers':[],'routes':[]}
        ns['menu']=lambda items:items
        # This is init order in generated patches: names (1100), hints (1300).
        exec(Path(__file__).with_name('hints_runtime.py').read_text(encoding='utf-8'),ns)
        self.assertEqual(ns['renpy'].input('Nickname?',default='Bear'),'Bear')
        self.assertEqual(calls[-1][2]['default'],'베어')

    def test_python_block_location_does_not_capture_password_input(self):
        source='python:\n    nickname = renpy.input("Nickname?", default="Bear")\n    answer = renpy.input("Password?", default="Bear")\n'
        data=discover({'story.rpy':source},[])
        self.assertEqual(data['inputs'][0]['statement_line'],1)
        ns,calls=name_runtime(location=('story.rpy',1))
        ns['_rpt_name_inputs']['inputs']=data['inputs']
        ns['_rpt_name_inputs']['inputs'][0]['translated_default']='곰돌이'
        self.assertEqual(ns['renpy'].input('Nickname?',default='Bear'),'Bear')
        self.assertEqual(calls[-1][2]['default'],'곰돌이')
        self.assertEqual(ns['renpy'].input('Password?',default='Bear'),'Bear')
        self.assertEqual(calls[-1][2]['default'],'Bear')


class HintCompatibilityTests(unittest.TestCase):
    def legacy(self):
        ns,calls,_=hint_runtime({},('story.rpy',1),{'Help':'도와준다'})
        del ns['renpy'].translate_string
        module=ModuleType('renpy.translation');module.translate_string=lambda s:{'Other':'다른 선택','Code?':'암호?'}.get(s,s)
        return ns,calls,module

    def test_cached_choice_does_not_evaluate_missing_default_function(self):
        ns,calls,module=self.legacy()
        self.assertEqual(ns['_rpt_hint_label']('Help'),'도와준다')
        ns['menu']([('Help',1)])
        self.assertEqual(calls[-1][0],'menu')

    def test_module_api_used_for_labels_matching_and_answers(self):
        ns,calls,module=self.legacy()
        with patch.dict('sys.modules',{'renpy.translation':module}):
            self.assertEqual(ns['_rpt_hint_label']('Other'),'다른 선택')
            entry={'file':'story.rpy','line':1,'source':'Code?','values':['123']}
            ns['_rpt_hint_index']['answers']={1:[entry]}
            self.assertIs(ns['_rpt_hint_match']('answers','암호?'),entry)
            self.assertEqual(ns['renpy'].input('Code?',default='keep'),'typed')
            self.assertIn('정답:',calls[-1][1])
            self.assertIn('암호?',calls[-1][1])


if __name__=='__main__':unittest.main()
