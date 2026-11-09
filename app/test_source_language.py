"""Synthetic Japanese/English regressions. No games, models or network requests."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automatic import add_literal_templates, outline
from desktop_bridge import project_language, validate_settings
from engine import save_json
from hy_backend import body, DEFAULT_MODEL
from input_defaults import translate as input_defaults
from name_hints import discover, input_kind, INTERPOLATION
from name_translation import personal, occurrences
from source_language import normalize, source_units, apply
from task_plan import arguments
from translation import cache, fingerprint, protect, restore, validate_text, validate_entry
from ui_policy import discover as menus, menu_translations, apply_policy
from types import SimpleNamespace

CFG={'model':DEFAULT_MODEL,'language':'korean','source_language':'japanese','num_ctx':16384,
     'batch_size':8,'parallel':2,'_reuse_completed':True,'_reuse_previous_models':True}


class SourceLanguageTests(unittest.TestCase):
    def test_explicit_selection_and_legacy_default(self):
        self.assertEqual(normalize(None),'english')
        self.assertEqual(normalize('ja'),'japanese')
        with self.assertRaises(ValueError):normalize('automatic')
        self.assertEqual(validate_settings({'source_language':'japanese'})['source_language'],'japanese')
        self.assertIn('--source-language',arguments(['run'],'X:/game',True,source_language='japanese'))
        self.assertEqual(fingerprint(dict(CFG,source_language='english')),fingerprint({k:v for k,v in CFG.items() if k!='source_language'}))

    def test_requested_menus_and_custom_originals(self):
        script='''screen navigation():
    textbutton "NEW GAME" action Start()
    textbutton "CHAPTER SELECT" action ShowMenu("chapters")
    textbutton "CLOSE{#menu}" action Return()
    textbutton "謎の部屋" action ShowMenu("save")
    textbutton "設定" action ShowMenu("preferences")
'''
        english=menu_translations(menus({'screen.rpy':script}))
        japanese=menu_translations(menus({'screen.rpy':script}),'japanese')
        self.assertEqual(english['NEW GAME'],'새 게임')
        self.assertEqual(english['CHAPTER SELECT'],'챕터 선택')
        self.assertEqual(english['CLOSE{#menu}'],'{#menu}닫기')
        self.assertEqual(english['設定'],'設定')
        self.assertEqual(japanese['設定'],'설정')
        self.assertEqual(japanese['謎の部屋'],'謎の部屋')

    def test_japanese_literals_and_names_reach_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            scripts=project/'staging/game';scripts.mkdir(parents=True)
            (project/'data/templates').mkdir(parents=True)
            save_json(project/'data/catalog.json',[])
            (scripts/'sample.rpy').write_text('define h = Character("春香")\nscreen sample():\n    text "こんにちは。"\n    textbutton "次へ" action Return()\n',encoding='utf-8')
            structure=outline(project)
            self.assertEqual({x['source'] for x in structure['screen_literals']},{'こんにちは。','次へ'})
            add_literal_templates(project,CFG)
            rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8'))
            self.assertEqual({r['source'] for r in rows},{'春香','こんにちは。','次へ'})

    def test_format_misclassification_is_recovered_without_deleting_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            rows=[{'id':'bad','kind':'dialogue','source':'%sさん、こんにちは。'},
                  {'id':'good','kind':'dialogue','source':'彼女は静かに笑った。'},
                  {'id':'format','kind':'string','source':'%d / %d'}]
            save_json(project/'data/catalog.json',rows)
            old=[dict(rows[0],text=rows[0]['source'],model='literal format',fingerprint='old'),
                 dict(rows[1],text='그녀는 조용히 웃었다.',model=DEFAULT_MODEL,fingerprint='old')]
            path=project/'data/translations.jsonl'
            path.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in old)+'\n',encoding='utf-8')
            before=path.read_bytes()
            known=cache(project,CFG)
            self.assertNotIn('bad',known)
            self.assertEqual(known['good']['text'],'그녀는 조용히 웃었다.')
            self.assertEqual(known['format']['text'],'%d / %d')
            self.assertEqual(path.read_bytes(),before)

    def test_japanese_validation_preserves_variables_and_nonfatal_fallback(self):
        source='{b}[player_name]さん、こんにちは。{/b} %s'
        protected,tokens=protect(source)
        self.assertEqual(len(tokens),4)
        self.assertEqual(restore(protected,tokens),source)
        self.assertIn('no Korean translation',validate_text(source,source,CFG))
        self.assertEqual(validate_entry(source,{'text':source,'status':'source_fallback'},CFG),[])
        self.assertEqual(validate_text('春香','하루카',CFG),[])
        self.assertEqual(validate_text('%sさん、こんにちは。','%s 씨, 안녕하세요.',CFG),[])

    def test_inputs_distinguish_name_address_and_answer(self):
        self.assertEqual(input_kind('hero','名前を入力してください'),'name')
        self.assertEqual(input_kind('label','どう呼べばいい？'),'address')
        self.assertEqual(input_kind('value','合言葉を入力してください'),'answer')
        self.assertTrue(INTERPOLATION.fullmatch('[主人公]'))
        self.assertTrue(personal('春香'))
        self.assertFalse(personal('こんにちは。'))
        self.assertTrue(occurrences('春香さんが来た。','春香'))
        self.assertFalse(occurrences('Cartoon','Car'))
        metadata=discover({'sample.rpy':'$ hero = renpy.input("名前を入力してください", default="春香")\n$ secret = renpy.input("合言葉を入力してください", default="桜")\n'},[])
        self.assertEqual([e['kind'] for e in metadata['inputs']],['name','answer'])
        entries=[dict(key='a',variable='nickname',default='お兄ちゃん',prompt='呼び名は？',kind='address'),
                 dict(key='b',variable='password',default='桜',prompt='合言葉は？',kind='answer')]
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            def ask(prompt,schema):calls.append(prompt);return {'0':'오빠'}
            result=input_defaults(Path(temp),entries,{}, {},{},ask,{'issues':[]},cfg=CFG)
            self.assertEqual(result['a']['text'],'오빠')
            self.assertNotIn('b',result)
            self.assertEqual(len(calls),1)
            self.assertIn('일본어',calls[0])

    def test_hy_profile_is_unchanged_and_source_is_explicit(self):
        inputs=[{'id':'0','text':'彼女は静かに笑った。','kind':'dialogue','name_hints':{},'movable_placeholders':[]}]
        request=body(inputs,{},CFG,{},'')
        self.assertIn('원문 언어: 일본어',request['messages'][0]['content'])
        self.assertEqual(request['options']['num_ctx'],16384)
        self.assertEqual(request['options']['num_predict'],1800)
        self.assertEqual(source_units('I love you baby.'),4)
        self.assertGreater(source_units('彼女は静かに笑いながらこちらを見ていた。'),1)

    def test_project_language_restores_metadata_without_reading_scripts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project=root/'data/projects/example'
            save_json(project/'project.json',dict(CFG,source='X:/example'))
            output=root/'output';output.mkdir()
            save_json(output/'project-link.json',{'project':'../data/projects/example'})
            self.assertEqual(project_language({'path':str(output)},root)['source_language'],'japanese')
            apply(project,CFG,'en')
            self.assertEqual(project_language({'path':str(output)},root)['source_language'],'english')

    def test_reference_keeps_japanese_and_font_fills_only_known_gaps(self):
        from display_text import compose
        displayed=compose({'kind':'dialogue','source':'春香さん、こんにちは。'},
                          {'text':'하루카 씨, 안녕하세요.'},{})
        self.assertIn('{size=*0.55}',displayed)
        self.assertIn('春香さん、こんにちは。',displayed)
        class Group:
            def __init__(self):self.items=[]
            def add(self,font,start,end):self.items.append((font,start,end))
        cfg=SimpleNamespace(font_transforms={},font_name_map={},font_replacement_map={})
        prefs=SimpleNamespace(language='korean',font_transform=None)
        data={'fonts':{'original.ttf':'regular','icons.ttf':'icon'},'coverage':{'regular':[[0xac00,0xd7a3]],'hand':[[0xac00,0xd7a3]]},
              'preferred':{'regular':'neo.ttf','hand':'pen.ttf'},'fallback':'noto.otf','bold':'bold.ttf',
              'japanese_missing':{'original.ttf':[[0x3042,0x3042]]}}
        ns={'config':cfg,'preferences':prefs,'_preferences':prefs,'basestring':str,'FontGroup':Group,
            '_rpt_language':'korean','_rpt_choices':{},'_rpt_fontdata':data,'menu':lambda items:items}
        exec(Path(__file__).with_name('presentation_runtime.py').read_text(encoding='utf-8'),ns)
        group=ns['_rpt_font_group']('original.ttf')
        self.assertIn(('noto.otf',0x3042,0x3042),group.items)
        self.assertNotIn(('noto.otf',0x3044,0x3044),group.items)
        self.assertEqual(group.items[-1],('original.ttf',None,None))
        self.assertEqual(ns['_rpt_font_group']('icons.ttf'),'icons.ttf')

    def test_policy_preserves_unknown_menu_and_story_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);scripts=project/'staging/game';scripts.mkdir(parents=True)
            save_json(project/'project.json',CFG)
            (scripts/'sample.rpy').write_text('screen navigation():\n    textbutton "設定" action ShowMenu("preferences")\n    textbutton "謎の部屋" action ShowMenu("custom")\nlabel start:\n    menu:\n        "彼女を追いかける":\n            pass\n',encoding='utf-8')
            sources=['設定','謎の部屋','彼女を追いかける']
            rows=[dict(id=str(i),kind='string',source=s) for i,s in enumerate(sources)]
            known=apply_policy(project,rows,{},preserve_choices=True)
            self.assertEqual(known['0']['text'],'설정')
            self.assertEqual(known['1']['text'],'謎の部屋')
            self.assertNotIn('2',known)


if __name__=='__main__':unittest.main()
