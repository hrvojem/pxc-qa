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


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# ---------------------------------------------------------------------------
import pbt_support  # noqa: E402,F401  (Hypothesis profile)
from hypothesis import given, strategies as st  # noqa: E402

# The suffixes mysqld accepts for size options (8.0.14+ adds T, P, E).
UNITS = {'': 1, 'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4, 'P': 1024 ** 5, 'E': 1024 ** 6}
units = st.sampled_from(sorted(UNITS)).flatmap(lambda u: st.sampled_from([u, u.lower()]))
spaces = st.text(' \t', max_size=2)
# The option name as mysqld accepts it: "_" and "-" are interchangeable.
pool_option_names = st.lists(st.sampled_from('_-'), min_size=3, max_size=3).map(
    lambda s: '--innodb' + s[0] + 'buffer' + s[1] + 'pool' + s[2] + 'size')
other_options = st.sampled_from(['--max-connections=1500', '--innodb_redo_log_capacity=2G',
                                 '--innodb_buffer_pool_instances=8', '--open_files_limit=65535',
                                 '--innodb_log_file_size=1G', '--skip-log-bin'])


class TestReadmeMemoryTable(unittest.TestCase):

    def test_table_matches_the_suites_buffer_pool_options(self):
        import re as _re
        with open(os.path.join(pbt_support.parent_dir, 'README.md')) as readme:
            rows = [line for line in readme if line.startswith('| `') and 'G |' in line]
        self.assertTrue(rows)
        for row in rows:
            suite = _re.match(r'\| `(\w+)`', row).group(1)
            per_node = _re.search(r'\| (\d+G) \|', row).group(1)
            for test in _re.findall(r'`(\w+)`', row.split('|')[1])[1:]:
                path = os.path.join(pbt_support.parent_dir, 'suite', suite, test + '.py')
                with open(path) as source:
                    self.assertIn('--innodb_buffer_pool_size=' + per_node, source.read(), path)


class TestParseSizeProperties(unittest.TestCase):

    @given(st.integers(0, 10 ** 12), units, spaces, spaces)
    def test_number_with_unit_parses_to_bytes(self, number, unit, before, after):
        self.assertEqual(resources.parse_size(before + str(number) + unit + after),
                         number * UNITS[unit.upper()])

    @given(st.integers(0, 10 ** 6), st.sampled_from(['GB', 'B', 'Z', 'k1', 'G G', '.5G', 'e3', 'Gi', 'x', 'PB']))
    def test_number_with_garbage_suffix_is_rejected(self, number, suffix):
        self.assertIsNone(resources.parse_size(str(number) + suffix))

    @given(st.integers(1, 10 ** 6), units)
    def test_negative_size_is_rejected(self, number, unit):
        self.assertIsNone(resources.parse_size('-' + str(number) + unit))


class TestParseSizeRejectProperties(unittest.TestCase):

    @given(st.text(st.characters(whitelist_categories=('Nd',)), min_size=1, max_size=4).filter(
        lambda digits: not digits.isascii()), units)
    def test_non_ascii_digits_are_rejected(self, digits, unit):
        self.assertIsNone(resources.parse_size(digits + unit))

    @given(st.integers(0, 10 ** 6), st.sampled_from(['K', 'M', 'G', 'T']), st.text(' \t', min_size=1, max_size=2))
    def test_space_between_number_and_unit_is_rejected(self, number, unit, gap):
        self.assertIsNone(resources.parse_size(str(number) + gap + unit))


class TestBufferPoolSpellingProperties(unittest.TestCase):

    @given(st.integers(1, 64), st.sampled_from(['M', 'G']), st.sampled_from(['--loose-', '--loose_', '--']),
           st.sampled_from(['', '"', "'"]))
    def test_loose_prefix_and_quoted_values_are_understood(self, n, unit, prefix, quote):
        option = prefix + 'innodb-buffer-pool-size=' + quote + str(n) + unit + quote
        self.assertEqual(resources.buffer_pool_bytes('--max-connections=1500 ' + option), n * UNITS[unit])


class TestDevShmDetectionProperties(unittest.TestCase):

    @given(st.sampled_from(['/dev/shm', '/dev//shm', '/dev/shm/', '/dev/./shm']),
           st.sampled_from(['', '/qa', '/qa/w1']))
    def test_any_spelling_of_a_dev_shm_workdir_gets_the_note(self, shm, rest):
        warning = resources.memory_warning('--innodb_buffer_pool_size=8G', 3, shm + rest, available=1)
        self.assertIn('/dev/shm', warning)

    @given(st.sampled_from(['/dev/shmx', '/dev/shm2/qa', '/devx/shm', '/home/dev/shm']))
    def test_lookalike_paths_do_not_get_the_note(self, workdir):
        warning = resources.memory_warning('--innodb_buffer_pool_size=8G', 3, workdir, available=1)
        self.assertNotIn('The workdir is in /dev/shm', warning)


class TestBufferPoolProperties(unittest.TestCase):

    @given(st.lists(st.tuples(pool_option_names, st.integers(1, 64), st.sampled_from(['M', 'G'])),
                    min_size=1, max_size=3),
           st.lists(other_options, max_size=4), st.randoms())
    def test_last_buffer_pool_option_wins(self, pools, others, rnd):
        pool_args = [name + '=' + str(n) + unit for name, n, unit in pools]
        options = list(others)
        positions = sorted(rnd.sample(range(len(options) + len(pool_args)), len(pool_args)))
        for position, arg in zip(positions, pool_args):
            options.insert(position, arg)
        _, last_n, last_unit = pools[-1]
        self.assertEqual(resources.buffer_pool_bytes(' '.join(options)), last_n * UNITS[last_unit])

    @given(st.lists(other_options, max_size=6))
    def test_no_buffer_pool_option_gives_none(self, others):
        self.assertIsNone(resources.buffer_pool_bytes(' '.join(others)))


class TestMemAvailableProperties(unittest.TestCase):

    @given(st.integers(0, 2 ** 40), st.lists(st.sampled_from(['MemTotal', 'MemFree', 'Buffers', 'Cached',
                                                             'SwapTotal', 'Shmem']), max_size=6),
           st.integers(0, 6))
    def test_reads_memavailable_in_kib_as_bytes(self, kib, other_keys, position):
        lines = ['%s:  %d kB' % (key, 1234) for key in other_keys]
        lines.insert(min(position, len(lines)), 'MemAvailable:   %d kB' % kib)
        with tempfile.NamedTemporaryFile('w', delete=False) as meminfo:
            meminfo.write('\n'.join(lines) + '\n')
        try:
            self.assertEqual(resources.mem_available_bytes(meminfo.name), kib * 1024)
        finally:
            os.remove(meminfo.name)


class TestMemoryWarningProperties(unittest.TestCase):

    @given(st.integers(1, 64), st.sampled_from(['M', 'G']), st.integers(-2, 12), st.integers(0, 2 ** 40),
           st.sampled_from(['/dev/shm/qa', '/home/qa', '']))
    def test_warns_exactly_when_the_pools_exceed_available_memory(self, n, unit, nodes, available, workdir):
        pool = n * UNITS[unit]
        warning = resources.memory_warning('--innodb_buffer_pool_size=%d%s' % (n, unit), nodes, workdir,
                                           available=available)
        self.assertEqual(warning is not None, nodes >= 1 and pool * nodes > available)

    @given(st.integers(1, 12), st.integers(0, 2 ** 40))
    def test_no_buffer_pool_option_never_warns(self, nodes, available):
        self.assertIsNone(resources.memory_warning('--max-connections=1500', nodes, '/dev/shm/qa',
                                                   available=available))


if __name__ == '__main__':
    unittest.main()
