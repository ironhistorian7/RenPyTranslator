"""Synthetic registration matrix, not a real-model/runtime compatibility claim.

Only metadata and request dictionaries are used. Requantizer/renamed filenames
must not override the family or declared size embedded in a GGUF header.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import model_store as store


FAMILIES = (
    ('qwen3','8B',None), ('qwen3moe','30B-A3B',None),
    ('qwen35','9B',None), ('qwen35moe','35B-A3B',None),
    ('gemma','7B',None), ('gemma3','12B',None),
    ('gemma4','E4B','gemma4-small'), ('gemma4','12B','gemma4-large'),
)
FILENAMES = (
    'official/Instruct-Q4_K_M.gguf',
    'unsloth/Instruct-UD-Q4_K_XL.gguf',
    'bartowski/Instruct-IQ4_XS.gguf',
    'mradermacher/Instruct.i1-IQ2_XS.gguf',
    'arbitrary-folder/completely-renamed.gguf',
)


def metadata(family,size):
    return {'general.architecture':family,'general.size_label':size,
            'general.name':'Synthetic Instruct','tokenizer.chat_template':'{{ messages }}'}


class ModelFamilyCompatibilityTests(unittest.TestCase):
    def test_official_requantized_and_renamed_sources_follow_header_family(self):
        for family,size,expected in FAMILIES:
            for filename in FILENAMES:
                with self.subTest(family=family,size=size,filename=filename):
                    data=metadata(family,size)
                    payload={'model':'rpt-local-arbitrary:latest','files':{filename:'sha256:'+'a'*64},
                             'stream':False,'parameters':{'temperature':0.2,'stop':['synthetic-stop']}}
                    before=deepcopy(payload)
                    result=store.registration_template_options(payload,data)
                    self.assertEqual(store.gemma4_renderer(data),expected)
                    self.assertEqual(result,{**before,**({'renderer':expected} if expected else {})})
                    self.assertEqual(payload,before)

    def test_alias_filename_and_rounded_parameter_count_do_not_choose_variant(self):
        for alias,filename in (
            ('gemma4-31b:latest','gemma4-large-31B.gguf'),
            ('gemma4-e2b:latest','gemma4-small-E2B.gguf'),
            ('Gemini-12B:latest','Gemini-official-looking-12B.gguf')):
            with self.subTest(alias=alias):
                payload={'model':alias,'files':{filename:'sha256:'+'b'*64}}
                uncertain={'general.architecture':'gemma4','general.name':'Renamed Instruct',
                           'general.parameter_count':11900000000}
                self.assertEqual(store.registration_template_options(payload,uncertain),payload)
                # An explicit size remains authoritative even when the public
                # alias and source filename suggest a different model variant.
                resolved=store.registration_template_options(payload,metadata('gemma4','E4B'))
                self.assertEqual(resolved['renderer'],'gemma4-small')
                self.assertEqual(resolved['model'],alias)
                self.assertEqual(resolved['files'],payload['files'])

    def test_gemma_names_cannot_apply_gemma4_options_to_other_architectures(self):
        for family in ('qwen3','qwen3moe','qwen35','qwen35moe','gemma','gemma3','llama','gemini','unknown'):
            with self.subTest(family=family):
                data={**metadata(family,'12B'),'general.name':'Gemma4-12B-Instruct'}
                payload={'model':'gemma4-12b:latest','files':{'gemma4-12B.gguf':'sha256:'+'c'*64}}
                self.assertIsNone(store.gemma4_renderer(data))
                self.assertEqual(store.registration_template_options(payload,data),payload)

    def test_explicit_templates_renderers_and_parsers_survive_every_family(self):
        for family,size,_ in FAMILIES:
            for custom in ({'renderer':'custom-renderer'}, {'parser':'custom-parser'},
                           {'template':'synthetic custom {{ messages }}'}):
                with self.subTest(family=family,custom=custom):
                    base={'model':'rpt-local-source:latest','from':'rpt-local-source:latest',
                          'stream':False,'parameters':{'stop':['synthetic-stop']}}
                    payload={**deepcopy(base),**custom}
                    self.assertEqual(store.registration_template_options(payload,metadata(family,size)),payload)
                    self.assertEqual(store.registration_template_options(base,metadata(family,size),existing=custom),base)

    def test_hy_protocol_template_and_generation_parameters_are_preserved(self):
        payload={'model':'synthetic-hy:latest','files':{'renamed.gguf':'sha256:'+'d'*64},
                 'template':store.HY_TEMPLATE,'parameters':{'stop':['<|eos|>'],
                 'temperature':0.7,'top_p':0.6,'top_k':20},'stream':False}
        before=deepcopy(payload)
        self.assertEqual(store.registration_template_options(payload,metadata('llama','7B')),before)
        self.assertEqual(payload,before)

    def test_native_non_gemma4_registration_skips_gguf_and_api(self):
        installed={'ready':True,'weight':{'digest':'sha256:'+'e'*64}}
        fake=SimpleNamespace(model=Mock(return_value=installed))
        # Qwen 3.5 now checks its embedded canonical template; its dedicated
        # template-options tests cover that path rather than this no-read case.
        for family in ('qwen3','qwen3moe','gemma','gemma3','llama'):
            with self.subTest(family=family), \
                 patch.object(store,'_registered_template_options',return_value=({'model_family':family,'model_families':[family]},{})), \
                 patch.object(store,'gguf',side_effect=AssertionError('Native models need no additional header read')), \
                 patch.object(store,'_create_registered_model',side_effect=AssertionError('No Gemma-specific rewrite')):
                result=store.ensure_registered_template('http://127.0.0.1:1','rpt-local-12b:latest',
                                                        runtime_version='0.40.15',store=fake)
                self.assertFalse(result['updated'])
                self.assertEqual(result['status'],'not_applicable')

    def test_gemma4_small_and_large_repair_preserve_id_weights_and_parameters(self):
        digest='sha256:'+'f'*64
        for size,wanted in (('E4B','gemma4-small'),('12B','gemma4-large')):
            with self.subTest(size=size):
                alias='rpt-local-renamed:latest'
                installed={'ready':True,'weight':{'digest':digest}}
                fake=SimpleNamespace(root=Path('SyntheticStore'),checked=lambda value:value,
                                     runtime_version='0.40.15',model=Mock(return_value=installed))
                # Both config views are synthetic. No blobs or runtime exist.
                options=[({'model_family':'gemma4'},{'renderer':'gemma4','parser':'gemma4'}),
                         ({'model_family':'gemma4'},{'renderer':wanted,'parser':'gemma4'})]
                with patch.object(store,'_registered_template_options',side_effect=options), \
                     patch.object(store,'ManifestStore',return_value=fake), \
                     patch.object(store,'_create_registered_model',return_value={'status':'success'}) as create, \
                     patch.object(store,'gguf',side_effect=AssertionError('Metadata already supplied')), \
                     patch.object(store,'hash_file',side_effect=AssertionError('No weight hash')), \
                     patch.object(store,'choose',side_effect=AssertionError('No selection change')):
                    result=store.ensure_registered_template('http://127.0.0.1:1',alias,
                        runtime_version='0.40.15',metadata=metadata('gemma4',size),store=fake)
                create.assert_called_once_with('http://127.0.0.1:1',{
                    'model':alias,'from':alias,'stream':False,'renderer':wanted})
                self.assertEqual(result['weight_digest'],digest)
                self.assertTrue(result['alias_preserved'])
                self.assertTrue(result['updated'])


if __name__=='__main__':unittest.main()
