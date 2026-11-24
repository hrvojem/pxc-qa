"""Unit tests for util/resources.py. They need no server.

Run from the repository root:
    python3 test/test_resources.py
"""
import os
import sys
import tempfile
import unittest

cwd = os.path.dirname(os.path.realpath(__file__))
parent_dir = os.path.normpath(os.path.join(cwd, '../'))
sys.path.insert(0, parent_dir)

from util import resources  # noqa: E402

GIB = 1024 ** 3


class TestParsing(unittest.TestCase):

    def test_parse_size(self):
        self.assertEqual(resources.parse_size('8G'), 8 * GIB)
        self.assertEqual(resources.parse_size('512m'), 512 * 1024 ** 2)
        self.assertEqual(resources.parse_size('134217728'), 134217728)
        self.assertEqual(resources.parse_size('1T'), 1024 * GIB)
        for bad in ('', 'G', '8GB', '-1G', '1.5G'):
            self.assertIsNone(resources.parse_size(bad), bad)

    def test_buffer_pool_from_options(self):
        self.assertEqual(resources.buffer_pool_bytes(
            '--max-connections=1500 --innodb_buffer_pool_size=8G --innodb_redo_log_capacity=2G'), 8 * GIB)
        self.assertEqual(resources.buffer_pool_bytes('--innodb-buffer-pool-size=2G'), 2 * GIB)
        self.assertEqual(resources.buffer_pool_bytes(
            '--innodb_buffer_pool_size=8G --innodb_buffer_pool_size=1G'), GIB)
        self.assertIsNone(resources.buffer_pool_bytes('--max-connections=1500'))
        self.assertIsNone(resources.buffer_pool_bytes(None))

    def test_mem_available(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as meminfo:
            meminfo.write('MemTotal:       16000000 kB\nMemFree:  1000 kB\nMemAvailable:   12582912 kB\n')
        try:
            self.assertEqual(resources.mem_available_bytes(meminfo.name), 12 * GIB)
        finally:
            os.remove(meminfo.name)
        self.assertIsNone(resources.mem_available_bytes('/nonexistent/meminfo'))


class TestMemoryWarning(unittest.TestCase):

    def test_warns_when_buffer_pools_exceed_available_memory(self):
        warning = resources.memory_warning('--innodb_buffer_pool_size=8G', 3, '/home/qa', available=15 * GIB)
        self.assertIn('3 node(s) x 8.0 GiB InnoDB buffer pool = 24.0 GiB', warning)
        self.assertIn('only 15.0 GiB', warning)
        self.assertNotIn('/dev/shm', warning)

    def test_mentions_dev_shm_workdir(self):
        warning = resources.memory_warning('--innodb_buffer_pool_size=8G', 3, '/dev/shm/qa', available=15 * GIB)
        self.assertIn('The workdir is in /dev/shm', warning)

    def test_no_warning_when_it_fits_or_is_unknown(self):
        self.assertIsNone(resources.memory_warning('--innodb_buffer_pool_size=2G', 3, '', available=15 * GIB))
        self.assertIsNone(resources.memory_warning('--max-connections=1500', 3, '', available=1))
        self.assertIsNone(resources.memory_warning('--innodb_buffer_pool_size=8G', 0, '', available=1))


if __name__ == '__main__':
    unittest.main()
