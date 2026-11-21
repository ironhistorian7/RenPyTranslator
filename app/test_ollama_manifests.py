"""Tiny synthetic manifest/GGUF fixtures; no server, downloads, or game data."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gpu_policy
import model_store as store
from ollama_manifests import ManifestError, ManifestStore, MODEL_LAYER
from test_model_store import sample_gguf


LIST_TYPE = 'application/vnd.ollama.manifest.list.v2+json'


class ManifestCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.models = self.root/'models'
        self.ollama = self.models/'ollama'
        for target, value in (('model_store.ROOT', self.root),
                              ('model_store.ensure_idle', None),
                              ('desktop_bridge.preferences', {})):
            mocked = patch(target, value) if target.endswith('.ROOT') else patch(target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def blob(self, payload):
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        digest = 'sha256:'+hashlib.sha256(payload).hexdigest()
        path = self.ollama/'blobs'/digest.replace(':', '-')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return dict(digest=digest, size=len(payload))

    def leaf(self, payload=b'synthetic weights', runner=None):
        weight = dict(self.blob(payload), mediaType=MODEL_LAYER)
        value = dict(schemaVersion=2, config=self.blob(b'{}'), layers=[weight])
        if runner:
            value.update(runner=runner, format='gguf')
        return value

    def reference(self, value, runner='ggml'):
        return dict(self.blob(value), runner=runner, format='gguf')

    def named(self, name, value, *, version='v2', host=None, namespace='library'):
        model, tag = name.rsplit(':', 1)
        base = 'manifests-v2' if version == 'v2' else 'manifests'
        host = host or ('ollama.com' if version == 'v2' else 'registry.ollama.ai')
        path = self.ollama/base/host/namespace/model/tag
        store.save(path, value)
        if version == 'v2':
            # The real Windows v2 named file copies a content-addressed manifest.
            self.blob(path.read_bytes())
        return path

    def test_v2_single_inventory_selection_and_duplicate_download_detection(self):
        value = self.leaf()
        self.named('new:latest', value)
        store.choose('new:latest')
        self.assertIsNone(store.problem())
        installed = store.inventory()['installed']
        self.assertEqual([entry['id'] for entry in installed], ['new:latest'])
        weight = value['layers'][0]
        self.assertEqual(store.installed_match(dict(sha=weight['digest'][7:], size=weight['size'])), 'new:latest')

    def test_public_hosts_merge_with_v2_precedence_and_foreign_namespace_kept(self):
        old = self.named('same:q6', self.leaf(b'old'), version='legacy')
        new = self.named('same:q6', self.leaf(b'new'))
        self.named('other:tag', self.leaf(b'foreign'), version='legacy', host='example.invalid', namespace='team')
        self.named('public:tag', self.leaf(b'public'), namespace='team')
        parsed = ManifestStore(self.ollama)
        self.assertEqual(parsed.names()['same:q6'], [new, old])
        self.assertEqual(parsed.model('same:q6')['weight']['digest'], self.blob(b'new')['digest'])
        self.assertIn('example.invalid/team/other:tag', parsed.names())
        self.assertIn('team/public:tag', parsed.names())
        self.assertEqual(len(store.inventory()['installed']), 3)

    def test_inline_reference_and_nested_list_choose_windows_runner(self):
        old = self.leaf(b'ggml', 'ggml')
        preferred = self.leaf(b'llamacpp', 'llamacpp')
        nested = dict(mediaType=LIST_TYPE, manifests=[self.reference(preferred, 'llamacpp')])
        value = dict(mediaType=LIST_TYPE, manifests=[old, self.reference(nested, 'llamacpp')])
        self.named('nested:latest', value)
        result = ManifestStore(self.ollama).model('nested:latest')
        self.assertTrue(result['ready'])
        self.assertEqual(result['weight']['digest'], preferred['layers'][0]['digest'])

    def test_missing_preferred_reference_falls_back_to_local_runner(self):
        fallback = self.leaf(b'available')
        value = dict(mediaType=LIST_TYPE, manifests=[
            dict(digest='sha256:'+'a'*64, runner='llamacpp'),
            self.reference(fallback, 'ggml')])
        self.named('fallback:latest', value)
        self.assertTrue(ManifestStore(self.ollama).model('fallback:latest')['ready'])

    def test_create_writes_v2_list_then_refresh_reuses_registration(self):
        path = self.models/'arbitrary/nested/model.gguf'
        path.parent.mkdir(parents=True)
        path.write_bytes(sample_gguf())
        def create(request, **kwargs):
            body = json.loads(request.data)
            weight_digest = next(iter(body['files'].values()))
            leaf = dict(config=self.blob(b'{}'), layers=[
                dict(digest=weight_digest, size=path.stat().st_size, mediaType=MODEL_LAYER)])
            self.named(body['model'], dict(mediaType=LIST_TYPE, manifests=[self.reference(leaf)]))
            return io.BytesIO(b'{"status":"success"}')
        opener = type('Opener', (), {})()
        opener.open = create
        with patch('hy_backend.runtime', return_value=contextlib.nullcontext({'endpoint':'http://127.0.0.1:1'})) as runtime, \
             patch.object(store.urllib.request, 'build_opener', return_value=opener), \
             contextlib.redirect_stdout(io.StringIO()):
            alias = store.register(path, manual=True)
        runtime.assert_called_once()
        self.assertEqual(store.inventory()['pending'], [])
        self.assertIn(alias, store.records())
        with patch('hy_backend.runtime', side_effect=AssertionError('Registration must be reused')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(store.register(path, manual=True), alias)

    def test_gpu_uses_resolved_child_without_loading_real_weights(self):
        payload = sample_gguf()
        leaf = self.leaf(payload)
        self.named('fit:latest', dict(mediaType=LIST_TYPE, manifests=[self.reference(leaf)]))
        result = gpu_policy.model_asset('fit:latest')
        weight = leaf['layers'][0]
        self.assertEqual(result['digest'], weight['digest'][7:])
        self.assertEqual(result['metadata']['general.architecture'], 'llama')
        matched = gpu_policy.matching_local(result['digest'], {'bytes':len(payload)})
        self.assertTrue(matched['registered'])
        self.assertEqual(matched['model'], 'fit:latest')

    def test_legacy_delete_preserves_v2_child_and_shared_weight(self):
        leaf = self.leaf()
        old = self.named('old:latest', leaf, version='legacy')
        reference = self.reference(leaf)
        new = self.named('new:latest', dict(mediaType=LIST_TYPE, manifests=[reference]))
        weight = store.blob(leaf['layers'][0]['digest'])
        child = store.blob(reference['digest'])
        store.remove('old:latest')
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(weight.exists())
        self.assertTrue(child.exists())
        store.remove('new:latest')
        self.assertFalse(new.exists())
        self.assertFalse(weight.exists())
        self.assertFalse(child.exists())

    def test_delete_all_alias_paths_retains_nonselected_child_used_elsewhere(self):
        old_leaf, active_leaf, shared_leaf = self.leaf(b'old'), self.leaf(b'active'), self.leaf(b'shared')
        old = self.named('both:latest', old_leaf, version='legacy')
        shared_ref = self.reference(shared_leaf, 'ggml')
        new = self.named('both:latest', dict(mediaType=LIST_TYPE, manifests=[
            self.reference(active_leaf, 'llamacpp'), shared_ref]))
        other = self.named('retained:latest', dict(mediaType=LIST_TYPE, manifests=[shared_ref]))
        store.remove('both:latest')
        self.assertFalse(old.exists())
        self.assertFalse(new.exists())
        self.assertTrue(other.exists())
        self.assertTrue(store.blob(shared_ref['digest']).exists())
        self.assertTrue(store.blob(shared_leaf['layers'][0]['digest']).exists())
        self.assertFalse(store.blob(active_leaf['layers'][0]['digest']).exists())

    def test_downgrade_anchor_manifest_layers_keep_their_child_references(self):
        shared_leaf = self.leaf(b'shared')
        reference = self.reference(shared_leaf)
        anchor = self.leaf(b'anchor')
        anchor['layers'].append(dict(reference, mediaType='application/vnd.docker.distribution.manifest.v2+json'))
        self.named('anchor:latest', anchor, version='legacy')
        self.named('target:latest', shared_leaf)
        store.remove('target:latest')
        self.assertTrue(store.blob(shared_leaf['layers'][0]['digest']).exists())
        self.assertTrue(store.blob(reference['digest']).exists())

    def test_malformed_shared_graph_stops_delete_before_any_changes(self):
        target = self.named('target:latest', self.leaf(), version='legacy')
        broken = self.named('broken:latest', dict(mediaType=LIST_TYPE, manifests=[dict(digest='sha256:'+'b'*64)]))
        with self.assertRaisesRegex(ManifestError, '하위 모델'):
            store.remove('target:latest')
        self.assertTrue(target.exists())
        self.assertTrue(broken.exists())

    def test_reference_cycle_is_rejected_without_reading_weights(self):
        digest = 'sha256:'+'c'*64
        path = self.ollama/'blobs'/digest.replace(':', '-')
        store.save(path, dict(mediaType=LIST_TYPE, manifests=[dict(digest=digest)]))
        self.named('cycle:latest', dict(mediaType=LIST_TYPE, manifests=[dict(digest=digest)]))
        with self.assertRaisesRegex(ManifestError, '순환'):
            ManifestStore(self.ollama).model('cycle:latest')

    def test_v2_missing_runtime_advice_uses_server_result_not_version_cutoff(self):
        value = self.leaf()
        self.named('new:latest', value)
        for version in ('0.34.2', '0.40.15'):
            self.assertIn('최신 추론 런타임', store.missing_runtime_model('new:latest', version))
        self.named('new:latest', value, version='legacy')
        self.assertNotIn('최신 추론 런타임', store.missing_runtime_model('new:latest', '0.34.2'))
        from support_report import classify_issue
        self.assertEqual(classify_issue({'error_type':'ModelRegistrationError'})['code'], 'model_registration')

    def test_same_alias_same_weights_works_in_both_runtime_views(self):
        leaf = self.leaf(sample_gguf())
        self.named('same:latest', leaf, version='legacy')
        self.named('same:latest', dict(mediaType=LIST_TYPE, manifests=[self.reference(leaf)]))
        for runtime_id in ('ollama-0.34.2', 'ollama-0.40.15'):
            result = gpu_policy.model_asset('same:latest', runtime_id)
            self.assertEqual(result['digest'], leaf['layers'][0]['digest'][7:])
            self.assertTrue(gpu_policy.matching_local(result['digest'], {'bytes':result['bytes']}, runtime_id)['registered'])

    def test_conflicting_alias_gpu_and_registration_follow_selected_runtime(self):
        old_payload, new_payload = sample_gguf('Old fixture'), sample_gguf('New fixture')
        old_leaf, new_leaf = self.leaf(old_payload), self.leaf(new_payload)
        self.named('both:latest', old_leaf, version='legacy')
        self.named('both:latest', dict(mediaType=LIST_TYPE, manifests=[self.reference(new_leaf)]))
        self.assertEqual(store.inventory()['installed'][0]['sha'], new_leaf['layers'][0]['digest'][7:])
        for runtime_id, payload, leaf in (('ollama-0.34.2', old_payload, old_leaf),
                                          ('ollama-0.40.15', new_payload, new_leaf)):
            result = gpu_policy.model_asset('both:latest', runtime_id)
            self.assertEqual(result['digest'], leaf['layers'][0]['digest'][7:])
            path = self.models/(runtime_id+'.gguf')
            path.write_bytes(payload)
            with patch('hy_backend.runtime', side_effect=AssertionError('Use the selected existing registration')), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(store.register(path, manual=True, runtime_id=runtime_id), 'both:latest')
        v2_only = self.named('new-only:latest', new_leaf)
        before = v2_only.read_bytes()
        with self.assertRaisesRegex(ManifestError, '최신 추론 런타임'):
            gpu_policy.model_asset('new-only:latest', 'ollama-0.34.2')
        self.assertEqual(v2_only.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
