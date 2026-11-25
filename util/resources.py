"""Warn when a test's servers won't fit in the host's memory.

Several suites start every node with a large InnoDB buffer pool (up to 8G).
On a smaller host the kernel's OOM killer then stops a mysqld mid-test, and
the failure shows up as a node that never starts or syncs, which looks like
a PXC bug. A warning up front makes the real cause obvious.
"""
import re

from util.processes import normalize_path

# The suffixes mysqld accepts for size options (8.0.14+ adds T, P and E).
_SIZE_UNITS = {'': 1, 'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4, 'P': 1024 ** 5, 'E': 1024 ** 6}


def parse_size(value):
    """Parse a MySQL size such as 8G, 512M or 134217728 into bytes; None if
    mysqld wouldn't accept it (ASCII digits, then an optional unit, no space
    between them; surrounding whitespace is ignored)."""
    match = re.fullmatch(r'\s*([0-9]+)([KMGTPE]?)\s*', str(value), re.IGNORECASE | re.ASCII)
    if not match:
        return None
    return int(match.group(1)) * _SIZE_UNITS[match.group(2).upper()]


def buffer_pool_bytes(mysqld_options):
    """The innodb_buffer_pool_size given in a mysqld option string, in bytes;
    None if it isn't set. Accepts "_" or "-" in the name, the --loose- prefix
    and a quoted value, as mysqld does. The last one given wins, as for mysqld."""
    sizes = re.findall(r'--(?:loose[_-])?innodb[_-]buffer[_-]pool[_-]size=(\S+)', mysqld_options or '')
    if not sizes:
        return None
    value = sizes[-1]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        value = value[1:-1]
    return parse_size(value)


def mem_available_bytes(meminfo_path='/proc/meminfo'):
    """MemAvailable from /proc/meminfo in bytes; None where it can't be read."""
    try:
        with open(meminfo_path) as meminfo:
            for line in meminfo:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def memory_warning(mysqld_options, number_of_nodes, workdir='', available=None):
    """A warning if the nodes' buffer pools alone exceed available memory, else None."""
    pool = buffer_pool_bytes(mysqld_options)
    if pool is None or number_of_nodes < 1:
        return None
    if available is None:
        available = mem_available_bytes()
    if available is None:
        return None
    needed = pool * number_of_nodes
    if needed <= available:
        return None
    gib = 1024 ** 3
    message = ("WARNING: %d node(s) x %.1f GiB InnoDB buffer pool = %.1f GiB, but only %.1f GiB of memory "
               "is available. Nodes may be killed for lack of memory (check the kernel log for OOM kills)."
               % (number_of_nodes, pool / gib, needed / gib, available / gib))
    if (normalize_path(workdir) + '/').startswith('/dev/shm/'):
        message += " The workdir is in /dev/shm, so the data directories use memory too."
    return message
