"""Property-based tests (Hypothesis) for the test runner's helpers in
qa_framework.py: option parsing, test selection, disabled tests, ordering,
duration formatting and result classification. They need no server.

Run from the repository root (config.py reads ./config.ini):
    python3 test/test_qa_framework.py
"""
import io
import os
import tempfile
import unittest
from unittest import mock

import pbt_support  # noqa: F401  (sys.path, mysql stub, Hypothesis profile)
from hypothesis import assume, given, strategies as st

import qa_framework  # noqa: E402

SUITES = qa_framework.SUITES
SKIP = qa_framework.SKIP_TEST_EXIT_CODE

# A test file name as the runner uses them: no path separator, ends in .py.
test_file_names = st.text(st.characters(blacklist_characters='/\x00', blacklist_categories=('Cs',)),
                          min_size=1, max_size=20).map(lambda stem: stem + '.py')
suites = st.sampled_from(SUITES)
# A list element for parse_csv_values: non-empty, no comma, no surrounding whitespace.
csv_items = st.text(st.characters(blacklist_characters=',', blacklist_categories=('Cs',)),
                    min_size=1, max_size=15).filter(lambda item: item == item.strip())
whitespace = st.text(' \t', max_size=3)


def quiet():
    return mock.patch('sys.stdout', new_callable=io.StringIO)


class TestParseCsvValues(unittest.TestCase):

    @given(st.lists(csv_items, max_size=8))
    def test_joined_items_parse_back(self, items):
        self.assertEqual(qa_framework.parse_csv_values(','.join(items)), items)

    @given(st.lists(st.tuples(whitespace, csv_items, whitespace), max_size=8),
           st.lists(whitespace, max_size=3))
    def test_padding_and_empty_entries_are_ignored(self, padded_items, empty_entries):
        value = ','.join([before + item + after for before, item, after in padded_items] + empty_entries)
        self.assertEqual(qa_framework.parse_csv_values(value), [item for _, item, _ in padded_items])

    @given(st.one_of(st.none(), st.just('')))
    def test_missing_value_gives_empty_list(self, value):
        self.assertEqual(qa_framework.parse_csv_values(value), [])

    @given(st.text(max_size=40))
    def test_items_are_never_empty_or_padded(self, value):
        for item in qa_framework.parse_csv_values(value):
            self.assertTrue(item)
            self.assertEqual(item, item.strip())
            self.assertNotIn(',', item)


class TestGetQualifiedTest(unittest.TestCase):

    @given(suites, test_file_names)
    def test_suite_qualified_name_splits_into_suite_and_file(self, suite, name):
        self.assertEqual(qa_framework.get_qualified_test(suite + '.' + name), (suite, name))

    @given(st.text(st.characters(blacklist_characters='\x00', blacklist_categories=('Cs',)), max_size=30))
    def test_name_without_suite_prefix_is_unqualified(self, name):
        assume(not any(name.startswith(suite + '.') for suite in SUITES))
        self.assertEqual(qa_framework.get_qualified_test(name), (None, os.path.basename(name)))

    @given(suites, st.text(max_size=20).filter(lambda name: not name.endswith('.py')))
    def test_suite_prefix_without_py_is_unqualified(self, suite, name):
        test = suite + '.' + name
        self.assertEqual(qa_framework.get_qualified_test(test), (None, os.path.basename(test)))


# disabled.list is read line by line (universal newlines) and each line is
# strip()ped, so a name it can hold has no \r or \n and no surrounding whitespace;
# a line starting with "#" is a comment (README), so no entry can start with one.
listable_names = test_file_names.filter(lambda name: '\r' not in name and '\n' not in name
                                        and name == name.strip() and not name.startswith('#'))
# Entries for disabled.list: qualified ("suite.file.py"), plain ("file.py"),
# comments, blank lines and non-.py names, with optional surrounding spaces.
qualified_entries = st.tuples(suites, listable_names)
plain_entries = listable_names.filter(lambda name: not any(name.startswith(s + '.') for s in SUITES))
ignored_lines = st.one_of(st.just(''), st.text(' \t', max_size=3),
                          st.text(max_size=15).map(lambda text: '#' + text.replace('\n', ' ')),
                          st.text(max_size=15).filter(lambda text: '\n' not in text and '\r' not in text
                                                      and not text.strip().endswith('.py')
                                                      and not text.strip().startswith('#')))


def write_disabled_list(directory, lines):
    with open(os.path.join(directory, 'disabled.list'), 'w', newline='\n') as handle:
        handle.write('\n'.join(lines) + '\n')


class TestDisabledTests(unittest.TestCase):

    @given(st.lists(qualified_entries, max_size=5), st.lists(plain_entries, max_size=5),
           st.lists(ignored_lines, max_size=5), st.randoms(), whitespace, whitespace)
    def test_disabled_list_entries_are_classified(self, qualified, plain, ignored, rnd, before, after):
        lines = [before + suite + '.' + name + after for suite, name in qualified]
        lines += [before + name + after for name in plain] + ignored
        rnd.shuffle(lines)
        with tempfile.TemporaryDirectory() as directory:
            write_disabled_list(directory, lines)
            result = qa_framework.get_disabled_tests(directory)
        self.assertEqual(result, (set(qualified), set(plain)))

    def test_missing_disabled_list_disables_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(qa_framework.get_disabled_tests(directory), (set(), set()))

    @given(st.lists(st.tuples(suites, test_file_names), max_size=10),
           st.sets(qualified_entries, max_size=5), st.sets(plain_entries, max_size=5))
    def test_filter_removes_exactly_the_disabled_runs_in_order(self, runs, qualified, plain):
        test_runs = [('/repo/suite/' + suite + '/' + name, suite) for suite, name in runs]
        with quiet():
            kept = qa_framework.filter_disabled_tests(test_runs, (qualified, plain), io.StringIO())
        expected = [run for run, (suite, name) in zip(test_runs, runs)
                    if name not in plain and (suite, name) not in qualified]
        self.assertEqual(kept, expected)

    @given(suites, suites, listable_names)
    def test_qualified_entry_disables_the_test_only_in_its_suite(self, disabled_suite, run_suite, name):
        with tempfile.TemporaryDirectory() as directory:
            write_disabled_list(directory, [disabled_suite + '.' + name])
            disabled = qa_framework.get_disabled_tests(directory)
        run = ('/repo/suite/' + run_suite + '/' + name, run_suite)
        with quiet():
            kept = qa_framework.filter_disabled_tests([run], disabled, io.StringIO())
        self.assertEqual(kept == [], disabled_suite == run_suite)


class TestDisabledListEncodingProperties(unittest.TestCase):

    @given(st.lists(st.one_of(qualified_entries.map(lambda e: e[0] + '.' + e[1]), plain_entries),
                    min_size=1, max_size=5))
    def test_byte_order_mark_does_not_change_the_result(self, entries):
        with tempfile.TemporaryDirectory() as plain_dir, tempfile.TemporaryDirectory() as bom_dir:
            content = '\n'.join(entries) + '\n'
            with open(os.path.join(plain_dir, 'disabled.list'), 'w', encoding='utf-8') as handle:
                handle.write(content)
            with open(os.path.join(bom_dir, 'disabled.list'), 'w', encoding='utf-8-sig') as handle:
                handle.write(content)
            self.assertEqual(qa_framework.get_disabled_tests(bom_dir), qa_framework.get_disabled_tests(plain_dir))


class TestFailedLogArchiveProperties(unittest.TestCase):

    @given(suites, st.text(st.characters(blacklist_characters='\x00/', blacklist_categories=('Cs',)),
                           min_size=1, max_size=15).map(lambda stem: stem + '.py'))
    def test_tar_command_names_the_archive_literally(self, suite, name):
        import shlex
        with mock.patch.object(qa_framework, 'print_failed_test_log'), \
                mock.patch.object(qa_framework.os, 'system') as system, quiet():
            qa_framework.handle_test_result(io.StringIO(), 0, suite, name, 1)
        argv = shlex.split(system.call_args[0][0])
        workdir = qa_framework.get_worker_thread_dir(0)
        self.assertEqual(argv[:3], ['tar', '-czf', workdir + '/failed_logs/' + suite + '_' + name + '.tar.gz'])


class TestSuiteConstants(unittest.TestCase):

    def test_suite_names_are_unique_and_prefix_free(self):
        self.assertEqual(len(SUITES), len(set(SUITES)))
        for a in SUITES:
            for b in SUITES:
                if a != b:
                    self.assertFalse((b + '.').startswith(a + '.'), (a, b))

    def test_default_suites_are_known_suites(self):
        self.assertLessEqual(set(qa_framework.DEFAULT_SUITES), set(SUITES))
        self.assertLessEqual(set(qa_framework.EXCLUDED_DEFAULT_SUITES), set(SUITES))
        self.assertFalse(set(qa_framework.DEFAULT_SUITES) & set(qa_framework.EXCLUDED_DEFAULT_SUITES))

    def test_skip_exit_code_is_a_real_exit_status(self):
        self.assertTrue(1 <= SKIP <= 255)


class TestSplitGlobalTests(unittest.TestCase):

    @given(st.lists(st.tuples(suites, st.one_of(test_file_names,
                                                st.sampled_from(qa_framework.GLOBAL_MANIFEST_AND_CONFIG_TESTS))),
                    max_size=10))
    def test_split_is_an_order_preserving_partition(self, runs):
        test_runs = [('/repo/suite/' + suite + '/' + name, suite) for suite, name in runs]
        with quiet():
            regular, deferred = qa_framework.split_global_manifest_and_config_tests(test_runs, io.StringIO())
        is_global = [name in qa_framework.GLOBAL_MANIFEST_AND_CONFIG_TESTS for _, name in runs]
        self.assertEqual(regular, [run for run, glob in zip(test_runs, is_global) if not glob])
        self.assertEqual(deferred, [run for run, glob in zip(test_runs, is_global) if glob])


def parse_hms(text):
    hours, minutes, seconds = text.split(':')
    return int(hours), int(minutes), int(seconds)


class TestFormatDuration(unittest.TestCase):

    # Durations come from time.monotonic() differences, so they are >= 0.
    durations = st.floats(min_value=0, max_value=10 ** 7, allow_nan=False)

    @given(durations)
    def test_formatted_value_is_the_duration_rounded_to_a_second(self, duration):
        hours, minutes, seconds = parse_hms(qa_framework.format_duration(duration))
        self.assertLessEqual(abs(hours * 3600 + minutes * 60 + seconds - duration), 0.5)

    @given(durations)
    def test_minutes_and_seconds_are_two_digit_fields_below_60(self, duration):
        text = qa_framework.format_duration(duration)
        _, minutes, seconds = text.split(':')
        self.assertEqual((len(minutes), len(seconds)), (2, 2))
        self.assertTrue(0 <= int(minutes) < 60 and 0 <= int(seconds) < 60)
        self.assertGreaterEqual(len(text.split(':')[0]), 2)


class TestHandleTestResult(unittest.TestCase):

    findings = st.dictionaries(st.text(min_size=1, max_size=10),
                               st.lists(st.tuples(st.integers(1, 1000), st.just('assertion failure'),
                                                  st.text(max_size=20)), min_size=1, max_size=3),
                               max_size=2)
    pids = st.lists(st.integers(1, 10 ** 6), max_size=3)
    results = st.one_of(st.sampled_from([0, SKIP, 1]), st.integers(-255, 255))

    def handle(self, result, crash_findings, killed, surviving):
        with mock.patch.object(qa_framework, 'print_failed_test_log'), \
                mock.patch.object(qa_framework.os, 'system'), quiet() as out:
            failed = qa_framework.handle_test_result(io.StringIO(), 0, 'correctness', 'x.py', result, 1,
                                                     crash_findings, killed, surviving)
        return failed, out.getvalue()

    @given(results, findings, pids, pids)
    def test_status_label_follows_exit_code_crashes_and_survivors(self, result, crash_findings, killed, surviving):
        _, out = self.handle(result, crash_findings, killed, surviving)
        if crash_findings or surviving or result not in (0, SKIP):
            expected = '[Fail]'
        else:
            expected = '[Pass]' if result == 0 else '[Skipped]'
        self.assertIn(expected, out.splitlines()[0])

    @given(results, findings, pids, pids)
    def test_return_value_is_true_exactly_for_failures(self, result, crash_findings, killed, surviving):
        failed, out = self.handle(result, crash_findings, killed, surviving)
        self.assertEqual(failed, '[Fail]' in out.splitlines()[0])


if __name__ == '__main__':
    unittest.main()
