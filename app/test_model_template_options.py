"""Pure metadata/create-payload tests; no model files, registration or runtime."""
from copy import deepcopy
from contextlib import nullcontext,redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import model_store as store
from model_store import expected_renderer,gemma4_renderer,registration_template_options
from ollama_manifests import ManifestStore,MODEL_LAYER


class GemmaRendererTests(unittest.TestCase):
    def metadata(self,label=None,**more):
        data={'general.architecture':'gemma4',**more}
        if label is not None:data['general.size_label']=label
        return data

    def test_declared_large_and_small_variants(self):
        for label,expected in [('12B','gemma4-large'),('26B','gemma4-large'),('31B','gemma4-large'),
                               ('E2B','gemma4-small'),('E4B','gemma4-small'),(' 12 b ','gemma4-large')]:
            self.assertEqual(gemma4_renderer(self.metadata(label)),expected)

    def test_rounded_parameter_count_cannot_override_declared_size(self):
        self.assertEqual(gemma4_renderer(self.metadata('12B',**{'general.parameter_count':11900000000})),
                         'gemma4-large')
        self.assertIsNone(gemma4_renderer(self.metadata(**{'general.parameter_count':11900000000})))

    def test_unique_embedded_name_only_when_size_label_is_absent(self):
        self.assertEqual(gemma4_renderer(self.metadata(**{'general.name':'Gemma-4-12B-Instruct-Q4'})),
                         'gemma4-large')
        self.assertEqual(gemma4_renderer(self.metadata(**{'general.name':'gemma4-e4b-it'})),
                         'gemma4-small')
        self.assertEqual(gemma4_renderer(self.metadata('E4B',**{'general.name':'custom-12B'})),
                         'gemma4-small')
        for label,name in [('11.9B','gemma-4-12B'),('unknown','gemma-4-12B'),(None,'merge-12B-26B'),
                           (None,'Global_Step_560')]:
            self.assertIsNone(gemma4_renderer(self.metadata(label,**{'general.name':name})))

    def test_unrelated_families_and_unsupported_sizes_are_not_guessed(self):
        for family in ('llama','qwen3','gemma3','gemma4-moe',''):
            self.assertIsNone(gemma4_renderer({'general.architecture':family,'general.size_label':'12B'}))
        for label in ('4B','9B','27B','12B-A3B','12B/26B'):
            self.assertIsNone(gemma4_renderer(self.metadata(label)))

    def test_payload_keeps_alias_weights_and_stop_parameters(self):
        payload={'model':'rpt-local-digest:latest','files':{'renamed.gguf':'sha256:synthetic'},
                 'stream':False,'parameters':{'stop':['<turn|>'],'temperature':0.2}}
        before=deepcopy(payload)
        result=registration_template_options(payload,self.metadata('12B'))
        self.assertEqual(result,{**before,'renderer':'gemma4-large'})
        self.assertEqual(payload,before)

    def test_existing_registration_can_be_prepared_without_changing_alias(self):
        payload={'model':'rpt-local-digest:latest','from':'rpt-local-digest:latest','stream':False}
        result=registration_template_options(payload,self.metadata('12B'),existing={'parser':'gemma4'})
        self.assertEqual(result,{**payload,'renderer':'gemma4-large'})
        self.assertNotIn('parser',result)
        self.assertEqual(registration_template_options(payload,self.metadata('12B'),
                         existing={'renderer':'gemma4','parser':'gemma4'}),result)

    def test_explicit_custom_renderer_parser_and_template_are_preserved(self):
        base={'model':'same:latest','from':'same:latest','stream':False}
        for custom in ({'renderer':'custom-renderer'},{'renderer':'gemma4-small'},
                       {'parser':'custom-parser'},{'template':'custom template'}):
            payload={**base,**custom};before=deepcopy(payload)
            self.assertEqual(registration_template_options(payload,self.metadata('12B')),before)
            self.assertEqual(payload,before)
            self.assertEqual(registration_template_options(base,self.metadata('12B'),existing=custom),base)

    def test_no_options_added_when_metadata_is_uncertain(self):
        payload={'model':'rpt-local-digest:latest','from':'rpt-local-digest:latest'}
        self.assertEqual(registration_template_options(payload,self.metadata()),payload)
        self.assertEqual(registration_template_options(payload,{'general.architecture':'llama'}),payload)


QWEN35_TEMPLATE=("{% for message in messages %}{{ '<|im_start|>' + message['role'] + '\\n' + "
                 "message['content'] + '<|im_end|>\\n' }}{% endfor %}"
                 "{% if add_generation_prompt %}{{ '<|im_start|>assistant\\n' }}"
                 "{% if enable_thinking is defined and not enable_thinking %}"
                 "{{ '<think>\\n\\n</think>\\n\\n' }}{% endif %}{% endif %}")


class QwenRendererTests(unittest.TestCase):
    def metadata(self,family='qwen35',template=QWEN35_TEMPLATE):
        return {'general.architecture':family,'tokenizer.chat_template':template}

    def test_canonical_dense_and_moe_use_registered_renderer_and_missing_parser(self):
        for family in ('qwen35','qwen35moe'):
            metadata=self.metadata(family)
            self.assertEqual(expected_renderer(metadata),'qwen3.5')
            body={'model':'same:latest','from':'same:latest','parameters':{'stop':['<|im_end|>']}}
            before=deepcopy(body)
            self.assertEqual(registration_template_options(body,metadata),
                             {**body,'renderer':'qwen3.5','parser':'qwen3.5'})
            self.assertEqual(body,before)

    def test_existing_parser_and_custom_options_are_not_overwritten(self):
        metadata=self.metadata();body={'model':'same:latest','from':'same:latest'}
        self.assertEqual(registration_template_options(body,metadata,existing={'parser':'qwen3.5'}),
                         {**body,'renderer':'qwen3.5'})
        for custom in ({'renderer':'qwen3.5'},{'renderer':'my-renderer'},{'parser':'my-parser'},
                       {'parser':'gemma4'},{'template':'my template'}):
            self.assertEqual(registration_template_options(body,metadata,existing=custom),body)

    def test_unknown_or_noncanonical_templates_remain_native(self):
        for template in ('{{ messages }}',QWEN35_TEMPLATE.replace('<|im_end|>','END'),
                         QWEN35_TEMPLATE.replace('enable_thinking','other_flag').replace('<think>','').replace('</think>',''),
                         '<|im_start|><|im_end|><think></think>'):
            metadata=self.metadata(template=template)
            self.assertIsNone(expected_renderer(metadata))
            self.assertEqual(registration_template_options({'model':'same:latest'},metadata),{'model':'same:latest'})

    def test_qwen3_and_other_families_never_emit_nonexistent_qwen3_renderer(self):
        for family in ('qwen3','qwen3moe','gemma','gemma2','gemma3','gemma3n','llama'):
            metadata=self.metadata(family)
            self.assertIsNone(expected_renderer(metadata))
            self.assertEqual(registration_template_options({'model':'same:latest'},metadata),{'model':'same:latest'})


def synthetic_gguf(label='12B'):
    values={'general.architecture':'gemma4','general.size_label':label,
            'general.name':'Gemma 4 '+label,'tokenizer.chat_template':'{{ messages }}'}
    def string(value):
        encoded=value.encode();return struct.pack('<Q',len(encoded))+encoded
    return b'GGUF'+struct.pack('<IQQ',3,1,len(values))+b''.join(
        string(key)+struct.pack('<I',8)+string(value) for key,value in values.items())+b'synthetic tensor bytes'


class RegisteredTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.ollama=self.root/'models/ollama'
        patched=patch.object(store,'ROOT',self.root);patched.start();self.addCleanup(patched.stop)
        patched=patch('desktop_bridge.preferences',return_value={});patched.start();self.addCleanup(patched.stop)
        self.name='rpt-local-synthetic:latest'

    def blob(self,payload):
        if isinstance(payload,dict):payload=json.dumps(payload).encode()
        digest='sha256:'+hashlib.sha256(payload).hexdigest()
        target=self.ollama/'blobs'/digest.replace(':','-');target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(payload)
        return {'digest':digest,'size':len(payload)}

    def install(self,renderer='gemma4',parser='gemma4',label='12B',template=False):
        config={'model_family':'gemma4','renderer':renderer,'parser':parser}
        self.document={'config':self.blob(config),'layers':[
            {**self.blob(synthetic_gguf(label)),'mediaType':MODEL_LAYER},
            {**self.blob({'stop':['<turn|>'],'temperature':0.2}),'mediaType':'application/vnd.ollama.image.params'}]}
        if template:self.document['layers'].append({**self.blob(b'explicit template'),'mediaType':'application/vnd.ollama.image.template'})
        self.path=self.ollama/'manifests/registry.ollama.ai/library'/self.name.replace(':','/')
        store.save(self.path,self.document)

    def test_both_bundled_api_versions_support_renderer_override(self):
        for version in ('0.34.2','v0.34.2','ollama-0.34.2','0.40.15','0.40.16'):
            self.assertTrue(store.renderer_api_supported(version))
        for version in ('0.34.1','0.20.0','test','',None):
            self.assertFalse(store.renderer_api_supported(version))

    def test_existing_alias_update_changes_only_renderer_and_skips_next_create(self):
        for version in ('0.34.2','0.40.15'):
            with self.subTest(version=version):
                self.install();before_layers=deepcopy(self.document['layers'])
                weight=store.blob(before_layers[0]['digest']);before_weights=weight.read_bytes()
                seen=[]
                def create(req,**kwargs):
                    self.assertTrue(req.full_url.endswith('/api/create'))
                    body=json.loads(req.data);seen.append(body)
                    self.assertEqual(body,{'model':self.name,'from':self.name,'stream':False,'renderer':'gemma4-large'})
                    config=json.loads(store.blob(self.document['config']['digest']).read_text(encoding='utf-8'))
                    config['renderer']=body['renderer'];self.document['config']=self.blob(config)
                    store.save(self.path,self.document)
                    return io.BytesIO(b'{"status":"success"}')
                opener=type('Opener',(),{'open':staticmethod(create)})()
                with patch.object(store.urllib.request,'build_opener',return_value=opener),\
                     patch.object(store,'hash_file',side_effect=AssertionError('No weight hash')),\
                     patch.object(store,'choose',side_effect=AssertionError('No selection change')),\
                     patch.object(store,'download',side_effect=AssertionError('No download')),\
                     patch.object(store,'gguf',wraps=store.gguf) as metadata:
                    result=store.ensure_registered_template('http://127.0.0.1:1',self.name,runtime_version=version)
                    second=store.ensure_registered_template('http://127.0.0.1:1',self.name,runtime_version=version)
                self.assertTrue(result['updated']);self.assertTrue(result['alias_preserved'])
                self.assertFalse(second['updated']);self.assertEqual(len(seen),1);metadata.assert_called_once()
                self.assertEqual(self.document['layers'],before_layers)
                self.assertEqual(weight.read_bytes(),before_weights)

    def test_explicit_custom_settings_skip_header_reads_and_api(self):
        for options in ({'renderer':'gemma4-large'},{'renderer':'gemma4-small'},
                        {'renderer':'my-renderer'},{'parser':'my-parser'},{'template':True}):
            with self.subTest(options=options):
                self.install(**options);before=self.path.read_bytes()
                with patch.object(store,'gguf',side_effect=AssertionError('No header needed')),\
                     patch.object(store,'_create_registered_model',side_effect=AssertionError('No create')):
                    result=store.ensure_registered_template('http://127.0.0.1:1',self.name,runtime_version='0.40.15')
                self.assertFalse(result['updated']);self.assertEqual(self.path.read_bytes(),before)

    def test_unknown_version_or_uncertain_size_does_not_guess(self):
        with patch.object(store,'manifest_store',side_effect=AssertionError('No store for unsupported runtime')):
            result=store.ensure_registered_template('http://127.0.0.1:1',self.name,runtime_version='0.20.0')
        self.assertEqual(result['status'],'unsupported_runtime')
        self.install(label='11.9B')
        with patch.object(store,'_create_registered_model',side_effect=AssertionError('No create for uncertain size')):
            result=store.ensure_registered_template('http://127.0.0.1:1',self.name,runtime_version='0.40.15')
        self.assertEqual(result['status'],'uncertain')

    def test_canonical_qwen35_same_alias_update_adds_only_standard_pair(self):
        for family in ('qwen35','qwen35moe'):
            with self.subTest(family=family):
                self.install(renderer='',parser='')
                config={'model_family':family,'renderer':'','parser':''}
                self.document['config']=self.blob(config);store.save(self.path,self.document)
                before_layers=deepcopy(self.document['layers']);seen=[]
                def create(endpoint,body):
                    seen.append(body)
                    self.assertEqual(body,{'model':self.name,'from':self.name,'stream':False,
                                           'renderer':'qwen3.5','parser':'qwen3.5'})
                    self.document['config']=self.blob({**config,'renderer':body['renderer'],'parser':body['parser']})
                    store.save(self.path,self.document)
                    return {'status':'success'}
                metadata={'general.architecture':family,'tokenizer.chat_template':QWEN35_TEMPLATE}
                with patch.object(store,'gguf',return_value=metadata) as header,\
                     patch.object(store,'_create_registered_model',side_effect=create):
                    result=store.ensure_registered_template('http://127.0.0.1:1',self.name,runtime_version='0.40.15')
                    second=store.ensure_registered_template('http://127.0.0.1:1',self.name,runtime_version='0.40.15')
                self.assertTrue(result['updated']);self.assertFalse(second['updated'])
                self.assertEqual(len(seen),1);header.assert_called_once()
                self.assertEqual(self.document['layers'],before_layers)

    def test_new_registration_posts_explicit_size_renderer(self):
        for version in ('0.34.2','0.40.15'):
            with self.subTest(version=version):
                # Separate hashes make each subcase a genuinely new registration.
                path=self.root/'models'/('gemma-'+version+'.gguf');path.parent.mkdir(exist_ok=True)
                path.write_bytes(synthetic_gguf()+version.encode());seen=[]
                def create(req,**kwargs):
                    body=json.loads(req.data);seen.append(body)
                    self.assertEqual(body['renderer'],'gemma4-large')
                    self.assertNotIn('parser',body);self.assertNotIn('parameters',body)
                    self.name=body['model'];digest=next(iter(body['files'].values()))
                    self.document={'config':self.blob({'model_family':'gemma4','renderer':body['renderer'],'parser':'gemma4'}),
                                   'layers':[{'mediaType':MODEL_LAYER,'digest':digest,'size':path.stat().st_size}]}
                    self.path=self.ollama/'manifests/registry.ollama.ai/library'/self.name.replace(':','/')
                    store.save(self.path,self.document)
                    return io.BytesIO(b'{"status":"success"}')
                opener=type('Opener',(),{'open':staticmethod(create)})()
                with patch('hy_backend.runtime',return_value=nullcontext({'endpoint':'http://127.0.0.1:1'})),\
                     patch.object(store.urllib.request,'build_opener',return_value=opener),redirect_stdout(io.StringIO()):
                    name=store.register(path,manual=True,runtime_id=version)
                self.assertEqual(name,self.name);self.assertEqual(len(seen),1);self.assertIsNone(store.selected())

if __name__=='__main__':unittest.main()
