"""Find running mysqld processes by the --defaults-file they were started with.

Kept free of MySQL and config imports so the test runner can use it too.
"""
import os
import signal
import subprocess
import time


def normalize_path(path):
    """os.path.normpath, plus collapsing a leading "//" to "/": normpath keeps
    exactly two leading slashes (POSIX leaves their meaning to the system), but
    on Linux "//qa/conf" and "/qa/conf" are the same directory."""
    path = os.path.normpath(path)
    if path.startswith('//'):
        path = '/' + path.lstrip('/')
    return path


def mysqld_processes():
    """Return [(pid, defaults_file)] for running mysqld processes that were
    started with --defaults-file, with the path normalized (some callers build
    it with doubled slashes). Only the mysqld binary itself counts, not
    mysqld_safe or mysqldump given the same file. Returns None if processes
    can't be listed.
    """
    try:
        output = subprocess.run(['pgrep', '-a', '-x', 'mysqld'], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return None
    processes = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2 or os.path.basename(fields[1]) != 'mysqld':
            continue
        for arg in fields[2:]:
            if arg.startswith('--defaults-file='):
                processes.append((int(fields[0]), normalize_path(arg[len('--defaults-file='):])))
                break
    return processes


def mysqld_running(conf_file):
    """True if a mysqld started with `conf_file` is running, False if not,
    None if processes can't be listed."""
    processes = mysqld_processes()
    if processes is None:
        return None
    conf_file = normalize_path(conf_file)
    return any(defaults_file == conf_file for _, defaults_file in processes)


def tail_lines(path, count=20):
    """Last `count` lines of a text file, or [] if it can't be read."""
    if count <= 0:
        return []  # [-0:] would be the whole file
    try:
        with open(path, 'r', errors='replace') as handle:
            return [line.rstrip('\n') for line in handle.readlines()[-count:]]
    except OSError:
        return []


def kill_mysqld_for_confs(conf_files, timeout=30):
    """SIGKILL every running mysqld started with one of `conf_files` and wait
    up to `timeout` seconds for them to exit. Returns the pids signalled."""
    wanted = {normalize_path(conf_file) for conf_file in conf_files}
    found = mysqld_processes() or []
    pids = []
    for pid in [pid for pid, conf_file in found if conf_file in wanted]:
        try:
            os.kill(pid, signal.SIGKILL)
            pids.append(pid)
        except ProcessLookupError:
            pass  # already gone; not killed by us
    deadline = time.monotonic() + timeout
    while pids and time.monotonic() < deadline:
        if not any(pid in pids for pid, _ in (mysqld_processes() or [])):
            break
        time.sleep(0.5)
    return pids


def stop_nodes_after_failed_start(conf_files):
    """Kill the servers a failed cluster/server start already launched, so they
    don't outlive the test: the caller never got the node list, so nothing
    else would shut them down."""
    pids = kill_mysqld_for_confs(conf_files)
    if pids:
        print("Startup failed; killed " + str(len(pids)) + " mysqld process(es) it had started: "
              + ", ".join(str(pid) for pid in pids))
