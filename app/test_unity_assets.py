import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import unity_assets as unity


class Object:
    def __init__(self, path_id, kind, raw=b'', script='', name='name'):
        self.path_id = path_id
        self.type = SimpleNamespace(name=kind)
        self.class_id = 49 if kind == 'TextAsset' else 1
        self.type_id = self.class_id
        self.raw = raw
        self.value = SimpleNamespace(m_Name=name, m_PathName=None, m_Script=script)
    def get_raw_data(self):
        return self.raw
    def read(self):
        return self.value


def file_with(*objects):
    return SimpleNamespace(objects={o.path_id: o for o in objects}, externals=[])


class VerifiedAssetsTest(unittest.TestCase):
    def test_accepts_replacement_and_untouched_payload(self):
        before = file_with(Object(1, 'TextAsset', script='old'), Object(2, 'Other', b'unchanged'))
        after = file_with(Object(1, 'TextAsset', script='new'), Object(2, 'Other', b'unchanged'))
        with patch.object(unity, '_file', side_effect=[before, after]):
            self.assertTrue(unity.verify_asset('source', 'output', {1: b'new'}))

    def test_rejects_changed_unselected_object_and_references(self):
        before = file_with(Object(1, 'TextAsset', script='old'), Object(2, 'Other', b'original'))
        after = file_with(Object(1, 'TextAsset', script='new'), Object(2, 'Other', b'changed'))
        with patch.object(unity, '_file', side_effect=[before, after]):
            with self.assertRaises(unity.UnityAssetError):
                unity.verify_asset('source', 'output', {1: b'new'})
        after.objects[2].raw = b'original'
        after.externals = [SimpleNamespace(path='other', guid=b'', type=0, asset_path='')]
        with patch.object(unity, '_file', side_effect=[before, after]):
            with self.assertRaises(unity.UnityAssetError):
                unity.verify_asset('source', 'output', {1: b'new'})

    def test_rejects_wrong_replacement_or_type(self):
        before = file_with(Object(1, 'TextAsset', script='old'))
        after = file_with(Object(1, 'TextAsset', script='different'))
        with patch.object(unity, '_file', side_effect=[before, after]):
            with self.assertRaises(unity.UnityAssetError):
                unity.verify_asset('source', 'output', {1: b'new'})
        with patch.object(unity, '_file', side_effect=[before, after]):
            with self.assertRaises(unity.UnityAssetError):
                unity.verify_asset('source', 'output', {7: b'new'})


class DiscoveryTest(unittest.TestCase):
    def test_snapshot_resource_is_explicit_and_version_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'game'
            data = root / 'Game_Data'
            data.mkdir(parents=True)
            snapshot = Path(directory) / 'snapshot'
            resource = snapshot / data.name / 'resources.assets'
            resource.parent.mkdir(parents=True)
            resource.touch()
            manager_object = Object(99, 'ResourceManager')
            manager_object.value = SimpleNamespace(m_Container=[])
            manager = file_with(manager_object)
            manager.unity_version = '2022.3.1f1'
            resources = file_with()
            resources.unity_version = manager.unity_version
            with patch.object(unity, '_data_dir', return_value=data), patch.object(unity, '_file', side_effect=[manager, resources]) as load:
                self.assertEqual(unity.inspect_unity(root, asset_root=snapshot)['groups'], [])
                self.assertEqual(load.call_args_list[1].args[0], resource.resolve())
            resources.unity_version = '2021.3.1f1'
            with patch.object(unity, '_data_dir', return_value=data), patch.object(unity, '_file', side_effect=[manager, resources]):
                with self.assertRaises(unity.UnityAssetError):
                    unity.inspect_unity(root, asset_root=snapshot)
            with patch.object(unity, '_data_dir', return_value=data):
                with self.assertRaises(unity.UnityAssetError):
                    unity.inspect_unity(root)
                with self.assertRaises(unity.UnityAssetError):
                    unity.inspect_unity(root, asset_root=Path(directory) / 'missing')

    def test_aliases_require_parallel_locales_and_report_indicators(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'Game_Data'
            data.mkdir()
            (data / 'resources.assets').touch()
            (data / 'ScriptingAssemblies.json').write_text('Unity.Localization.dll Unity.Addressables.dll')
            entries = [('localization/ja/ui', 1), ('localization/en/ui', 2),
                       ('translations/ja/only', 3), ('config/ja/ui', 4), ('config/en/ui', 5)]
            pointers = [(name, SimpleNamespace(m_FileID=1, m_PathID=ident)) for name, ident in entries]
            manager_object = Object(99, 'ResourceManager')
            manager_object.value = SimpleNamespace(m_Container=pointers)
            manager = file_with(manager_object)
            manager.externals = [SimpleNamespace(path='resources.assets')]
            resources = file_with(*(Object(i, 'TextAsset') for i in range(1, 6)))
            with patch.object(unity, '_data_dir', return_value=data), patch.object(unity, '_file', side_effect=[manager, resources]):
                result = unity.inspect_unity(directory)
            self.assertEqual([g['group'] for g in result['groups']], ['localization/ui'])
            self.assertEqual(set(result['groups'][0]['languages']), {'ja', 'en'})
            self.assertEqual(len(result['indicators']), 2)


if __name__ == '__main__':
    unittest.main()
