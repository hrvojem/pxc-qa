"""Unit tests for util/processes.py and Utility.startup_check() failing fast
when mysqld exits during startup. They need no server.

Run from the repository root (config.py reads ./config.ini):
    python3 test/test_startup_check.py
"""
import io
import os
import sys
import tempfile
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

from util import processes, utility  # noqa: E402
from util.utility import Utility  # noqa: E402

PGREP = ('101 /pxc/bin/mysqld --defaults-file=/qa/conf/node1.cnf --datadir=/qa/node1\n'
         '102 /pxc/bin/mysqld --defaults-file=/qa//conf/ps1.cnf --datadir=/qa//psnode1\n'
         '103 /pxc/bin/mysqld --no-defaults --initialize-insecure --datadir=/qa/node2\n')


class TestProcesses(unittest.TestCase):

    def pgrep(self, output):
        return mock.patch.object(processes.subprocess, 'run', return_value=mock.Mock(stdout=output))

    def test_lists_defaults_files_normalized(self):
        with self.pgrep(PGREP):
            self.assertEqual(processes.mysqld_processes(),
                             [(101, '/qa/conf/node1.cnf'), (102, '/qa/conf/ps1.cnf')])

    def test_running_matches_exact_conf_file(self):
        with self.pgrep(PGREP):
            self.assertTrue(processes.mysqld_running('/qa/conf/node1.cnf'))
            self.assertTrue(processes.mysqld_running('/qa/conf//ps1.cnf'))
            self.assertFalse(processes.mysqld_running('/qa/conf/node2.cnf'))
            self.assertFalse(processes.mysqld_running('/qa/conf/node1.cnf.bak'))

    def test_unknown_when_pgrep_missing(self):
        with mock.patch.object(processes.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertIsNone(processes.mysqld_processes())
            self.assertIsNone(processes.mysqld_running('/qa/conf/node1.cnf'))

    def test_tail_lines(self):
        with tempfile.NamedTemporaryFile('w', suffix='.err', delete=False) as handle:
            handle.write(''.join('line %d\n' % i for i in range(30)))
        try:
            self.assertEqual(processes.tail_lines(handle.name, 3), ['line 27', 'line 28', 'line 29'])
        finally:
            os.remove(handle.name)
        self.assertEqual(processes.tail_lines('/nonexistent/node1.err'), [])


class FakeNode:
    def __init__(self, conf_file='/qa/conf/node2.cnf', err_log='/nonexistent/node2.err', connects_after=None):
        self.conf_file = conf_file
        self.err_log = err_log
        self.connects_after = connects_after
        self.checks = 0

    def connection_check(self, log_error_on_failure=True):
        self.checks += 1
        return 0 if self.connects_after is not None and self.checks >= self.connects_after else 1

    def get_conf_file(self):
        return self.conf_file

    def get_error_log(self):
        return self.err_log

    def get_node_number(self):
        return 2


class TestStartupCheck(unittest.TestCase):

    def run_check(self, node, running, timeout=600):
        clock = iter(range(0, 100000))
        with mock.patch.object(utility.time, 'sleep'), \
                mock.patch.object(utility.time, 'time', side_effect=lambda: next(clock)), \
                mock.patch.object(utility, 'DEFAULT_SERVER_UP_TIMEOUT', timeout), \
                mock.patch.object(processes, 'mysqld_running', side_effect=running) as running_mock, \
                mock.patch.object(Utility, 'check_testcase') as check_testcase, \
                mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            result = Utility('NO').startup_check(node)
        return result, check_testcase.call_args[0][0], running_mock.call_count, out.getvalue()

    def test_server_that_comes_up_passes(self):
        result, status, _, _ = self.run_check(FakeNode(connects_after=3), lambda conf: True)
        self.assertEqual((result, status), (0, 0))

    def test_exited_server_fails_without_waiting_for_timeout(self):
        with tempfile.NamedTemporaryFile('w', suffix='.err', delete=False) as handle:
            handle.write('[ERROR] [MY-000000] [WSREP-SST] socat not found in path\n')
        try:
            states = iter([True, True, False])
            result, status, polls, out = self.run_check(FakeNode(err_log=handle.name),
                                                        lambda conf: next(states))
        finally:
            os.remove(handle.name)
        self.assertNotEqual(status, 0)
        self.assertEqual(result, status)
        self.assertEqual(polls, 3)
        self.assertIn('Cluster node2 mysqld exited during startup', out)
        self.assertIn('socat not found in path', out)
        self.assertNotIn('Timeout', out)

    def test_unknown_process_state_falls_back_to_timeout(self):
        _, status, _, out = self.run_check(FakeNode(), lambda conf: None, timeout=5)
        self.assertNotEqual(status, 0)
        self.assertIn('Timeout while starting/restarting cluster node2', out)

    def test_node_without_conf_file_falls_back_to_timeout(self):
        _, status, polls, out = self.run_check(FakeNode(conf_file=None), lambda conf: False, timeout=5)
        self.assertNotEqual(status, 0)
        self.assertEqual(polls, 0)
        self.assertIn('Timeout', out)


if __name__ == '__main__':
    unittest.main()
