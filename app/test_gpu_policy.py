"""Synthetic hardware/model fixtures only; no inference or game reads."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gpu_policy as policy
from diagnostics import runtime_settings
from source_context import SourceContext

Q6='88ef0aba59952a4cfe4be36cb5baf797dbb370bc60e9dcbd7297036021e52831'
Q4='9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b'
CFG=dict(model='original:q6',num_ctx=16384,parallel=2,batch_size=8)


def device(total,used=0,id='GPU-example'):
    return dict(id=id,name='Synthetic GPU',total=total,used=used,utilization=99)


def asset(digest=Q6):
    return dict(digest=digest,bytes=6164482720 if digest==Q6 else 4624648896,
         metadata={'general.architecture':'test','test.block_count':32,'test.embedding_length':4096,
                   'test.attention.head_count':32,'test.attention.head_count_kv':8})


class AmdPolicyTests(unittest.TestCase):
    def amd(self, total=16384):
        return dict(id='DXGI-0000000000000001', name='AMD', vendor='amd', total=total)

    def test_amd_profiles_are_estimates_and_never_use_nvidia_calibration(self):
        for size, expected, parallel in [(6144,'6gb',2),(8192,'8gb',2),(12288,'12gb',2),
                                        (16384,'16gb',3),(24576,'24gb',4),(32768,'32gb',6)]:
            with redirect_stdout(io.StringIO()), patch.object(policy,'model_asset',side_effect=AssertionError('No NVIDIA calibration')):
                result=policy.configure(CFG,devices=[self.amd(size)],preferences={'gpu_mode':'auto'},environment={})
            self.assertEqual(result['_hardware_policy']['profile'],expected)
            self.assertEqual(result['_hardware_policy']['support'],'estimated_not_measured')
            self.assertEqual(result['parallel'],parallel)
            self.assertNotIn('_hardware_cuda_device',result)

    def test_amd_igpu_masks_and_mixed_gpu(self):
        amd=self.amd(); igpu=dict(id='DXGI-0000000000000002',vendor='other',total=512,name='iGPU')
        data=policy.rules(); pref={'gpu_mode':'auto'}
        self.assertEqual(policy.capacity_profile([amd,igpu],pref,{},data)[1]['vendor'],'amd')
        for mask in ('GGML_VK_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES','OLLAMA_VULKAN'):
            self.assertEqual(policy.capacity_profile([amd],pref,{mask:'0'},data)[1]['status'],'unknown')
        nvidia=device(12288)
        self.assertNotEqual(policy.capacity_profile([amd,nvidia],pref,{},data)[1].get('vendor'),'amd')
        self.assertEqual(policy.capacity_profile([amd,self.amd(8192)],pref,{},data)[1]['profile'],'16gb')

    def test_preview_reports_only_verified_planned_amd_backend(self):
        for backend in ('vulkan', 'rocm'):
            gpu=dict(self.amd(),selectable=True,backend=backend,**{backend+'_index':0})
            preview=policy.inference_preview([gpu],{'gpu_mode':'auto'},environment={})
            self.assertEqual(preview['backend'],backend)
            self.assertEqual(preview['devices'][0]['backend'],backend)
        unverified=dict(self.amd(),selectable=True,backend='vulkan')
        self.assertEqual(policy.inference_preview([unverified],{'gpu_mode':'auto'},environment={})['backend'],'unverified')

    def test_amd_small_adapter_does_not_raise_hardware_error(self):
        with redirect_stdout(io.StringIO()):
            result=policy.configure(CFG,devices=[self.amd(4096)],preferences={'gpu_mode':'auto'},environment={})
        self.assertNotIn('_hardware_error',result)

    def test_enabled_vulkan_environment_keeps_amd_capacity_detection(self):
        pref={'gpu_mode':'auto'}; data=policy.rules()
        for value in ('1','true','TRUE'):
            profile,audit=policy.capacity_profile([self.amd()],pref,{'OLLAMA_VULKAN':value},data)
            self.assertEqual(profile['id'],'16gb')
            self.assertEqual(audit['backend'],'unverified')
        for value in ('0','false',''):
            self.assertEqual(policy.capacity_profile([self.amd()],pref,{'OLLAMA_VULKAN':value},data)[1]['status'],'unknown')
        self.assertEqual(policy.capacity_profile([device(12288)],pref,{'OLLAMA_VULKAN':'0'},data)[1]['status'],'preserved')

    def test_mixed_auto_12gb_pins_nvidia_but_nvidia_only_is_unchanged(self):
        pref={'gpu_mode':'auto'}; gpu=device(12288,id='GPU-0001')
        with redirect_stdout(io.StringIO()):
            mixed=policy.configure(CFG,devices=[dict(gpu,mixed_amd=True)],preferences=pref,environment={})
            single=policy.configure(CFG,devices=[gpu],preferences=pref,environment={})
        self.assertEqual(mixed['_hardware_cuda_device'],'GPU-0001')
        self.assertTrue(mixed['_hardware_policy']['mixed_amd'])
        self.assertNotIn('_hardware_cuda_device',single)
        self.assertEqual((mixed['num_ctx'],mixed['parallel'],mixed['kv_cache_type']),(16384,2,'q8_0'))

    def test_custom_amd_warning_does_not_claim_testing(self):
        pref={'gpu_mode':'auto','inference_overrides':{'parallel':1}}
        amd=policy.inference_preview([self.amd(8192)],pref,environment={})
        nvidia=policy.inference_preview([device(8192)],pref,environment={})
        self.assertIn('추정 프리셋',amd['warnings'][0])
        self.assertIn('시험한 설정',nvidia['warnings'][0])


class PolicyTests(unittest.TestCase):
    def configure(self,total,cfg=CFG,used=0,**kw):
        with redirect_stdout(io.StringIO()):
            return policy.configure(cfg,devices=[device(total,used)],preferences={'gpu_mode':'auto'},environment={},**kw)

    def test_12gb_preserves_all_existing_settings_regardless_of_usage(self):
        for total in (12282,12288,14336):
            for used in (0,total-1):
                with patch.object(policy,'model_asset',side_effect=AssertionError('No model read on preserved path')):
                    result=self.configure(total,used=used)
                self.assertEqual({k:result[k] for k in CFG},CFG)
                self.assertTrue(result['context_txt_enabled'])
                self.assertEqual(result['kv_cache_type'],'q8_0')
                self.assertEqual(result['_hardware_policy']['status'],'preserved')

    def test_6_and_8_use_same_family_local_q4_without_using_free_vram(self):
        for total,ctx in ((6142,4096),(8190,8192)):
            outcomes=[]
            for used in (0,total-1):
                with patch.object(policy,'model_asset',return_value=asset()),\
                     patch.object(policy,'matching_local',return_value=dict(model='local:q4',registered=True)) as find:
                    result=self.configure(total,used=used)
                self.assertEqual(result['model'],'local:q4');self.assertEqual(result['num_ctx'],ctx)
                self.assertEqual(result['parallel'],2);self.assertEqual(result['kv_cache_type'],'q8_0')
                self.assertFalse(result['context_txt_enabled'])
                self.assertEqual(find.call_args.args[0],Q4)
                self.assertFalse(result['_hardware_policy']['used_or_free_vram_consulted'])
                outcomes.append(result)
            self.assertEqual(outcomes[0],outcomes[1])
        self.assertEqual(CFG,dict(model='original:q6',num_ctx=16384,parallel=2,batch_size=8))

    def test_existing_q4_kept_and_serial_name_work_is_not_made_parallel(self):
        with patch.object(policy,'model_asset',return_value=asset(Q4)),patch.object(policy,'matching_local') as find:
            result=self.configure(6142,dict(CFG,model='local:q4',parallel=1))
        self.assertEqual(result['model'],'local:q4');self.assertEqual(result['parallel'],1)
        find.assert_not_called()

    def test_missing_quantization_is_actionable_and_never_downloaded(self):
        with patch.object(policy,'model_asset',return_value=asset()),patch.object(policy,'matching_local',return_value=None),\
             patch('model_store.download',side_effect=AssertionError('No download')):
            result=self.configure(6142)
            with self.assertRaisesRegex(ValueError,'Q4_K_M'):policy.prepare_model(result)

    def test_raw_file_registration_is_lazy_and_does_not_change_model_selection(self):
        raw=dict(path='models/example.gguf',digest=Q4,registered=False)
        with patch.object(policy,'model_asset',return_value=asset()),patch.object(policy,'matching_local',return_value=raw),\
             patch('model_store.register',return_value='local:q4') as register,patch('model_store.choose') as choose:
            result=self.configure(6142);register.assert_not_called()
            active=policy.prepare_model(result)
        self.assertEqual(active['model'],'local:q4');register.assert_called_once();choose.assert_not_called()
        self.assertEqual(register.call_args.args[1]['repo'],'tencent/Hy-MT2-7B-GGUF')
        self.assertNotIn('_hardware_pending_model',active)

    def test_gpu_masks_and_explicit_selection_use_only_eligible_devices(self):
        devices=[device(8190,id='GPU-small'),device(16380,id='GPU-large')]
        data=policy.rules()
        p,a=policy.capacity_profile(devices,{'gpu_mode':'auto'},{},data)
        self.assertEqual(p['id'],'16gb');self.assertEqual(a['total_mib'],16380)
        p,a=policy.capacity_profile(devices,{'gpu_mode':'auto'},{'CUDA_VISIBLE_DEVICES':'0'},data)
        self.assertEqual(p['id'],'8gb');self.assertEqual(a['total_mib'],8190)
        p,a=policy.capacity_profile(devices,{'gpu_mode':'selected','gpu_ids':['GPU-small']},{},data)
        self.assertEqual(p['id'],'8gb')
        p,a=policy.capacity_profile(devices,{'gpu_mode':'auto'},{'CUDA_VISIBLE_DEVICES':''},data)
        self.assertIsNone(p);self.assertEqual(a['status'],'unknown')

    def test_frozen_job_does_not_redetect_gpu_after_start_or_restart(self):
        with patch.object(policy,'model_asset',return_value=asset(Q4)):result=self.configure(6142)
        with patch('desktop_bridge.nvidia_devices',side_effect=AssertionError('No redetection')):
            self.assertEqual(policy.configure(result),result)

    def test_large_gpu_tiers_only_change_workers_and_ignore_current_usage(self):
        for total,workers in [(16380,3),(20480,3),(24570,4),(30720,4),(32760,6),(49152,6)]:
            outcomes=[]
            for used in (0,total-1):
                with patch.object(policy,'model_asset',side_effect=AssertionError('No model changes or reads')):
                    result=self.configure(total,used=used)
                self.assertEqual(result['parallel'],workers)
                self.assertEqual(result['num_ctx'],CFG['num_ctx']);self.assertEqual(result['model'],CFG['model'])
                self.assertTrue(result['context_txt_enabled']);self.assertEqual(result['kv_cache_type'],'q8_0')
                self.assertEqual(result['_hardware_policy']['status'],'estimated')
                outcomes.append(result)
            self.assertEqual(outcomes[0],outcomes[1])

    def test_presets_and_overrides_are_not_clamped_back_and_have_directional_warnings(self):
        large=[device(24570)]
        for overrides,warn in [({'parallel':2},True),({'parallel':6},False),({'num_ctx':32768},False),({'num_ctx':8192},True)]:
            prefs={'inference_overrides':overrides}
            preview=policy.inference_preview(large,prefs,{})
            self.assertEqual(bool(preview['warnings']),warn)
            with redirect_stdout(io.StringIO()):result=policy.configure(CFG,devices=large,preferences=prefs,environment={})
            for k,v in overrides.items():self.assertEqual(result[k],v)
            self.assertEqual(result['_hardware_policy']['settings_warnings'],preview['warnings'])
        for total in (6142,8190,12282):
            for worker in (1,3):
                preview=policy.inference_preview([device(total)],{'inference_overrides':{'parallel':worker}},{})
                self.assertTrue(preview['warnings'])
        with patch.object(policy,'model_asset',return_value=asset(Q4)),redirect_stdout(io.StringIO()):
            result=policy.configure(CFG,devices=[device(6142)],preferences={'inference_overrides':{'num_ctx':8192,'parallel':3,'context_txt_enabled':True}},environment={})
        self.assertEqual(result['num_ctx'],8192);self.assertEqual(result['parallel'],3);self.assertTrue(result['context_txt_enabled'])
        self.assertEqual(result['_hardware_policy']['memory_basis'],'metadata_estimate_not_benchmarked')

    def test_manual_preset_reset_unknown_hardware_and_serial_helpers(self):
        with redirect_stdout(io.StringIO()):
            serial=policy.configure(dict(CFG,parallel=1,_serial_model_request=True),devices=[device(24570)],preferences={'inference_overrides':{'parallel':6}},environment={})
            customized=policy.configure(dict(CFG,parallel=1),devices=[device(24570)],preferences={'inference_overrides':{'parallel':6}},environment={})
            manual=policy.configure(CFG,devices=[device(24570)],preferences={'inference_preset':'12gb'},environment={})
            unknown=policy.configure(CFG,devices=[],preferences={'inference_overrides':{'parallel':3}},environment={})
            reset=policy.configure(CFG,devices=[device(24570)],preferences={'inference_preset':'auto','inference_overrides':{}},environment={})
        self.assertEqual(serial['parallel'],1);self.assertEqual(manual['parallel'],2)
        self.assertEqual(customized['parallel'],6)
        self.assertEqual(unknown['parallel'],3);self.assertEqual(unknown['_hardware_policy']['status'],'unknown')
        self.assertEqual(reset['parallel'],4)

    def test_custom_kv_precision_changes_memory_estimate(self):
        sizes=[policy.estimated_memory(asset(),16384,2,policy.rules(),kind) for kind in ('q4_0','q8_0','f16')]
        self.assertLess(sizes[0],sizes[1]);self.assertLess(sizes[1],sizes[2])

    def test_more_than_two_actual_workers_commit_once_and_resume_cached_rows(self):
        import threading
        from automatic import settings
        from engine import save_json
        from translation import translate,fingerprint
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            rows=[dict(id=str(i),source='Sentence %d.'%i,kind='dialogue',file='story.rpy',block='intro') for i in range(4)]
            save_json(project/'data/catalog.json',rows)
            cfg=settings(project,dict(CFG,model='synthetic',parallel=4,batch_size=1))
            barrier=threading.Barrier(4,timeout=5);threads=set();lock=threading.Lock()
            def fake(batch,ctx,active,retry_note=''):
                with lock:threads.add(threading.get_ident())
                barrier.wait()
                return [dict(id=r['id'],source=r['source'],text='번역 문장.',model=active['model'],fingerprint=fingerprint(active)) for r in batch],{'seconds':0}
            with patch('runtime_recovery.run',side_effect=lambda project,cfg,operation,saved:operation(cfg)),patch('translation.translate_batch',side_effect=fake):
                translate(project,cfg)
            self.assertEqual(len(threads),4)
            path=project/'data/translations.jsonl';saved=path.read_bytes()
            values=[json.loads(line) for line in saved.splitlines()]
            self.assertEqual(sorted(v['id'] for v in values),['0','1','2','3'])
            with patch('runtime_recovery.run',side_effect=AssertionError('No server')),patch('translation.translate_batch',side_effect=AssertionError('No retranslation')):
                translate(project,dict(cfg,parallel=6))
            self.assertEqual(path.read_bytes(),saved)

    def test_unknown_model_is_estimated_from_its_metadata_and_never_replaced_with_hy(self):
        other=asset('other');other['bytes']=1024**3
        with patch.object(policy,'model_asset',return_value=other),patch.object(policy,'matching_local') as find:
            result=self.configure(8190,dict(CFG,model='other:instruct'))
        find.assert_not_called();self.assertEqual(result['model'],'other:instruct')
        self.assertEqual(result['_hardware_policy']['memory_basis'],'metadata_estimate_not_benchmarked')
        other['metadata']={}
        with patch.object(policy,'model_asset',return_value=other):result=self.configure(8190,dict(CFG,model='other:instruct'))
        self.assertIn('_hardware_error',result)

    def test_no_gpu_or_unreadable_detection_does_not_guess_capacity(self):
        with redirect_stdout(io.StringIO()):
            result=policy.configure(CFG,devices=[],preferences={},environment={})
        self.assertEqual(result['_hardware_policy']['status'],'unknown')
        self.assertTrue(result['context_txt_enabled'])

    def test_saved_auto_preferences_replace_legacy_generic_defaults(self):
        cfg=dict(model='generic:latest',num_ctx=8192)
        prefs=dict(inference_preset='auto',inference_overrides={})
        preview=policy.inference_preview([device(12282)],prefs,{})
        with redirect_stdout(io.StringIO()),patch.object(policy,'model_asset',side_effect=AssertionError('No model load')):
            result=policy.configure(cfg,devices=[device(12282)],preferences=prefs,environment={})
        self.assertEqual({key:result[key] for key in policy.FIELDS},preview['effective'])
        self.assertEqual(result['num_ctx'],16384);self.assertEqual(result['parallel'],2)
        self.assertEqual(result['_hardware_policy']['effective_settings'],preview['effective'])
        self.assertEqual(cfg,dict(model='generic:latest',num_ctx=8192))

    def test_saved_generic_baseline_change_does_not_retranslate_completed_cache(self):
        from automatic import settings
        from engine import save_json
        from translation import translate,fingerprint
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            row=dict(id='cached',source='Hello.',kind='dialogue',file='story.rpy',block='intro')
            save_json(project/'data/catalog.json',[row])
            previous=settings(project,dict(model='generic',language='korean',num_ctx=8192))
            line=dict(id='cached',source=row['source'],text='안녕.',model='generic',fingerprint=fingerprint(previous))
            path=project/'data/translations.jsonl';path.write_text(json.dumps(line)+'\n',encoding='utf-8')
            before=path.read_bytes()
            with redirect_stdout(io.StringIO()):
                active=policy.configure(previous,devices=[device(12282)],
                    preferences={'inference_preset':'auto','inference_overrides':{}},environment={})
            self.assertNotEqual(fingerprint(previous),fingerprint(active))
            with patch('runtime_recovery.run',side_effect=AssertionError('No model server for cached work')),\
                 patch('translation.translate_batch',side_effect=AssertionError('No retranslation')),redirect_stdout(io.StringIO()):
                translate(project,active)
            self.assertEqual(path.read_bytes(),before)

    def test_saved_overrides_and_direct_explicit_values_are_respected(self):
        with redirect_stdout(io.StringIO()):
            saved=policy.configure(dict(model='generic',num_ctx=8192),devices=[device(12282)],
                 preferences={'inference_preset':'auto','inference_overrides':{'num_ctx':32768,'parallel':3}},environment={})
            direct=self.configure(12282,dict(model='direct',num_ctx=6144,parallel=1))
        self.assertEqual((saved['num_ctx'],saved['parallel']),(32768,3))
        self.assertEqual((direct['num_ctx'],direct['parallel']),(6144,1))
        for result in (saved,direct):
            self.assertEqual(result['_hardware_policy']['effective_settings'],{k:result[k] for k in policy.FIELDS})

    def test_missing_worker_is_resolved_in_config_and_audit(self):
        result=self.configure(12282,dict(model='generic',num_ctx=8192))
        self.assertEqual(result['parallel'],2)
        self.assertEqual(result['_hardware_policy']['effective_settings']['parallel'],result['parallel'])

    def test_saved_large_and_small_presets_apply_to_generic_models(self):
        prefs=dict(inference_preset='auto',inference_overrides={})
        small=asset('generic');small['bytes']=1024**3
        for total,profile in ((6142,'6gb'),(8190,'8gb'),(16380,'16gb'),(24570,'24gb'),(32760,'32gb')):
            with redirect_stdout(io.StringIO()),patch.object(policy,'model_asset',return_value=small):
                result=policy.configure(dict(model='generic',num_ctx=8192),devices=[device(total)],preferences=prefs,environment={})
            preview=policy.inference_preview([device(total)],prefs,{})
            self.assertEqual(result['_hardware_policy']['profile'],profile)
            self.assertEqual({k:result[k] for k in policy.FIELDS},preview['effective'])

    def test_detection_failure_still_uses_saved_baseline_without_gpu_work(self):
        prefs=dict(inference_preset='auto',inference_overrides={})
        with redirect_stdout(io.StringIO()),patch('desktop_bridge.policy_devices',side_effect=OSError('synthetic')):
            result=policy.configure(dict(model='generic',num_ctx=8192),preferences=prefs,environment={})
        self.assertEqual((result['num_ctx'],result['parallel']),(16384,2))
        self.assertEqual(result['_hardware_policy']['status'],'unknown')

    def test_txt_disabled_without_opening_notes_and_whole_nearby_dialogue_remains(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);(project/'data').mkdir()
            rows=[dict(id=str(i),source=s,kind='dialogue',file='story.rpy',block='intro',speaker='a')
                  for i,s in enumerate(['Before.','I love you baby.','After.'])]
            cfg=dict(CFG,context_txt_enabled=False,_hardware_policy={'profile':'6gb'})
            with patch('context_notes.ContextNotes._read',side_effect=AssertionError('Do not open TXT')):
                index=SourceContext(rows,project,cfg);ctx=index.context(rows[1:2],cfg)
            self.assertNotIn('translation_guidance',ctx)
            self.assertEqual([p['text'] for p in ctx['source_passage'] if 'text' in p],['Before.','After.'])
            report=json.loads((project/'data/context-report.json').read_text(encoding='utf-8'))
            self.assertFalse(report['enabled']);self.assertEqual(report['loaded_files'],[])

    def test_final_guard_strips_txt_only_and_logs_policy(self):
        ctx={'translation_guidance':{'global':'notes'},'current_state':'state',
             'source_passage':[{'text':'I love you baby.'}], 'variable_identities':{'x':'name'}}
        cfg=dict(CFG,context_txt_enabled=False,_hardware_policy={'profile':'6gb'})
        selected=policy.translation_context(ctx,cfg)
        self.assertNotIn('translation_guidance',selected);self.assertNotIn('current_state',selected)
        self.assertEqual(selected['source_passage'],ctx['source_passage'])
        self.assertEqual(selected['variable_identities'],ctx['variable_identities'])
        self.assertIn('translation_guidance',ctx)
        self.assertEqual(runtime_settings(cfg)['context_txt_policy'],'disabled_by_gpu_profile')

    def test_low_profile_reuses_old_model_cache_and_keeps_cached_neighbor_whole(self):
        from automatic import settings
        from engine import save_json
        from translation import translate,fingerprint
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            rows=[dict(id=str(i),source=s,kind='dialogue',file='story.rpy',block='intro',speaker='a')
                  for i,s in enumerate(['Before.','I love you baby.','After.'])]
            save_json(project/'data/catalog.json',rows)
            cached=dict(id='0',source=rows[0]['source'],text='앞 문장.',model='original:q6',fingerprint='old')
            path=project/'data/translations.jsonl'
            path.write_text(json.dumps(cached)+'\n',encoding='utf-8')
            with patch.object(policy,'model_asset',return_value=asset(Q4)):
                cfg=self.configure(6142,dict(CFG,model='local:q4',batch_size=1,_reuse_previous_models=True))
            cfg=settings(project,cfg);calls=[]
            def fake(batch,ctx,active,retry_note=''):
                calls.append((batch,ctx,active))
                self.assertEqual(active['num_ctx'],4096);self.assertFalse(active['context_txt_enabled'])
                self.assertNotIn('translation_guidance',ctx)
                return [dict(id=r['id'],source=r['source'],text='사랑해, 자기야.' if r['id']=='1' else '뒤 문장.',
                             model=active['model'],fingerprint=fingerprint(active)) for r in batch],{'seconds':0}
            with patch('runtime_recovery.run',side_effect=lambda project,cfg,operation,saved:operation(cfg)),\
                 patch('context_notes.ContextNotes._read',side_effect=AssertionError('No TXT reads')),\
                 patch('translation.translate_batch',side_effect=fake):
                translate(project,cfg)
            self.assertEqual(len(calls),2)
            context=next(ctx for batch,ctx,active in calls if batch[0]['id']=='1')
            self.assertIn('Before.',[p.get('text') for p in context['source_passage']])
            saved=path.read_bytes()
            with patch('translation.translate_batch',side_effect=AssertionError('No retranslation')),\
                 patch('runtime_recovery.run',side_effect=AssertionError('No server on completed resume')):
                translate(project,cfg)
            self.assertEqual(path.read_bytes(),saved)


if __name__=='__main__':unittest.main()
