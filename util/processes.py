"""Find running mysqld processes by the --defaults-file they were started with.

Kept free of MySQL and config imports so the test runner can use it too.
"""
import os
import subprocess


def mysqld_processes():
    """Return [(pid, defaults_file)] for running mysqld processes that were
    started with --defaults-file, with the path normalized (some callers build
    it with doubled slashes). Returns None if processes can't be listed.
    """
    try:
        output = subprocess.run(['pgrep', '-a', 'mysqld'], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return None
    processes = []
    for line in output.splitlines():
        fields = line.split()
        for arg in fields[1:]:
            if arg.startswith('--defaults-file='):
                processes.append((int(fields[0]), os.path.normpath(arg[len('--defaults-file='):])))
                break
    return processes


def mysqld_running(conf_file):
    """True if a mysqld started with `conf_file` is running, False if not,
    None if processes can't be listed."""
    processes = mysqld_processes()
    if processes is None:
        return None
    conf_file = os.path.normpath(conf_file)
    return any(defaults_file == conf_file for _, defaults_file in processes)


def tail_lines(path, count=20):
    """Last `count` lines of a text file, or [] if it can't be read."""
    try:
        with open(path, 'r', errors='replace') as handle:
            return [line.rstrip('\n') for line in handle.readlines()[-count:]]
    except OSError:
        return []
