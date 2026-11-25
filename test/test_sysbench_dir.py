"""Unit tests for sysbench_run.resolve_sysbench_dir(). They need no server.

Run from the repository root (config.py reads ./config.ini):
    python3 test/test_sysbench_dir.py
"""
import io
import os
import sys
import types
import unittest
from unittest import mock

cwd = os.path.dirname(os.path.realpath(__file__))
parent_dir = os.path.normpath(os.path.join(cwd, '../'))
sys.path.insert(0, parent_dir)

# util.db_connection imports mysql.connector at module level. These tests
# never connect, so stub it when the connector isn't installed locally.
try:
    import mysql.connector  # noqa: F401
except ImportError:
    mysql_module = types.ModuleType('mysql')
    connector_module = types.ModuleType('mysql.connector')
    connector_module.errors = types.ModuleType('mysql.connector.errors')
    mysql_module.connector = connector_module
    native_module = types.ModuleType('_mysql_connector')
    native_module.MySQLInterfaceError = type('MySQLInterfaceError', (Exception,), {})
    sys.modules.update({'mysql': mysql_module, 'mysql.connector': connector_module,
                        'mysql.connector.errors': connector_module.errors,
                        '_mysql_connector': native_module})

from util.sysbench_run import resolve_sysbench_dir  # noqa: E402

CANDIDATES = ['/usr/local/share/sysbench', '/usr/share/sysbench']


def files(*directories):
    present = {os.path.join(d, 'oltp_common.lua') for d in directories}
    return lambda path: path in present


class TestResolveSysbenchDir(unittest.TestCase):

    def resolve(self, configured, isfile):
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            result = resolve_sysbench_dir(configured, CANDIDATES, isfile)
        return result, out.getvalue()

    def test_configured_dir_with_scripts_is_kept(self):
        result, out = self.resolve('/opt/sysbench/share', files('/opt/sysbench/share', '/usr/share/sysbench'))
        self.assertEqual(result, '/opt/sysbench/share')
        self.assertEqual(out, '')

    def test_falls_back_to_package_location(self):
        result, out = self.resolve('/usr/local/share/sysbench', files('/usr/share/sysbench'))
        self.assertEqual(result, '/usr/share/sysbench')
        self.assertIn('using /usr/share/sysbench', out)

    def test_falls_back_to_source_build_location(self):
        result, _ = self.resolve('/wrong/path', files('/usr/local/share/sysbench', '/usr/share/sysbench'))
        self.assertEqual(result, '/usr/local/share/sysbench')

    def test_nothing_found_keeps_configured_dir(self):
        result, out = self.resolve('/wrong/path', files())
        self.assertEqual(result, '/wrong/path')
        self.assertEqual(out, '')


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# ---------------------------------------------------------------------------
import pbt_support  # noqa: E402,F401  (Hypothesis profile)
from hypothesis import given, strategies as st  # noqa: E402

DIRS = ['/usr/local/share/sysbench', '/usr/share/sysbench', '/opt/sysbench', '/wrong/path']


class TestResolveSysbenchDirProperties(unittest.TestCase):

    @given(st.sampled_from(DIRS), st.sets(st.sampled_from(DIRS)), st.sampled_from(['', '/']))
    def test_configured_dir_or_first_standard_dir_with_scripts(self, configured, with_scripts, trailing):
        with mock.patch('sys.stdout', new_callable=io.StringIO):
            result = resolve_sysbench_dir(configured + trailing, CANDIDATES, files(*with_scripts))
        if configured in with_scripts:
            expected = configured + trailing
        else:
            usable = [c for c in CANDIDATES if c in with_scripts]
            expected = usable[0] if usable else configured + trailing
        self.assertEqual(result, expected)


if __name__ == '__main__':
    unittest.main()
