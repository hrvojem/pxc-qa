"""Unit tests for Utility.test_table_count that need no running server.

Run from the repository root (config.py reads ./config.ini):
    python3 test/test_table_checksum.py
(`python3 -m unittest test.…` can pick up Python's own `test` package instead.)
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

from util.utility import Utility  # noqa: E402


class FakeNode:
    """Answers SHOW TABLES and CHECKSUM TABLE like a server would."""

    def __init__(self, checksums, node_number=0):
        self.checksums = checksums
        self.node_number = node_number

    def get_node_number(self):
        return self.node_number

    def execute_get_values(self, query):
        if query.lower().startswith('show full tables in'):
            assert query.endswith("where Table_type = 'BASE TABLE'"), query
            return [(table, 'BASE TABLE') for table in self.checksums]
        if query.lower().startswith('checksum table'):
            table = query.split('.')[-1].strip('`')
            db = query.split()[2].split('.')[0].strip('`')
            return [(db + '.' + table, self.checksums.get(table))]
        raise AssertionError('unexpected query: ' + query)

    def execute_get_value(self, query, retries=0, retry_wait=5):
        # Mirrors DbConnection.execute_get_value: first column of the first row.
        return self.execute_get_values(query)[0][0]


class TestTableCount(unittest.TestCase):

    def run_check(self, checksums1, checksums2):
        utility = Utility('NO')
        with mock.patch('util.utility.time.sleep'), \
                mock.patch.object(Utility, 'check_testcase') as check_testcase:
            utility.test_table_count(FakeNode(checksums1, 1), FakeNode(checksums2, 2), 'test')
        return check_testcase.call_args[0][0]

    def test_identical_data_passes(self):
        self.assertEqual(self.run_check({'t1': 111, 't2': 222}, {'t1': 111, 't2': 222}), 0)

    def test_different_checksum_fails(self):
        # The old implementation compared CHECKSUM TABLE's first column (the
        # table name), so this case passed even though the data differed.
        self.assertEqual(self.run_check({'t1': 111, 't2': 222}, {'t1': 111, 't2': 999}), 1)

    def test_table_missing_on_node2_fails(self):
        self.assertEqual(self.run_check({'t1': 111, 't2': 222}, {'t1': 111}), 1)

    def test_table_only_on_node2_fails(self):
        # The old implementation only listed node1's tables, so this passed.
        self.assertEqual(self.run_check({'t1': 111}, {'t1': 111, 't2': 222}), 1)

    def test_checksum_unavailable_on_both_nodes_fails(self):
        # NULL == NULL must not count as matching data.
        self.assertEqual(self.run_check({'t1': None}, {'t1': None}), 1)

    def test_checksum_unavailable_on_one_node_fails(self):
        self.assertEqual(self.run_check({'t1': 111}, {'t1': None}), 1)

    def test_nonpositive_attempts_rejected(self):
        utility = Utility('NO')
        for attempts in (0, -1):
            with self.subTest(attempts=attempts), \
                    mock.patch.object(Utility, 'check_testcase') as check_testcase:
                with self.assertRaises(ValueError):
                    utility.test_table_count(FakeNode({'t1': 1}, 1), FakeNode({'t1': 2}, 2), 'test',
                                             attempts=attempts)
                check_testcase.assert_not_called()

    def test_message_names_the_nodes_compared(self):
        utility = Utility('NO')
        with mock.patch('util.utility.time.sleep'), \
                mock.patch.object(Utility, 'check_testcase'), \
                mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            utility.test_table_count(FakeNode({'t1': 1}, 1), FakeNode({'t1': 1, 't2': 2}, 3), 'test')
        self.assertIn('Table(test.t2) exists only on node3', out.getvalue())

    def test_mismatch_that_resolves_on_recheck_passes(self):
        node1 = FakeNode({'t1': 111}, 1)
        node2 = FakeNode({'t1': 0}, 2)
        calls = []

        def catch_up(_seconds):
            calls.append(_seconds)
            node2.checksums['t1'] = 111

        utility = Utility('NO')
        with mock.patch('util.utility.time.sleep', side_effect=catch_up), \
                mock.patch.object(Utility, 'check_testcase') as check_testcase:
            utility.test_table_count(node1, node2, 'test')
        self.assertEqual(check_testcase.call_args[0][0], 0)
        self.assertEqual(len(calls), 1)


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# ---------------------------------------------------------------------------
import pbt_support  # noqa: E402,F401  (Hypothesis profile)
from hypothesis import given, strategies as st  # noqa: E402

# checksum_mismatches() writes "Table(<db>.<table>) ..." without escaping, so the
# oracle can only read a table name back if it has no ")" or space.
table_names = st.text(st.characters(blacklist_characters=') ', blacklist_categories=('Cs',)),
                      min_size=1, max_size=12)
checksums = st.one_of(st.none(), st.integers(0, 2 ** 32 - 1))
checksum_maps = st.dictionaries(table_names, checksums, max_size=6)


def differing_tables(checksums1, checksums2):
    """Tables the contract says must be reported: on one node only, checksum
    unavailable on either node, or different checksums."""
    return {table for table in set(checksums1) | set(checksums2)
            if table not in checksums1 or table not in checksums2
            or checksums1[table] is None or checksums2[table] is None
            or checksums1[table] != checksums2[table]}


def reported_table(message, db):
    prefix = 'Table(' + db + '.'
    assert message.startswith(prefix), message
    return message[len(prefix):message.index(') ', len(prefix))]


class TestChecksumMismatchProperties(unittest.TestCase):

    @given(checksum_maps, checksum_maps)
    def test_every_differing_table_is_reported_once(self, checksums1, checksums2):
        messages = Utility.checksum_mismatches(checksums1, checksums2, 'db')
        self.assertEqual(sorted(reported_table(m, 'db') for m in messages),
                         sorted(differing_tables(checksums1, checksums2)))

    @given(st.dictionaries(table_names, st.integers(0, 2 ** 32 - 1), max_size=6))
    def test_identical_available_checksums_report_nothing(self, checksums):
        self.assertEqual(Utility.checksum_mismatches(checksums, dict(checksums), 'db'), [])

    @given(checksum_maps.filter(lambda c: None in c.values()))
    def test_unavailable_checksum_never_matches_itself(self, checksums):
        messages = Utility.checksum_mismatches(checksums, dict(checksums), 'db')
        unavailable = {t for t, v in checksums.items() if v is None}
        self.assertEqual({reported_table(m, 'db') for m in messages}, unavailable)

    @given(checksum_maps, checksum_maps)
    def test_swapping_nodes_reports_the_same_tables(self, checksums1, checksums2):
        forward = Utility.checksum_mismatches(checksums1, checksums2, 'db', 'nodeA', 'nodeB')
        backward = Utility.checksum_mismatches(checksums2, checksums1, 'db', 'nodeB', 'nodeA')
        self.assertEqual(sorted(reported_table(m, 'db') for m in forward),
                         sorted(reported_table(m, 'db') for m in backward))

    @given(checksum_maps, checksum_maps)
    def test_one_sided_table_names_the_node_that_has_it(self, checksums1, checksums2):
        for message in Utility.checksum_mismatches(checksums1, checksums2, 'db', 'nodeA', 'nodeB'):
            table = reported_table(message, 'db')
            if table not in checksums2:
                self.assertTrue(message.endswith('exists only on nodeA'), message)
            elif table not in checksums1:
                self.assertTrue(message.endswith('exists only on nodeB'), message)


def parse_quoted_identifiers(sql):
    """The backtick-quoted identifiers in `sql`, with doubled backticks undone."""
    names, i = [], 0
    while True:
        start = sql.find('`', i)
        if start < 0:
            return names
        j, name = start + 1, ''
        while True:
            end = sql.index('`', j)
            name += sql[j:end]
            if sql[end + 1:end + 2] == '`':
                name, j = name + '`', end + 2
            else:
                break
        names.append(name)
        i = end + 1


class RecordingNode:
    """Answers like a server from a {table: checksum} map, recording each query."""

    def __init__(self, checksums):
        self.checksums = checksums
        self.queries = []

    def execute_get_values(self, query):
        self.queries.append(query)
        if query.lower().startswith('show full tables in'):
            return [(table, 'BASE TABLE') for table in self.checksums]
        db, table = parse_quoted_identifiers(query)
        return [(db + '.' + table, self.checksums[table])]


identifiers = st.text(st.characters(blacklist_characters='\x00', blacklist_categories=('Cs',)), min_size=1,
                      max_size=10)


class TestIdentifierQuotingProperties(unittest.TestCase):

    @given(identifiers, st.dictionaries(identifiers, st.integers(0, 2 ** 32 - 1), max_size=5))
    def test_queries_name_the_exact_database_and_tables(self, db, checksums):
        node = RecordingNode(checksums)
        self.assertEqual(Utility.table_checksums(node, db), checksums)
        self.assertEqual(parse_quoted_identifiers(node.queries[0]), [db])
        self.assertEqual([parse_quoted_identifiers(q) for q in node.queries[1:]],
                         [[db, table] for table in checksums])


class TestDatabaseNameProperties(unittest.TestCase):

    @given(table_names, checksum_maps, checksum_maps)
    def test_reports_use_the_given_database_name(self, db, checksums1, checksums2):
        for message in Utility.checksum_mismatches(checksums1, checksums2, db):
            self.assertTrue(message.startswith('Table(' + db + '.'), message)


class ConvergingNode(FakeNode):
    """node2 serving stale checksums for its first `stale_rounds` reads."""

    def __init__(self, checksums, node_number, stale_rounds):
        super().__init__({table: value + 1 for table, value in checksums.items()}, node_number)
        self.target = checksums
        self.stale_rounds = stale_rounds
        self.rounds = 0

    def execute_get_values(self, query):
        if query.lower().startswith('show full tables in'):
            self.rounds += 1
            if self.rounds > self.stale_rounds:
                self.checksums = dict(self.target)
        return super().execute_get_values(query)


class TestRetryProperties(unittest.TestCase):

    @given(st.integers(1, 8), st.integers(0, 10), st.integers(0, 30))
    def test_passes_iff_node_catches_up_within_the_attempts(self, attempts, stale_rounds, retry_wait):
        node1 = FakeNode({'t1': 10, 't2': 20}, 1)
        node2 = ConvergingNode({'t1': 10, 't2': 20}, 2, stale_rounds)
        with mock.patch('util.utility.time.sleep') as sleep, \
                mock.patch.object(Utility, 'check_testcase') as check_testcase, \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            Utility('NO').test_table_count(node1, node2, 'test', attempts=attempts, retry_wait=retry_wait)
        self.assertEqual(check_testcase.call_args[0][0], 0 if stale_rounds < attempts else 1)

    @given(st.integers(1, 8), st.integers(0, 10), st.integers(0, 30))
    def test_waits_between_attempts_only(self, attempts, stale_rounds, retry_wait):
        node1 = FakeNode({'t1': 10}, 1)
        node2 = ConvergingNode({'t1': 10}, 2, stale_rounds)
        with mock.patch('util.utility.time.sleep') as sleep, \
                mock.patch.object(Utility, 'check_testcase'), \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            Utility('NO').test_table_count(node1, node2, 'test', attempts=attempts, retry_wait=retry_wait)
        self.assertEqual([c.args for c in sleep.call_args_list], [(retry_wait,)] * min(stale_rounds, attempts - 1))

    @given(st.integers(-100, 0))
    def test_attempts_below_one_query_nothing(self, attempts):
        node1, node2 = mock.Mock(), mock.Mock()
        with self.assertRaises(ValueError):
            Utility('NO').test_table_count(node1, node2, 'test', attempts=attempts)
        node1.execute_get_values.assert_not_called()
        node2.execute_get_values.assert_not_called()


if __name__ == '__main__':
    unittest.main()
