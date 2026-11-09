"""Bounded cache commit retry on invented files only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch,call

from atomic_files import replace_cache_file


class AtomicFileTests(unittest.TestCase):
    def test_normal_commit_has_no_sleep_or_extra_attempt(self):
        source=Mock()
        with patch('atomic_files.time.sleep') as sleep:
            replace_cache_file(source,'destination')
        source.replace.assert_called_once_with('destination')
        sleep.assert_not_called()

    def test_transient_lock_retries_only_the_replace(self):
        source=Mock()
        source.replace.side_effect=[PermissionError('busy'),None]
        with patch('atomic_files.time.sleep') as sleep:
            replace_cache_file(source,'destination')
        self.assertEqual(source.replace.call_count,2)
        self.assertEqual(sleep.call_args_list,[call(30)])

    def test_persistent_lock_preserves_both_files_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'cache';source=Path(temp)/'cache.tmp'
            dest.write_bytes(b'previous');source.write_bytes(b'pending')
            with patch.object(Path,'replace',side_effect=PermissionError('busy')) as replace, \
                 patch('atomic_files.time.sleep'),self.assertRaises(PermissionError):
                replace_cache_file(source,dest)
            self.assertEqual(replace.call_count,2)
            self.assertEqual(dest.read_bytes(),b'previous')
            self.assertEqual(source.read_bytes(),b'pending')

    def test_unrelated_io_error_is_not_retried(self):
        source=Mock()
        source.replace.side_effect=OSError('disk error')
        with patch('atomic_files.time.sleep') as sleep,self.assertRaises(OSError):
            replace_cache_file(source,'destination')
        self.assertEqual(source.replace.call_count,1)
        sleep.assert_not_called()
