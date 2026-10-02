import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import model_store as store


def sample_gguf(name='Tiny Instruct'):
    items={'general.name':name,'general.architecture':'llama','tokenizer.chat_template':'{{ messages }}'}
    def string(s):b=s.encode();return struct.pack('<Q',len(b))+b
    return b'GGUF'+struct.pack('<IQQ',3,1,len(items))+b''.join(string(k)+struct.pack('<I',8)+string(v) for k,v in items.items())+b'fake tensor'


class Response(io.BytesIO):
    status=200
    headers={}


class ModelStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);p=patch.object(store,'ROOT',self.root);p.start();self.addCleanup(p.stop)
        p=patch.object(store,'ensure_idle');p.start();self.addCleanup(p.stop)

    def install(self,name='example:latest',payload=b'weights',shared=None):
        digest=shared or 'sha256:'+hashlib.sha256(payload).hexdigest();blob=store.blob(digest);blob.parent.mkdir(parents=True,exist_ok=True);blob.write_bytes(payload)
        path=store.model_root()/'ollama/manifests/registry.ollama.ai/library'/name.replace(':','/')
        store.save(path,{'config':{'digest':digest,'size':len(payload)},'layers':[{'mediaType':'application/vnd.ollama.image.model','digest':digest,'size':len(payload)}]})
        return blob,path

    def test_none_is_explicit_and_never_defaults_to_installed_hy(self):
        self.install('rpt-hymt2-7b:q6_k');self.assertIsNone(store.selected())
        self.assertEqual(store.problem()['title'],'사용할 모델을 선택해 주세요.')
        store.choose('rpt-hymt2-7b:q6_k');self.assertIsNone(store.problem())
        store.choose(None);self.assertIsNone(store.selected())
        with self.assertRaises(ValueError):store.require_selected()

    def test_missing_file_and_no_install_are_different(self):
        self.assertEqual(store.problem()['title'],'번역 모델이 필요합니다.')
        blob,_=self.install();store.choose('example:latest');blob.unlink()
        self.assertEqual(store.problem()['title'],'선택한 모델을 찾을 수 없습니다.')

    def test_delete_preserves_shared_blobs_and_clears_selection(self):
        blob,_=self.install('one:latest');self.install('two:latest');store.choose('one:latest')
        store.remove('one:latest');self.assertTrue(blob.exists());self.assertIsNone(store.selected())
        store.remove('two:latest');self.assertFalse(blob.exists())

    def test_metadata_and_manual_registration_without_inference(self):
        path=store.model_root()/'tiny.gguf';path.parent.mkdir();path.write_bytes(sample_gguf())
        self.assertEqual(store.inventory()['pending'],['tiny.gguf'])
        opener=type('Opener',(),{'open':lambda *a,**k:Response(b'{"status":"success"}')})()
        # Simulate the API writing its manifest, using the submitted digest.
        def create(req,**kwargs):
            data=json.loads(req.data);digest=next(iter(data['files'].values()));self.install(data['model'],path.read_bytes(),digest)
            return Response(b'{"status":"success"}')
        opener.open=create
        with patch('hy_backend.runtime',side_effect=lambda cfg,**kw:contextlib.nullcontext({'endpoint':'http://127.0.0.1:1'})),patch.object(store.urllib.request,'build_opener',return_value=opener),contextlib.redirect_stdout(io.StringIO()):
            model=store.register(path,manual=True)
        self.assertEqual(store.protocol(model),'generic');self.assertEqual(store.inventory()['pending'],[])
        self.assertIsNone(store.selected());self.assertTrue(path.exists())
        store.remove(model);self.assertFalse(path.exists())

    def test_invalid_gguf_and_outside_path_rejected(self):
        p=store.model_root()/'bad.gguf';p.parent.mkdir();p.write_bytes(b'not a model')
        with self.assertRaises(ValueError):store.gguf(p)
        with self.assertRaises(ValueError):store.bounded(self.root/'other.gguf')

    def test_existing_checkpoint_title_is_corrected_without_registration(self):
        self.install('local:latest')
        store.save(self.root/'data/model-library.json',{'local:latest':{
            'title':'Global_Step_560','local_files':[{'local_file':'nested/Model-Q6_K.gguf'}]}})
        before=(self.root/'data/model-library.json').read_bytes()
        with patch.object(store,'register',side_effect=AssertionError('No registration needed')):
            model=store.inventory()['installed'][0]
        self.assertEqual(model['title'],'Model-Q6_K.gguf');self.assertEqual(model['id'],'local:latest')
        self.assertEqual((self.root/'data/model-library.json').read_bytes(),before)
        self.assertEqual(store.display_name('id:tag',{'title':'Step_123'}),'id:tag')
        self.assertEqual(store.display_name('id:tag',{'local_file':'old/Legacy.gguf'}),'Legacy.gguf')
        self.assertEqual(store.display_name('id:tag',{'filename':'repo/Download.gguf','local_file':'copy.gguf'}),'Download.gguf')

    def test_new_manual_registration_uses_filename_not_embedded_name(self):
        path=store.model_root()/'nested/Model-Q6_K.gguf';path.parent.mkdir(parents=True)
        path.write_bytes(sample_gguf('Global_Step_560'));self.install('existing:latest',path.read_bytes())
        with patch('hy_backend.runtime',side_effect=AssertionError('No server needed')),contextlib.redirect_stdout(io.StringIO()):
            name=store.register(path,manual=True)
        self.assertEqual(store.records()[name]['title'],path.name)
        self.assertEqual(store.inventory()['installed'][0]['title'],path.name)

    def test_nested_files_and_same_basename_remain_distinct(self):
        paths=['gguf/HY/model.gguf','custom/deeper/model.GGUF','model.gguf']
        for relative in paths:
            path=store.model_root()/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(sample_gguf(relative))
        self.assertCountEqual(store.inventory()['pending'],paths)
        aliases=[]
        with patch('hy_backend.runtime',side_effect=AssertionError('No server needed for registered weights')),contextlib.redirect_stdout(io.StringIO()):
            for i,relative in enumerate(paths):
                path=store.model_root()/relative
                self.install('existing'+str(i)+':latest',path.read_bytes())
                aliases.append(store.register(path,manual=True))
        self.assertEqual(store.inventory()['pending'],[])
        store.remove(aliases[0])
        self.assertFalse((store.model_root()/paths[0]).exists())
        self.assertTrue((store.model_root()/paths[1]).exists())
        self.assertTrue((store.model_root()/paths[2]).exists())

    def test_duplicate_files_share_model_and_refresh_does_not_repeat_registration(self):
        payload=sample_gguf();self.install('existing:latest',payload)
        for relative in ('gguf/model.gguf','arbitrary/nested/copy.gguf'):
            path=store.model_root()/relative;path.parent.mkdir(parents=True);path.write_bytes(payload)
        with patch.object(store,'mutation_lock',side_effect=contextlib.nullcontext),patch('hy_backend.runtime',side_effect=AssertionError('No server needed')),contextlib.redirect_stdout(io.StringIO()):
            result=store.dispatch({'op':'refresh'})
            self.assertEqual(len(result['installed']),1);self.assertEqual(result['pending'],[])
            with patch.object(store,'register',side_effect=AssertionError('Must skip unchanged files')):
                store.dispatch({'op':'refresh'})
        self.assertEqual(len(store.records()['existing:latest']['local_files']),2)
        self.assertIsNone(store.selected())

    def test_old_top_level_registration_still_recognized(self):
        path=store.model_root()/'old.gguf';path.parent.mkdir();path.write_bytes(sample_gguf())
        self.install('old:latest',path.read_bytes())
        store.save(self.root/'data/model-library.json',{'old:latest':dict(local_file=path.name,local_size=path.stat().st_size,local_mtime=path.stat().st_mtime_ns)})
        self.assertEqual(store.inventory()['pending'],[])
        store.remove('old:latest');self.assertFalse(path.exists())

    def test_nested_junction_not_followed(self):
        import os
        import subprocess
        if os.name!='nt':self.skipTest('Windows junction check')
        outside=self.root/'outside';outside.mkdir();(outside/'hidden.gguf').write_bytes(sample_gguf())
        models=store.model_root();models.mkdir();link=models/'linked'
        result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(outside)],capture_output=True)
        if result.returncode:self.skipTest('Junction unavailable')
        try:self.assertEqual(store.inventory()['pending'],[])
        finally:link.rmdir()

    def test_download_resume_verification_and_no_auto_selection(self):
        payload=sample_gguf()+b'x'*100;sha=hashlib.sha256(payload).hexdigest();part=store.model_root()/'.downloads'/(sha+'.part');part.parent.mkdir(parents=True);part.write_bytes(payload[:50])
        detail={'revision':'a'*40,'files':[{'name':'tiny.gguf','sha':sha,'size':len(payload),'recommended':False}],'description':''}
        seen=[]
        def opened(req,**kwargs):
            seen.append(req.get_header('Range'));r=Response(payload[50:]);r.status=206;r.headers={'Content-Range':f'bytes 50-{len(payload)-1}/{len(payload)}'};return r
        with patch.object(store,'details',return_value=detail),patch.object(store.urllib.request,'urlopen',side_effect=opened),patch.object(store,'register',return_value='local:latest') as register,contextlib.redirect_stdout(io.StringIO()):
            store.download('test/model','tiny.gguf','a'*40)
        self.assertEqual(seen,['bytes=50-']);self.assertEqual(store.blob('sha256:'+sha).read_bytes(),payload)
        self.assertIsNone(store.selected());register.assert_called_once()

    def test_catalogue_matches_manual_model_by_hash_not_filename(self):
        payload=b'weights';self.install('manual-renamed:latest',payload)
        sha=hashlib.sha256(payload).hexdigest()
        data={'sha':'a'*40,'siblings':[
            {'rfilename':'Official-Q6_K.gguf','size':len(payload),'lfs':{'sha256':sha}},
            {'rfilename':'Official-Q4_K_M.gguf','size':len(payload),'lfs':{'sha256':'b'*64}}]}
        with patch.object(store,'fetch',side_effect=[json.dumps(data).encode(),b'description']):
            result=store.details('test/model')
        rows={f['name']:f for f in result['files']}
        self.assertEqual(rows['Official-Q6_K.gguf']['installedModel'],'manual-renamed:latest')
        self.assertIsNone(rows['Official-Q4_K_M.gguf']['installedModel'])
        self.assertIsNone(store.installed_match({'sha':sha,'size':len(payload)+1}))

    def test_duplicate_download_does_not_transfer_hash_register_or_select(self):
        payload=b'weights';self.install('manual:latest',payload)
        detail={'revision':'a'*40,'files':[{'name':'official.gguf','sha':hashlib.sha256(payload).hexdigest(),'size':len(payload)}]}
        with patch.object(store,'details',return_value=detail),patch.object(store.urllib.request,'urlopen') as transfer,patch.object(store,'hash_file') as hashing,patch.object(store,'register') as register,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(store.download('test/model','official.gguf','a'*40),'manual:latest')
        transfer.assert_not_called();hashing.assert_not_called();register.assert_not_called()
        self.assertIsNone(store.selected())

    def test_missing_registered_weights_do_not_count_as_installed(self):
        path,_=self.install('manual:latest');sha=path.name.removeprefix('sha256-');path.unlink()
        self.assertIsNone(store.installed_match({'sha':sha,'size':len(b'weights')}))

    def test_recommended_repo_includes_hashes_for_installed_badge(self):
        data={'siblings':[{'rfilename':store.RECOMMENDED_FILE,'size':7,'lfs':{'sha256':'a'*64}}]}
        with patch.object(store,'fetch',side_effect=[b'[]',json.dumps(data).encode()]):result=store.search()
        self.assertEqual(result[0]['files'][0]['sha'],'a'*64)

    def test_cancel_leaves_partial_download(self):
        payload=b'x'*3000000;sha=hashlib.sha256(payload).hexdigest()
        detail={'revision':'a'*40,'files':[{'name':'tiny.gguf','sha':sha,'size':len(payload),'recommended':False}],'description':''}
        with patch.object(store,'details',return_value=detail),patch.object(store.urllib.request,'urlopen',return_value=Response(payload)),patch.object(store,'cancelled',side_effect=[None,KeyboardInterrupt()]),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):store.download('test/model','tiny.gguf','a'*40)
        self.assertEqual((store.model_root()/'.downloads'/(sha+'.part')).stat().st_size,1024*1024)
        self.assertFalse(store.blob('sha256:'+sha).exists())

    def test_bad_hash_does_not_become_installed(self):
        detail={'revision':'a'*40,'files':[{'name':'tiny.gguf','sha':'0'*64,'size':3,'recommended':False}],'description':''}
        with patch.object(store,'details',return_value=detail),patch.object(store.urllib.request,'urlopen',return_value=Response(b'bad')),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError,'검증'):store.download('test/model','tiny.gguf','a'*40)
        self.assertFalse(store.blob('sha256:'+'0'*64).exists())

    def test_cli_preflight_before_project_access_and_offline_jobs(self):
        import translate_game
        with patch('translate_game.resolve_project',side_effect=AssertionError('Must not inspect games')) as resolve:
            with self.assertRaisesRegex(ValueError,'모델'):translate_game.main(['run','--source','DO-NOT-READ'])
            resolve.assert_not_called()
        self.assertFalse(store.needs_model(['font','layout','display','answers','language']))

    def test_model_switch_reuses_completed_translation(self):
        from automatic import resolve_project,settings
        from translation import cache
        project=self.root/'synthetic-project';config=project/'project.json'
        store.save(config,{'source':'X:/DO-NOT-READ','model':'old-model','language':'korean','automatic_version':1})
        store.save(project/'data/catalog.json',[{'id':'one','source':'Hello.','kind':'dialogue'}])
        line={'id':'one','source':'Hello.','text':'안녕.','model':'old-model','fingerprint':'previous'}
        path=project/'data/translations.jsonl';path.write_text(json.dumps(line)+'\n',encoding='utf-8');before=path.read_bytes()
        _,cfg=resolve_project(config=config,model='new-model')
        self.assertEqual(cfg['model'],'new-model')
        self.assertEqual(cache(project,settings(project,cfg))['one']['text'],'안녕.')
        self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':unittest.main()
