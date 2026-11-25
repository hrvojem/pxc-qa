"""Unit tests for util/error_log.py and the test runner's crash check.
They need no server.

Run from the repository root (config.py reads ./config.ini):
    python3 test/test_error_log.py
"""
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

cwd = os.path.dirname(os.path.realpath(__file__))
parent_dir = os.path.normpath(os.path.join(cwd, '../'))
sys.path.insert(0, parent_dir)

from util import error_log  # noqa: E402
import qa_framework  # noqa: E402

# Lines taken from real PXC 8.4 error logs.
CRASH_LINES = [
    '2026-11-03T21:17:24Z UTC - mysqld got signal 6 ;',
    '2026-11-03T21:17:24.650040Z 1 [ERROR] [MY-013183] [InnoDB] Assertion failure: '
    'lock0lock.cc:336:lock_latest_err_file thread 127909209216704',
    "mysqld: /src/sql/handler.cc:123: int foo(): Assertion `thd != nullptr' failed.",
    '==12345==ERROR: AddressSanitizer: heap-use-after-free on address 0x6020',
    '==12345==ERROR: LeakSanitizer: detected memory leaks',
    'WARNING: ThreadSanitizer: data race (pid=12345)',
    '==12345==WARNING: MemorySanitizer: use-of-uninitialized-value',
    '/src/storage/innobase/row/row0mysql.cc:42:7: runtime error: signed integer overflow',
    "terminate called after throwing an instance of 'std::bad_alloc'",
    '2026-11-23T20:51:15Z 0 [ERROR] [MY-012153] [InnoDB] Database page corruption on disk or a failed file read',
    '2026-11-23T20:51:15Z 0 [ERROR] [MY-000000] [Galera] Inconsistency detected: Inconsistent by consensus',
]

CLEAN_LINES = [
    '2026-11-23T20:51:15.731456Z 0 [ERROR] [MY-000000] [WSREP-SST] socat not found in path',
    '2026-11-23T20:51:15Z 0 [ERROR] [MY-000067] [Server] unknown variable \'foo=1\'.',
    '2026-11-23T20:51:15Z 0 [ERROR] [MY-010119] [Server] Aborting',
    # Boilerplate printed after any crash; the crash line itself is what counts.
    'InnoDB: corruption in the InnoDB tablespace. Please refer to',
    '2026-11-23T20:51:09Z 0 [Note] [MY-012550] [InnoDB] Database was not shutdown normally!',
    '2026-11-23T20:51:15Z 0 [Note] [MY-000000] [Galera] Received SELF-LEAVE. Closing connection.',
    # Informational sanitizer output, not a report.
    '==12345==INFO: AddressSanitizer ignores mlock/mlockall/munlock/munlockall',
    '==12345==WARNING: AddressSanitizer failed to allocate 0x10000000000 bytes',
]


class TestScanErrorLog(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, lines):
        path = os.path.join(self.log_dir, name)
        with open(path, 'w') as handle:
            handle.write('\n'.join(lines) + '\n')
        return path

    def test_each_crash_line_is_found(self):
        for line in CRASH_LINES:
            with self.subTest(line=line):
                path = self.write('node1.err', CLEAN_LINES + [line])
                findings = error_log.scan_error_log(path)
                self.assertEqual([f[2] for f in findings], [line])
                self.assertEqual(findings[0][0], len(CLEAN_LINES) + 1)

    def test_clean_log_has_no_findings(self):
        path = self.write('node1.err', CLEAN_LINES)
        self.assertEqual(error_log.scan_error_log(path), [])

    def test_allowed_pattern_suppresses_finding(self):
        import re
        path = self.write('node1.err', [CRASH_LINES[0]])
        self.assertEqual(error_log.scan_error_log(path, [re.compile('got signal 6')]), [])

    def test_scan_covers_node_ps_and_init_logs_only(self):
        self.write('node2.err', [CRASH_LINES[0]])
        self.write('psnode1.err', [CRASH_LINES[1]])
        self.write('init3.log', [CRASH_LINES[2]])
        self.write('ps_init1.log', [CRASH_LINES[3]])
        self.write('node1.err', CLEAN_LINES)
        self.write('sysbench.log', [CRASH_LINES[0]])
        results = error_log.scan_error_logs(self.log_dir)
        self.assertEqual(sorted(os.path.basename(p) for p in results),
                         ['init3.log', 'node2.err', 'ps_init1.log', 'psnode1.err'])

    def test_clear_removes_only_error_logs(self):
        self.write('node1.err', CLEAN_LINES)
        self.write('init1.log', CLEAN_LINES)
        self.write('ps_init1.log', CLEAN_LINES)
        self.write('sysbench.log', CLEAN_LINES)
        error_log.clear_error_logs(self.log_dir)
        self.assertEqual(os.listdir(self.log_dir), ['sysbench.log'])

    def test_format_caps_findings_per_file(self):
        path = self.write('node1.err', [CRASH_LINES[0]] * 15)
        lines = error_log.format_findings(error_log.scan_error_logs(self.log_dir))
        self.assertEqual(lines[0], 'Crash markers in ' + path + ':')
        self.assertEqual(len(lines), 1 + error_log.MAX_FINDINGS_PER_FILE + 1)
        self.assertEqual(lines[-1], '  ... 5 more')


class TestWaitForServers(unittest.TestCase):

    PGREP = ('101 /pxc/bin/mysqld --defaults-file=/qa/conf/node1.cnf --datadir=/qa/node1\n'
             '102 /pxc/bin/mysqld --defaults-file=/qa/w1/conf/node1.cnf --datadir=/qa/w1/node1\n'
             '103 /pxc/bin/mysqld --defaults-file=/qa//conf/ps1.cnf --datadir=/qa//psnode1\n'
             '104 /usr/sbin/mysqld --defaults-file=/etc/mysql/my.cnf\n'
             '105 /pxc/bin/mysqld --defaults-file=/qa/conf/sub/x.cnf\n'
             '106 /pxc/bin/mysqld --no-defaults --datadir=/qa/conf/\n')

    def test_only_this_workers_servers_are_matched(self):
        with mock.patch.object(error_log.processes.subprocess, 'run',
                               return_value=mock.Mock(stdout=self.PGREP)):
            self.assertEqual(error_log.worker_server_pids('/qa'), [101, 103])
            self.assertEqual(error_log.worker_server_pids('/qa/'), [101, 103])
            self.assertEqual(error_log.worker_server_pids('/qa/w1/'), [102])

    def test_waits_for_servers_that_exit(self):
        running = [[101], [101], []]
        with mock.patch.object(error_log, 'worker_server_pids', side_effect=lambda d: running.pop(0)), \
                mock.patch.object(error_log.time, 'sleep'), \
                mock.patch.object(error_log.os, 'kill') as kill:
            self.assertEqual(error_log.wait_for_servers_to_exit('/qa', timeout=60), ([], []))
        kill.assert_not_called()

    def test_kills_servers_still_running_after_timeout(self):
        with mock.patch.object(error_log, 'worker_server_pids', side_effect=[[101], [], []]), \
                mock.patch.object(error_log.time, 'sleep'), \
                mock.patch.object(error_log.os, 'kill') as kill:
            self.assertEqual(error_log.wait_for_servers_to_exit('/qa', timeout=0), ([101], []))
        kill.assert_called_once_with(101, error_log.signal.SIGKILL)

    def test_reports_servers_that_survive_sigkill(self):
        clock = iter(range(0, 1000, 10))
        with mock.patch.object(error_log, 'worker_server_pids', return_value=[101]), \
                mock.patch.object(error_log.time, 'sleep'), \
                mock.patch.object(error_log.time, 'monotonic', side_effect=lambda: next(clock)), \
                mock.patch.object(error_log.os, 'kill'):
            self.assertEqual(error_log.wait_for_servers_to_exit('/qa', timeout=0), ([101], [101]))


class TestHandleTestResult(unittest.TestCase):

    def handle(self, result, crash_findings, killed_pids=None, surviving_pids=None):
        with mock.patch.object(qa_framework, 'print_failed_test_log'), \
                mock.patch.object(qa_framework.os, 'system'), \
                mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            failed = qa_framework.handle_test_result(io.StringIO(), 0, 'correctness', 'x.py', result,
                                                     1, crash_findings, killed_pids, surviving_pids)
        return failed, out.getvalue()

    def test_passing_test_with_crash_fails(self):
        failed, out = self.handle(0, {'/w/log/node2.err': [(7, 'mysqld crashed on a signal', CRASH_LINES[0])]})
        self.assertTrue(failed)
        self.assertIn('[Fail]', out)
        self.assertIn('line 7 (mysqld crashed on a signal)', out)

    def test_skipped_test_with_crash_fails(self):
        failed, _ = self.handle(qa_framework.SKIP_TEST_EXIT_CODE,
                                {'/w/log/node1.err': [(1, 'assertion failure', CRASH_LINES[1])]})
        self.assertTrue(failed)

    def test_killed_servers_are_reported(self):
        failed, out = self.handle(0, {}, killed_pids=[101])
        self.assertFalse(failed)
        self.assertIn('Killed mysqld process(es) still running', out)

    def test_server_surviving_sigkill_fails_test(self):
        failed, out = self.handle(0, {}, killed_pids=[101], surviving_pids=[101])
        self.assertTrue(failed)
        self.assertIn('still running 30s after SIGKILL: 101', out)

    def test_passing_test_without_crash_passes(self):
        failed, out = self.handle(0, {})
        self.assertFalse(failed)
        self.assertIn('[Pass]', out)


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# ---------------------------------------------------------------------------
import os.path  # noqa: E402
import re  # noqa: E402

import pbt_support  # noqa: E402,F401  (Hypothesis profile)
from hypothesis import assume, given, strategies as st  # noqa: E402

DESCRIPTIONS = [description for description, _ in error_log.CRASH_PATTERNS]


def matches_any_marker(text):
    return any(pattern.search(text) for _, pattern in error_log.CRASH_PATTERNS)


# Log noise: printable text without line breaks that contains no crash marker.
noise = st.text(st.characters(blacklist_characters='\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029',
                              blacklist_categories=('Cs',)), max_size=40).filter(
    lambda text: not matches_any_marker(text))
words = st.text(st.characters(whitelist_categories=('Ll', 'Lu', 'Nd')), min_size=1, max_size=10)
digits = st.integers(0, 10 ** 6).map(str)

# One generator per crash marker kind, each paired with the description it must be reported as.
crash_markers = st.one_of(
    digits.map(lambda n: ('mysqld crashed on a signal', 'mysqld got signal ' + n + ' ;')),
    st.tuples(words, digits).map(lambda a: ('assertion failure', 'Assertion failure: ' + a[0] + '.cc:' + a[1])),
    words.map(lambda expr: ('assertion failure', 'Assertion `' + expr + "' failed.")),
    st.tuples(st.sampled_from(['ERROR', 'WARNING']), st.sampled_from(['Address', 'Thread', 'Memory', 'Leak']),
              words).map(lambda a: ('sanitizer report', a[0] + ': ' + a[1] + 'Sanitizer: ' + a[2])),
    st.tuples(words, digits).map(lambda a: ('sanitizer report', a[0] + '.cc:' + a[1] + ': runtime error: x')),
    words.map(lambda kind: ('uncaught C++ exception', "terminate called after throwing an instance of '"
                            + kind + "'")),
    digits.map(lambda n: ('InnoDB corruption', '[ERROR] [MY-' + n + '] [InnoDB] page is corrupted')),
    st.just(('InnoDB corruption', 'Database page corruption on disk or a failed file read')),
    # Wording below is taken from the PXC 8.4.8 binaries (libgalera_smm.so, mysqld)
    # and libstdc++, not from memory.
    st.sampled_from([('Galera inconsistency', 'Inconsistency detected: Inconsistent by consensus'),
                     ('Galera inconsistency', 'Node consistency compromized, leaving cluster...'),
                     ('Galera inconsistency', 'Node consistency compromized, need to abort.'),
                     ('Galera inconsistency', 'a1b2c3 is inconsistent with group. Leaving cluster.'),
                     ('uncaught C++ exception', 'terminate called without an active exception'),
                     ('uncaught C++ exception', 'terminate called recursively'),
                     ('sanitizer report', '==7==ERROR: UndefinedBehaviorSanitizer: SEGV on unknown address'),
                     ('sanitizer report', '==7==ERROR: HWAddressSanitizer: tag-mismatch'),
                     # MySQL/PXC 5.7 log format: "[ERROR] InnoDB: ..." rather than "[InnoDB]".
                     ('InnoDB corruption', '[ERROR] InnoDB: Corruption of an index tree')]))

# Lines that look like markers but are informational or below [ERROR] level.
near_misses = st.one_of(
    words.map(lambda w: 'INFO: AddressSanitizer ignores mlock/mlockall/munlock/munlockall ' + w),
    digits.map(lambda n: 'WARNING: AddressSanitizer failed to allocate 0x' + n + ' bytes'),
    st.sampled_from(['[Note]', '[Warning]', '[System]']).map(
        lambda level: level + ' [MY-012345] [InnoDB] checking for corruption'),
    st.just('InnoDB: corruption in the InnoDB tablespace. Please refer to'),
    words.map(lambda w: 'mysqld_safe got ' + w),
    st.just('[ERROR] [MY-000067] [Server] unknown variable'),
    st.just('[ERROR] [MY-010119] [Server] Aborting'))


def write_lines(directory, name, lines):
    path = os.path.join(directory, name)
    with open(path, 'w', newline='\n') as handle:
        handle.write('\n'.join(lines) + '\n')
    return path


class TestScanProperties(unittest.TestCase):

    @given(st.lists(noise, max_size=20), st.lists(st.tuples(crash_markers, noise, noise), max_size=5),
           st.randoms())
    def test_scan_reports_exactly_the_planted_markers(self, noise_lines, planted, rnd):
        lines = [(None, line) for line in noise_lines]
        lines += [(description, before + marker + after) for (description, marker), before, after in planted]
        rnd.shuffle(lines)
        with tempfile.TemporaryDirectory() as directory:
            path = write_lines(directory, 'node1.err', [text for _, text in lines])
            findings = error_log.scan_error_log(path, [])
        expected = [(number, description, text) for number, (description, text) in enumerate(lines, 1)
                    if description is not None]
        self.assertEqual(findings, expected)

    @given(st.lists(st.tuples(noise, near_misses, noise), min_size=1, max_size=10))
    def test_near_misses_are_never_reported(self, lines):
        with tempfile.TemporaryDirectory() as directory:
            path = write_lines(directory, 'node1.err', [a + line + b for a, line, b in lines])
            self.assertEqual(error_log.scan_error_log(path, []), [])

    @given(st.lists(st.tuples(crash_markers, words), min_size=1, max_size=6, unique_by=lambda x: x[1]),
           st.data())
    def test_allowed_patterns_suppress_only_matching_lines(self, planted, data):
        lines = [marker + ' id=' + tag + ';' for (_, marker), tag in planted]
        allowed_tags = data.draw(st.sets(st.sampled_from([tag for _, tag in planted])))
        allowed = [re.compile(re.escape(' id=' + tag + ';')) for tag in allowed_tags]
        with tempfile.TemporaryDirectory() as directory:
            path = write_lines(directory, 'node1.err', lines)
            reported = [text for _, _, text in error_log.scan_error_log(path, allowed)]
        self.assertEqual(reported, [line for line, (_, tag) in zip(lines, planted) if tag not in allowed_tags])


# File names in a worker's log directory: the ones mysqld writes, and others.
mysqld_log_names = st.one_of(
    st.tuples(st.sampled_from(['node', 'psnode']), digits).map(lambda a: a[0] + a[1] + '.err'),
    st.tuples(st.sampled_from(['init', 'ps_init']), digits).map(lambda a: a[0] + a[1] + '.log'))
other_names = st.one_of(
    st.sampled_from(['sysbench.log', 'node1.err.bak', 'init1.txt', 'startup1.sh', 'node1.cnf', 'tests_log']),
    words.map(lambda w: w + '.out'))


def is_mysqld_log(name):
    return name.endswith('.err') or (name.startswith(('init', 'ps_init')) and name.endswith('.log'))


class TestLogFileSelectionProperties(unittest.TestCase):

    @given(st.sets(mysqld_log_names, max_size=6), st.sets(other_names, max_size=6))
    def test_clear_removes_exactly_the_mysqld_logs(self, logs, others):
        # Distinct even on a case-insensitive filesystem (macOS), where "A.out" == "a.out".
        assume(len({name.casefold() for name in logs | others}) == len(logs | others))
        with tempfile.TemporaryDirectory() as directory:
            for name in logs | others:
                write_lines(directory, name, ['x'])
            error_log.clear_error_logs(directory)
            self.assertEqual(set(os.listdir(directory)), {name for name in others if not is_mysqld_log(name)})

    @given(st.sets(mysqld_log_names, max_size=6), st.sets(other_names, max_size=6), crash_markers)
    def test_scan_reads_exactly_the_mysqld_logs(self, logs, others, marker):
        assume(len({name.casefold() for name in logs | others}) == len(logs | others))
        with tempfile.TemporaryDirectory() as directory:
            for name in logs | others:
                write_lines(directory, name, [marker[1]])
            scanned = {os.path.basename(path) for path in error_log.scan_error_logs(directory)}
        self.assertEqual(scanned, {name for name in logs | others if is_mysqld_log(name)})


# Directory names containing glob syntax, next to a sibling the glob would match instead.
glob_dir_names = st.tuples(st.sampled_from(['[', '*', '?']), words).map(
    lambda a: {'[': ('qa[1]', 'qa1'), '*': ('qa*', 'qaX'), '?': ('qa?', 'qaZ')}[a[0]])


class TestLogDirectoryProperties(unittest.TestCase):

    @given(glob_dir_names, st.sets(mysqld_log_names, min_size=1, max_size=4))
    def test_glob_characters_in_the_log_dir_are_literal(self, names, logs):
        own, sibling = names
        with tempfile.TemporaryDirectory() as root:
            for directory in (own, sibling):
                os.mkdir(os.path.join(root, directory))
                for name in logs:
                    write_lines(os.path.join(root, directory), name, ['mysqld got signal 11 ;'])
            own_dir = os.path.join(root, own)
            self.assertEqual(error_log.error_log_files(own_dir), sorted(os.path.join(own_dir, n) for n in logs))
            error_log.clear_error_logs(own_dir)
            self.assertEqual(set(os.listdir(os.path.join(root, sibling))), logs)

    @given(st.sets(mysqld_log_names, max_size=4), mysqld_log_names)
    def test_a_directory_named_like_a_log_is_not_a_log(self, logs, directory_name):
        assume(directory_name not in logs)
        with tempfile.TemporaryDirectory() as root:
            for name in logs:
                write_lines(root, name, ['x'])
            os.mkdir(os.path.join(root, directory_name))
            self.assertNotIn(os.path.join(root, directory_name), error_log.error_log_files(root))
            error_log.clear_error_logs(root)
            self.assertEqual(os.listdir(root), [directory_name])

    @given(st.lists(st.tuples(noise, st.booleans()), min_size=1, max_size=15), crash_markers, st.data())
    def test_line_numbers_match_grep_n_even_with_stray_carriage_returns(self, lines, marker, data):
        texts = [text + ('\r' if cr else '') for text, cr in lines]
        position = data.draw(st.integers(0, len(texts)))
        texts.insert(position, marker[1])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'node1.err')
            with open(path, 'w', newline='') as handle:
                handle.write('\n'.join(texts) + '\n')
            findings = error_log.scan_error_log(path, [])
        self.assertEqual([number for number, _, _ in findings], [position + 1])


class TestFormatFindingsProperties(unittest.TestCase):

    @given(st.dictionaries(words, st.integers(1, 40), min_size=1, max_size=4))
    def test_each_file_shows_at_most_the_cap_then_a_count_of_the_rest(self, counts):
        results = {path: [(n, 'assertion failure', 'line ' + str(n)) for n in range(1, count + 1)]
                   for path, count in counts.items()}
        lines = error_log.format_findings(results)
        cap = error_log.MAX_FINDINGS_PER_FILE
        expected_length = sum(1 + min(count, cap) + (1 if count > cap else 0) for count in counts.values())
        self.assertEqual(len(lines), expected_length)
        for path, count in counts.items():
            if count > cap:
                self.assertIn('  ... ' + str(count - cap) + ' more', lines)


# Path spellings: the same directory written with doubled slashes or "./".
def spellings(path):
    parts = [part for part in path.split('/') if part]
    return st.lists(st.sampled_from(['/', '//', '/./']), min_size=len(parts), max_size=len(parts)).map(
        lambda seps: ''.join(sep + part for sep, part in zip(seps, parts)))


class TestWorkerServerPidsProperties(unittest.TestCase):

    @given(st.lists(st.tuples(st.sampled_from(['/qa', '/qa/w1', '/qa/w2', '/other']),
                              st.sampled_from(['conf', 'conf/sub', 'log']), words), max_size=6),
           st.sampled_from(['/qa', '/qa/w1', '/qa/w2']), st.data())
    def test_a_server_belongs_to_the_worker_whose_conf_dir_holds_its_config(self, servers, worker, data):
        listing = ''
        for pid, (base, sub, name) in enumerate(servers, 100):
            conf = data.draw(spellings(base + '/' + sub + '/' + name + '.cnf'))
            listing += str(pid) + ' /pxc/bin/mysqld --defaults-file=' + conf + ' --datadir=/x\n'
        worker_spelling = data.draw(spellings(worker)) + data.draw(st.sampled_from(['', '/']))
        with mock.patch.object(error_log.processes.subprocess, 'run', return_value=mock.Mock(stdout=listing)):
            pids = error_log.worker_server_pids(worker_spelling)
        self.assertEqual(pids, [pid for pid, (base, sub, _) in enumerate(servers, 100)
                                if base == worker and sub == 'conf'])


if __name__ == '__main__':
    unittest.main()
