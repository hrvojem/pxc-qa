#!/usr/bin/env python3
""" Random mysqld option combinations against a PXC cluster, driven by Hypothesis.

Hypothesis draws combinations of options from conf/mysql_options_pxc80.txt
and, for each one, starts a cluster with them, loads data and shuts it down.
Every combination must satisfy:

  1. No node crashes: no crash marker (signal, assertion, sanitizer report,
     corruption, Galera inconsistency) in any node's error log, whether
     mysqld accepts or refuses the options, during startup or the data load.
  2. If the whole cluster starts, every node ends with the same data
     (CHECKSUM TABLE of every table matches node1).
  3. Every node that started shuts down within the shutdown timeout.
  4. If the bootstrap node (node1) starts with the options, the other nodes
     can join it: an option mysqld accepts must not leave a cluster that can
     never add a node.

Options that mysqld refuses cleanly at startup (node1 never starts), or under
which the data load fails cleanly (read_only, for example), are recorded but
are not failures; the first [ERROR] line from the node logs is printed as the
reason. A start that
fails because of the host itself (no disk/tmpfs space, no memory) stops the
run instead, since it says nothing about the options.

When a combination fails, Hypothesis shrinks it to the smallest combination
that still fails and prints it. The failing example is saved in .hypothesis/
in the repository and is tried first on the next run.

Environment variables:
  PXC_OPTION_EXAMPLES  number of combinations to try (default 10)
  PXC_OPTION_MAX       most options in one combination (default 6)
  PXC_OPTION_SEED      fixed seed, to repeat a run exactly
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import time

cwd = os.path.dirname(os.path.realpath(__file__))
parent_dir = os.path.normpath(os.path.join(cwd, '../../'))
sys.path.insert(0, parent_dir)
from base_test import *
from config import *
from util import executesql, error_log, resources
from util import sysbench_run
from util import utility
from util.utility import Utility

from hypothesis import given, note, seed, settings, strategies as st, HealthCheck
from hypothesis.database import DirectoryBasedExampleDatabase

OPTIONS_FILE = parent_dir + '/conf/mysql_options_pxc80.txt'

# Options left out of the draw, and why. Everything else in the file is fair game.
EXCLUDED_OPTIONS = {
    # mysqld prints something and exits, or runs a one-off task and exits.
    'help': 'prints help and exits',
    'verbose': 'only meaningful with --help',
    'validate-config': 'validates the configuration and exits',
    'wsrep-recover': 'recovers the wsrep position and exits',
    'tc-heuristic-recover': 'runs transaction-coordinator recovery and exits',
    # Changes how the process runs in ways the harness relies on.
    'daemonize': 'forks into the background; the harness tracks the launched process',
    'gdb': 'changes signal handling for an attached debugger',
    # Switch off the consistency Galera guarantees, so property 2 wouldn't hold.
    'wsrep-on': 'wsrep_on=OFF stops replicating writes',
    'wsrep-ignore-apply-errors': 'documented to allow nodes to diverge on apply errors',
    'pxc_strict_mode': 'modes other than the default ENFORCING allow unreplicable features',
    # Deliberate crashes in debug builds; a release build rejects them anyway.
    'innodb-force-recovery-crash': 'debug-build option that crashes on purpose',
    'sporadic-binlog-dump-fail': 'debug-build fault injection',
}
# Known issues: option values excluded until the bug they trigger is fixed, so the
# test keeps looking for new problems. Each one fails property 4 on PXC 8.4.8 and
# 8.4.11-12 (the latest package as of 2026-11-25).
KNOWN_ISSUES = {
    # xtrabackup applies the server's character_set_filesystem to its own paths,
    # so as SST donor it fails with "cannot my_setwd" and a garbled datadir path,
    # and no node can ever join. mysqld itself starts with these values.
    ('character-set-filesystem', 'utf16'): 'SST donor fails (xtrabackup cannot my_setwd)',
    ('character-set-filesystem', 'utf16le'): 'SST donor fails (xtrabackup cannot my_setwd)',
    ('character-set-filesystem', 'ucs2'): 'SST donor fails (xtrabackup cannot my_setwd)',
    ('character-set-filesystem', 'utf32'): 'SST donor fails (xtrabackup cannot my_setwd)',
}
# Values above this are skipped: on the 15 GB test hosts, with three nodes and the
# workdir in /dev/shm, one such value (buffer pool, redo log, mmap size) alone
# exhausts memory or the tmpfs, which tests the host rather than PXC.
MAX_VALUE_BYTES = 2 * 1024 ** 3

# Free space the workdir's filesystem needs for three fresh nodes and the data load.
MIN_FREE_BYTES = 2 * 1024 ** 3
# Startup errors caused by the host, not by the options being tested.
HOST_PROBLEMS = ('No space left on device', 'exceeds available storage space', 'Cannot allocate memory',
                 'Out of memory', 'Errcode: 28')

EXAMPLES = int(os.environ.get('PXC_OPTION_EXAMPLES', '10'))
MAX_OPTIONS = int(os.environ.get('PXC_OPTION_MAX', '6'))


def option_key(name):
    """ mysqld treats "-" and "_" in option names alike. """
    return name.replace('_', '-').lower()


def supported_options(basedir):
    """ Option names the mysqld in `basedir` accepts, from `mysqld --verbose --help`,
        or None if it can't be asked. The options file covers older versions too,
        and an option this mysqld doesn't know only gets the combination refused.
    """
    try:
        output = subprocess.run([os.path.join(basedir, 'bin', 'mysqld'), '--no-defaults', '--verbose', '--help'],
                                capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    names = {option_key(name) for name in re.findall(r'^  --([A-Za-z0-9_-]+)', output, re.MULTILINE)}
    return names or None


def load_option_values(path, supported=None):
    """ [(option, value)] from the options file, minus excluded options,
        debug-build options, options the mysqld under test doesn't know (when
        `supported` is given) and values too large for the test host.
    """
    pairs = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            option, value = line.split('=', 1)
            if option in EXCLUDED_OPTIONS or 'debug' in option:
                continue
            if supported is not None and option_key(option) not in supported:
                continue
            if (option, value) in KNOWN_ISSUES:
                continue
            size = resources.parse_size(value)
            if size is not None and size > MAX_VALUE_BYTES:
                continue
            pairs.append((option, value))
    return pairs


SUPPORTED_OPTIONS = supported_options(BASEDIR)
OPTION_VALUES = load_option_values(OPTIONS_FILE, SUPPORTED_OPTIONS)

# A combination: distinct options, each with one of its listed values. Drawn as a
# list of (option, value) pairs so Hypothesis shrinks by dropping whole options.
option_combinations = st.lists(st.sampled_from(OPTION_VALUES), min_size=1, max_size=MAX_OPTIONS,
                               unique_by=lambda pair: pair[0]).map(dict)

# Every combination tried, with its outcome, for the summary at the end.
tried = []


class RandomMySQLDOptionQA(BaseTest):
    def __init__(self):
        super().__init__(my_extra='--max-connections=1500')

    def data_load(self):
        # Sysbench data load
        sysbench = sysbench_run.SysbenchRun(self.node1, debug, workdir)
        sysbench.test_sanity_check(db)
        sysbench.test_sysbench_load(db, 10, 10, SYSBENCH_NORMAL_TABLE_SIZE)

        # Add prepared statement SQLs
        self.node1.execute_queries_from_file(parent_dir + '/util/prepared_statements.sql')

        # Random data load
        if os.path.isfile(parent_dir + '/util/executesql.py'):
            execute_sql = executesql.GenerateSQL(self.node1, db, 1000)
            execute_sql.create_table()


class HostProblem(Exception):
    """The test host can't run the cluster; the options were never really tried."""


def check_free_space():
    free = shutil.disk_usage(workdir).free
    if free < MIN_FREE_BYTES:
        raise HostProblem('only %.1f GiB free in %s, need %.1f GiB' % (free / 1024 ** 3, workdir,
                                                                       MIN_FREE_BYTES / 1024 ** 3))


# [ERROR] lines that frame or follow the real message (SST script banners) or are routine.
UNINFORMATIVE_ERROR = re.compile(r'^\*+$|FATAL ERROR|^Line \d+$|^Cleanup after exit|mysqlx', re.IGNORECASE)


def startup_error(log_dir):
    """ The first informative [ERROR] line in the node logs, as "nodeN: message", or None. """
    for path in sorted(glob.glob(os.path.join(glob.escape(log_dir), 'node*.err'))):
        with open(path, errors='replace') as handle:
            for line in handle:
                if '[ERROR]' not in line:
                    continue
                message = line.split('] ')[-1].strip()
                if message and not UNINFORMATIVE_ERROR.search(message):
                    return os.path.basename(path)[:-len('.err')] + ': ' + message[:160]
    return None


def bootstrap_node_started(log_dir):
    """ Whether node1's mysqld got as far as accepting connections. """
    try:
        with open(os.path.join(log_dir, 'node1.err'), errors='replace') as handle:
            return any('mysqld: ready for connections' in line for line in handle)
    except OSError:
        return False


def node_differences(nodes, attempts=6, retry_wait=5):
    """ Checksum differences between node1 and every other node, re-checked a
        few times while the other nodes apply the last write-sets.
    """
    differences = []
    for attempt in range(attempts):
        reference = Utility.table_checksums(nodes[0], db)
        differences = []
        for node in nodes[1:]:
            name = 'node' + str(node.get_node_number())
            differences += Utility.checksum_mismatches(reference, Utility.table_checksums(node, db), db,
                                                       'node1', name)
        if not differences:
            break
        time.sleep(retry_wait)
    return differences


def run_combination(options):
    """ Start a cluster with `options`, load data, compare nodes and shut down.
        Returns (outcome, crash findings, node differences, unclean shutdown,
        joiner failed after node1 accepted the options).
    """
    log_dir = workdir + '/log'
    check_free_space()
    error_log.clear_error_logs(log_dir)
    qa = RandomMySQLDOptionQA()
    started = False
    outcome = 'refused at startup'
    differences = []
    unclean_shutdown = False
    try:
        try:
            qa.start_pxc(custom_conf_settings=options, terminate_on_startup_failure=False)
            started = qa.pxc_nodes is not None and len(qa.pxc_nodes) == qa.get_number_of_nodes()
        except SystemExit:
            started = False  # a startup check gave up; the failed-start cleanup stopped the nodes
        if started:
            try:
                qa.data_load()
                outcome = 'started, data loaded'
            except SystemExit:
                outcome = 'started, data load failed'
            differences = node_differences(qa.pxc_nodes)
    finally:
        if started:
            unclean_shutdown = qa.shutdown_nodes(terminate_on_failure=False) != 0
    joiner_failed = False
    if not started:
        reason = startup_error(log_dir) or 'no [ERROR] line in the node logs'
        if any(problem in reason for problem in HOST_PROBLEMS):
            raise HostProblem('cluster could not start on this host: ' + reason)
        if bootstrap_node_started(log_dir):
            joiner_failed = True
            outcome = 'node1 started but a node could not join'
        outcome += ': ' + reason
    # Scan only after the nodes have exited, so their logs are complete.
    crashes = error_log.scan_error_logs(log_dir)
    return outcome, crashes, differences, unclean_shutdown, joiner_failed


def check_settings():
    return settings(max_examples=EXAMPLES, deadline=None, print_blob=True,
                    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
                    database=DirectoryBasedExampleDatabase(os.path.join(parent_dir, '.hypothesis')))


@check_settings()
@given(option_combinations)
def cluster_survives_option_combination(options):
    print('Trying options: ' + ', '.join(name + '=' + value for name, value in sorted(options.items())))
    outcome, crashes, differences, unclean_shutdown, joiner_failed = run_combination(options)
    tried.append((options, outcome.split(':')[0], bool(crashes or differences or unclean_shutdown
                                                       or joiner_failed)))
    print('  -> ' + outcome)
    note('outcome: ' + outcome)
    assert not crashes, 'node crashed:\n' + '\n'.join(error_log.format_findings(crashes))
    assert not differences, 'nodes differ after the data load:\n' + '\n'.join(differences)
    assert not unclean_shutdown, 'a node had to be killed at shutdown'
    assert not joiner_failed, outcome


if 'PXC_OPTION_SEED' in os.environ:
    cluster_survives_option_combination = seed(int(os.environ['PXC_OPTION_SEED']))(
        cluster_survives_option_combination)


utility.test_header("PXC random mysqld option combinations (Hypothesis)")
print('Drawing up to ' + str(MAX_OPTIONS) + ' options per combination from ' + str(len(OPTION_VALUES))
      + ' option values (' + ('options this mysqld supports' if SUPPORTED_OPTIONS else
                              'mysqld --help unavailable, all listed options') + '); '
      + str(EXAMPLES) + ' combinations')
failure = None
try:
    check_free_space()
    cluster_survives_option_combination()
except AssertionError as error:
    failure = error
except HostProblem as problem:
    print('Host problem, stopping: ' + str(problem))
    utility_cmd.check_testcase(1, "Random mysqld option combinations: host can't run the cluster")
finally:
    counts = {}
    for _, outcome, _ in tried:
        counts[outcome] = counts.get(outcome, 0) + 1
    print('Combinations tried (including shrinking): ' + str(len(tried)) + ' '
          + str(dict(sorted(counts.items()))))

if failure is not None:
    # Hypothesis attaches its report (the shrunk "Falsifying example", notes and the
    # @reproduce_failure blob) to the exception as __notes__ rather than printing it.
    print('Failure: ' + str(failure))
    for report_line in getattr(failure, '__notes__', []):
        print(report_line)
    utility_cmd.check_testcase(1, "Random mysqld option combinations: " + str(failure).splitlines()[0])
utility_cmd.check_testcase(0, "Random mysqld option combinations")
