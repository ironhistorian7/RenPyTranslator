"""Invented distributions only. Never access user games, model, or game runtime."""
import contextlib
import io
import json
import pickle
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from engine import extract_scripts, prepare, seal_stage, save_json, verify_source
from workspace_files import plan, create, clone, metadata, command, SCRIPT_ARCHIVE
from workspace_runtime import install


def archive(path, entries):
    path.parent.mkdir(parents=True,exist_ok=True)
    index={}
    with path.open('wb') as stream:
        stream.write(b'RPA-3.0 0000000000000000 00000000\n')
        for name,payload in entries.items():
            index[name]=[(stream.tell(),len(payload))];stream.write(payload)
        offset=stream.tell();stream.write(zlib.compress(pickle.dumps(index,protocol=2)))
        stream.seek(0);stream.write(('RPA-3.0 %016x 00000000\n' % offset).encode('ascii'))


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'input';self.project=self.root/'workspace'
        self.project.mkdir()
        for name,payload in {'renpy/loader.py':b'# engine', 'lib/windows-x86_64/python.exe':b'fake',
                'lib/pythonlib2.7/site.pyo':b'stdlib', 'lib/linux-x86_64/python':b'other OS',
                'Game.py':b'import renpy.bootstrap\nrenpy.bootstrap.bootstrap(".")\n',
                'Game.exe':b'launcher', 'log.txt':b'not input',
                'game/script.rpy':b'label start:\n    "Invented"\n',
                'game/fonts/example.ttf':b'font', 'game/data/settings.json':b'{}',
                'game/helpers/native.pyd':b'needed native module', 'game/helpers/native.dll':b'needed dependency',
                'game/context/global.txt':b'guidance', 'game/context_test/huge.txt':b'test only',
                'game/images/example.png':b'image' * 100000, 'game/audio/theme.ogg':b'audio',
                'game/video/demo.webm':b'video', 'game/cache/bytecode.rpyb':b'cache',
                'game/saves/persistent':b'save'}.items():
            path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
        archive(self.source/'game/archive.rpa',{'chapter.rpyc':b'original nodes',
                'chapter.rpy':b'label extra:\n    "Invented extra"\n',
                'helpers/util.py':b'VALUE = 1\n', 'fonts/archive.ttf':b'archive font',
                'settings/archive.json':b'{"saved":true}',
                'images/archived.png':b'media' * 200000})

    def test_copies_inputs_and_selected_windows_only_without_reading_loose_media(self):
        original_open=Path.open
        def protected(path,*args,**kwargs):
            if path.suffix.lower() in ('.png','.ogg','.webm'):
                raise AssertionError('Media contents must not be copied/read')
            return original_open(path,*args,**kwargs)
        with patch.object(Path,'open',protected):
            selection=plan(self.source);create(self.project,self.source,selection)
        stage=self.project/'staging'
        for name in ('renpy/loader.py','lib/windows-x86_64/python.exe','lib/pythonlib2.7/site.pyo',
                     'game/script.rpy','game/fonts/example.ttf','game/data/settings.json',
                     'game/context/global.txt','game/chapter.rpyc','game/helpers/util.py'):
            self.assertTrue((stage/name).is_file(),name)
        self.assertTrue((stage/'game/helpers/native.pyd').is_file())
        self.assertTrue((stage/'game/helpers/native.dll').is_file())
        for name in ('lib/linux-x86_64/python','log.txt','game/images/example.png','game/audio/theme.ogg',
                     'game/video/demo.webm','game/archive.rpa','game/cache/bytecode.rpyb',
                     'game/saves/persistent','game/context_test/huge.txt'):
            self.assertFalse((stage/name).exists(),name)
        saved=metadata(self.project)
        self.assertGreater(saved['skipped_bytes'],1000000)
        self.assertLess((stage/'game'/SCRIPT_ARCHIVE).stat().st_size,1000)
        self.assertIn('images/example.png',saved['loose_assets'])
        self.assertNotIn('script.rpy',saved['loose_assets'])
        self.assertIn('helpers/util.py', [p.relative_to(stage/'game').as_posix()
                      for p in (stage/'game').rglob('*.py')])

    def test_seal_preserves_original_archive_bytes_and_excludes_media(self):
        scripts=create(self.project,self.source)
        save_json(self.project/'data/extracted-scripts.json',[p.relative_to(self.project/'staging/game').as_posix()
                  for p in scripts if p.suffix in ('.rpy','.rpyc','.rpym','.rpymc')])
        save_json(self.project/'data/recovered-source-files.json',['chapter.rpy'])
        seal_stage(self.project)
        game=self.project/'staging/game'
        self.assertFalse((game/'chapter.rpyc').exists())
        self.assertTrue((self.project/'data/recovered-scripts/chapter.rpy').is_file())
        dest=self.root/'inspect';dest.mkdir()
        extract_scripts(game/SCRIPT_ARCHIVE,dest)
        self.assertEqual((dest/'chapter.rpyc').read_bytes(),b'original nodes')
        self.assertFalse((dest/'images').exists())
        self.assertEqual((game/'helpers/util.py').read_text(),'VALUE = 1\n')
        self.assertEqual((game/'settings/archive.json').read_text(),'{"saved":true}')

    def test_preparation_and_hash_check_ignore_media_changes_but_track_script_changes(self):
        cfg={'source':str(self.source),'language':'korean'}
        with patch('engine.engine_command'):
            prepare(self.project,cfg)
        saved=metadata(self.project)
        scope=json.loads((self.project/'data/source-manifest.json').read_text())
        self.assertNotIn('game/images/example.png',scope)
        self.assertIn('game/archive.rpa',scope)
        (self.source/'game/images/example.png').write_bytes(b'changed media')
        self.assertTrue(verify_source(self.project,cfg)['unchanged'])
        (self.source/'game/script.rpy').write_text('changed script')
        with self.assertRaisesRegex(RuntimeError,'Original source changed'):
            verify_source(self.project,cfg)
        self.assertEqual(metadata(self.project),saved)

    def test_clone_of_old_full_snapshot_is_small_and_does_not_modify_parent(self):
        parent=self.project; (parent/'staging').mkdir()
        import shutil
        shutil.copytree(self.source,parent/'staging',dirs_exist_ok=True)
        target=self.root/'comparison';target.mkdir()
        before=(parent/'staging/game/archive.rpa').read_bytes()
        clone(parent,target)
        self.assertFalse((target/'staging/game/images/example.png').exists())
        self.assertFalse((target/'staging/game/archive.rpa').exists())
        self.assertEqual((parent/'staging/game/archive.rpa').read_bytes(),before)
        self.assertEqual(metadata(target)['source_game'],str((parent/'staging/game').resolve()))

    def test_clone_of_compact_snapshot_keeps_original_archive_and_asset_reference(self):
        create(self.project,self.source)
        target=self.root/'comparison';target.mkdir()
        original=(self.project/'staging/game'/SCRIPT_ARCHIVE).read_bytes()
        clone(self.project,target)
        self.assertEqual((target/'staging/game'/SCRIPT_ARCHIVE).read_bytes(),original)
        self.assertEqual(metadata(target)['source_game'],metadata(self.project)['source_game'])
        self.assertEqual(metadata(target)['original_archive_scripts'],metadata(self.project)['original_archive_scripts'])
        self.assertFalse((target/'staging/game/archive.rpa').exists())

    def test_overlap_respects_archive_priority_and_loose_script_override(self):
        archive(self.source/'game/z.rpa',{'chapter.rpyc':b'high priority'})
        create(self.project,self.source)
        self.assertEqual((self.project/'staging/game/chapter.rpyc').read_bytes(),b'high priority')
        dest=self.root/'second';dest.mkdir()
        (dest/'chapter.rpyc').write_bytes(b'loose')
        extract_scripts(self.source/'game/z.rpa',dest)
        self.assertEqual((dest/'chapter.rpyc').read_bytes(),b'loose')

    def test_legacy_engine_command_is_unchanged_and_compact_command_uses_bridge(self):
        launcher=self.source/'Game.py'
        self.assertEqual(command(self.project,launcher,['compile']),[str(launcher),str(self.project/'staging'),'compile'])
        create(self.project,self.source)
        args=command(self.project,self.project/'staging/Game.py',['compile'])
        self.assertEqual(Path(args[0]).name,'workspace-bootstrap.py')
        self.assertEqual(args[-2:],[str(self.project/'staging'),'compile'])
        (self.source/'game').rename(self.source/'gone')
        with self.assertRaisesRegex(FileNotFoundError,'restore it'):
            command(self.project,self.project/'staging/Game.py',['compile'])

    def test_media_hint_and_font_inventory_keep_original_asset_evidence(self):
        from workspace_files import media_names,external_archives
        create(self.project,self.source)
        names,warnings=media_names(self.project)
        self.assertIn('images/example.png',names);self.assertIn('images/archived.png',names)
        self.assertEqual(warnings,[])
        self.assertEqual(external_archives(self.project),[self.source/'game/archive.rpa'])


class BridgeTests(unittest.TestCase):
    def test_three_loader_api_families_keep_assets_but_never_external_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';(source/'game/images').mkdir(parents=True)
            image=source/'game/images/example.png';image.write_bytes(b'picture')
            original=source/'game/archive.rpa';original.touch()
            own=root/'workspace/staging/game';own.mkdir(parents=True)
            record=root/'workspace/data/workspace-files.json'
            settings={'source_root':str(source),'source_game':str(source/'game'),
                'loose_assets':['images/example.png'],'archives':['game/archive.rpa'],
                'original_archive_scripts':['chapter.rpyc'],'_record':str(record)}
            for family in ('7.3.5','7.8.7','8.5.3'):
                with self.subTest(family=family):
                    loader=SimpleNamespace(archives=[],lower_map={},loadable_cache={})
                    config=SimpleNamespace(archives=['_rpt_original_scripts'])
                    def base_transfn(name):raise FileNotFoundError(name)
                    def base_listdir(*args,**kwargs):
                        return [(None,'chapter.rpyc'),(None,'external.rpyc'),(str(own),'tl/korean/chapter.rpy')]
                    def base_index():
                        path=str(original) if family!='7.3.5' else str(original)[:-4]
                        loader.archives=[('_rpt_original_scripts.rpa',{'chapter.rpyc':[]}),
                                         (path,{'chapter.rpyc':[],b'external.rpyc':[],
                                                'helpers/util.py':[],'images/archived.png':[]})]
                    loader.transfn=base_transfn;loader.listdirfiles=base_listdir;loader.index_archives=base_index
                    if family=='8.5.3':loader.arc_files=[]
                    engine=SimpleNamespace(loader=loader,config=config)
                    install(engine,settings);loader.index_archives()
                    for callback in (loader.transfn,loader.listdirfiles,loader.index_archives):
                        for protocol in (0,2,pickle.HIGHEST_PROTOCOL):
                            self.assertIs(pickle.loads(pickle.dumps(callback,protocol)),callback)
                    # The engine also serializes the complete module snapshot.
                    self.assertEqual(pickle.loads(pickle.dumps(vars(loader),2))['index_archives'],loader.index_archives)
                    self.assertEqual(loader.transfn('images/EXAMPLE.png'),str(image))
                    self.assertEqual(loader.transfn(str(original)),str(original))
                    self.assertEqual(list(loader.archives[1][1]),['images/archived.png'])
                    rows=loader.listdirfiles()
                    self.assertIn((str(source/'game'),'images/example.png'),rows)
                    self.assertNotIn((None,'external.rpyc'),rows)
                    self.assertNotIn((str(source/'game'),'images/example.png'),loader.listdirfiles(game=False))
                    if family=='8.5.3':self.assertEqual(loader.arc_files[0][2],str(original))
                    else:self.assertIn(str(original)[:-4],config.archives)
                    with self.assertRaises(FileNotFoundError):loader.transfn('../elsewhere.png')
                    with self.assertRaises(FileNotFoundError):loader.transfn('external.rpyc')

    def test_import_bridge_is_installed_before_bootstrap_on_invented_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);stage=root/'staging';package=stage/'renpy';package.mkdir(parents=True)
            (package/'__init__.py').write_text('from types import SimpleNamespace\nconfig = SimpleNamespace(archives=[])\n')
            (package/'loader.py').write_text('archives=[]\nlower_map={}\nloadable_cache={}\n'
                'def transfn(name):\n    raise IOError(name)\n'
                'def listdirfiles(common=True):\n    return []\n'
                'def index_archives():\n    return None\n')
            media=root/'assets/game/image.png';media.parent.mkdir(parents=True);media.write_bytes(b'invented')
            launcher=stage/'Game.py'
            launcher.write_text('import renpy.loader\nimport sys\nimport pickle\n'
                'for callback in (renpy.loader.transfn, renpy.loader.listdirfiles, renpy.loader.index_archives):\n'
                '    assert callback.__module__ == "rpt_workspace_assets"\n'
                '    for protocol in (0, 2, pickle.HIGHEST_PROTOCOL):\n'
                '        assert pickle.loads(pickle.dumps(callback, protocol)) is callback\n'
                'assert pickle.loads(pickle.dumps(vars(renpy.loader), 2))["index_archives"] is renpy.loader.index_archives\n'
                'assert renpy.loader.transfn("image.png").endswith("image.png")\n'
                'assert sys.argv[1:] == [%r, "compile"]\nprint("fixture bridge ready")\n' % str(stage))
            record=root/'data/workspace-files.json'
            save_json(record,{'source_root':str(root/'assets'),'source_game':str(media.parent),
                'loose_assets':['image.png'],'archives':[],'original_archive_scripts':[]})
            helper=Path(__file__).with_name('workspace_runtime.py')
            result=subprocess.run([sys.executable,'-B',str(helper),str(record),str(launcher),str(stage),'compile'],
                capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('fixture bridge ready',result.stdout)

    def test_install_twice_keeps_original_callbacks_and_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);image=root/'image.png';image.write_bytes(b'invented')
            def original(name):raise IOError(name)
            loader=SimpleNamespace(transfn=original,listdirfiles=lambda:[],index_archives=lambda:None,
                                   archives=[],lower_map={},loadable_cache={})
            engine=SimpleNamespace(loader=loader,config=SimpleNamespace(archives=[]))
            settings={'source_game':str(root),'source_root':str(root),'loose_assets':['image.png'],
                      'archives':[],'original_archive_scripts':[]}
            install(engine,settings);install(engine,settings)
            self.assertEqual(loader.transfn('image.png'),str(image))


if __name__=='__main__':unittest.main()
