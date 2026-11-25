"""Property-based tests (Hypothesis) for port allocation: find_available_ports()
and the per-worker port band layout used by pxc_allocate_ports() and
ps_allocate_ports(). is_port_busy() is replaced by a generated set of busy
ports, so no sockets are opened.

Layout (util/utility.py): each worker owns a WORKER_PORT_BAND_SIZE-wide band
starting at worker_port_band_base(worker_id); PXC node N uses the
NODE_PORT_STRIDE-wide slot at band_base + (N - 1) * NODE_PORT_STRIDE, PS node
N the slot at band_base + PS_PORT_SUB_BASE + (N - 1) * NODE_PORT_STRIDE.
Parallel workers must never overlap and every port must stay below 65535.

Run from the repository root (config.py reads ./config.ini):
    python3 test/test_ports.py
"""
import unittest
from unittest import mock

import pbt_support  # noqa: F401  (sys.path, mysql stub, Hypothesis profile)
from hypothesis import given, strategies as st

from util import utility, pxc_startup, ps_startup  # noqa: E402

BAND = utility.WORKER_PORT_BAND_SIZE
STRIDE = utility.NODE_PORT_STRIDE
PS_SUB = utility.PS_PORT_SUB_BASE
MAX_PXC_NODES = PS_SUB // STRIDE              # PXC slots must end before the PS sub-band
MAX_PS_NODES = (BAND - PS_SUB) // STRIDE      # PS slots must end before the next worker's band
MAX_WORKER = (65535 - utility.PORT_BAND_BASE - BAND + 1) // BAND  # last band ending at or below 65535

workers = st.integers(0, MAX_WORKER)
pxc_nodes = st.integers(1, MAX_PXC_NODES)
ps_nodes = st.integers(1, MAX_PS_NODES)


def busy(ports):
    return mock.patch.object(utility, 'is_port_busy', side_effect=lambda port: port in ports)


def pxc_slot(worker, node):
    with busy(set()):
        ports = pxc_startup.pxc_allocate_ports(worker, node, set_admin_address=True)
    return set(range(ports[0], ports[0] + STRIDE))


def ps_slot(worker, node):
    with busy(set()):
        port = ps_startup.ps_allocate_ports(worker, node)
    return set(range(port, port + STRIDE))


class TestFindAvailablePorts(unittest.TestCase):

    @given(st.integers(1, 60000), st.integers(0, 200), st.integers(1, 10), st.sets(st.integers(1, 60300)))
    def test_returns_the_lowest_free_ports_in_range(self, start, width, count, busy_ports):
        end = start + width
        free = [port for port in range(start, end + 1) if port not in busy_ports]
        with busy(busy_ports):
            if len(free) < count:
                with self.assertRaises(Exception) as raised:
                    utility.find_available_ports(start, end, count)
                self.assertNotIsInstance(raised.exception, ValueError)
            else:
                self.assertEqual(utility.find_available_ports(start, end, count), free[:count])

    @given(st.integers(0, 70000), st.integers(0, 70000), st.integers(-5, 0))
    def test_count_below_one_is_rejected(self, start, end, count):
        with busy(set()), self.assertRaises(ValueError):
            utility.find_available_ports(start, end, count)

    @given(st.integers(1, 70000), st.integers(1, 1000), st.integers(1, 5))
    def test_start_after_end_is_rejected(self, end, gap, count):
        with busy(set()), self.assertRaises(ValueError):
            utility.find_available_ports(end + gap, end, count)


class TestPortLayout(unittest.TestCase):

    @given(workers, pxc_nodes, st.booleans())
    def test_pxc_ports_stay_in_the_nodes_slot(self, worker, node, admin):
        base = utility.worker_port_band_base(worker) + (node - 1) * STRIDE
        with busy(set()):
            ports = pxc_startup.pxc_allocate_ports(worker, node, set_admin_address=admin)
        self.assertEqual(list(ports), list(range(base, base + (5 if admin else 4))))

    @given(workers, ps_nodes)
    def test_ps_port_is_the_first_of_the_nodes_slot(self, worker, node):
        with busy(set()):
            port = ps_startup.ps_allocate_ports(worker, node)
        self.assertEqual(port, utility.worker_port_band_base(worker) + PS_SUB + (node - 1) * STRIDE)

    @given(st.tuples(workers, pxc_nodes), st.tuples(workers, pxc_nodes))
    def test_pxc_slots_of_different_nodes_never_overlap(self, first, second):
        if first != second:
            self.assertFalse(pxc_slot(*first) & pxc_slot(*second))

    @given(workers, pxc_nodes, workers, ps_nodes)
    def test_pxc_and_ps_slots_never_overlap(self, pxc_worker, pxc_node, ps_worker, ps_node):
        self.assertFalse(pxc_slot(pxc_worker, pxc_node) & ps_slot(ps_worker, ps_node))

    @given(workers, st.one_of(pxc_nodes.map(lambda n: ('pxc', n)), ps_nodes.map(lambda n: ('ps', n))))
    def test_every_slot_stays_inside_its_workers_band(self, worker, kind_node):
        kind, node = kind_node
        slot = pxc_slot(worker, node) if kind == 'pxc' else ps_slot(worker, node)
        band_base = utility.worker_port_band_base(worker)
        self.assertTrue(band_base <= min(slot) and max(slot) < band_base + BAND)

    @given(workers, st.one_of(pxc_nodes.map(lambda n: ('pxc', n)), ps_nodes.map(lambda n: ('ps', n))))
    def test_every_port_is_below_65536(self, worker, kind_node):
        kind, node = kind_node
        slot = pxc_slot(worker, node) if kind == 'pxc' else ps_slot(worker, node)
        self.assertLessEqual(max(slot), 65535)

    @given(st.integers(-1000, -1))
    def test_negative_worker_uses_the_first_band(self, worker):
        self.assertEqual(utility.worker_port_band_base(worker), utility.worker_port_band_base(0))

    # Reject direction: a node or worker that doesn't fit the layout must be
    # refused, not given ports inside another node's or worker's range.

    @given(workers, st.integers(MAX_PXC_NODES + 1, 100))
    def test_pxc_node_beyond_the_layout_is_rejected(self, worker, node):
        with busy(set()), self.assertRaises(ValueError):
            pxc_startup.pxc_allocate_ports(worker, node)

    @given(workers, st.integers(MAX_PS_NODES + 1, 100))
    def test_ps_node_beyond_the_layout_is_rejected(self, worker, node):
        with busy(set()), self.assertRaises(ValueError):
            ps_startup.ps_allocate_ports(worker, node)

    @given(workers, st.integers(-100, 0))
    def test_pxc_node_numbers_below_one_are_rejected(self, worker, node):
        with busy(set()), self.assertRaises(ValueError):
            pxc_startup.pxc_allocate_ports(worker, node)

    @given(workers, st.integers(-100, 0))
    def test_ps_node_numbers_below_one_are_rejected(self, worker, node):
        with busy(set()), self.assertRaises(ValueError):
            ps_startup.ps_allocate_ports(worker, node)

    @given(workers, st.one_of(st.floats(1, MAX_PXC_NODES, allow_nan=False).filter(lambda x: x != int(x)),
                              st.sampled_from(['1', None, True, 2.0])))
    def test_non_integer_node_numbers_are_rejected(self, worker, node):
        with busy(set()), self.assertRaises(ValueError):
            pxc_startup.pxc_allocate_ports(worker, node)

    @given(st.integers(MAX_WORKER + 1, 10 ** 6))
    def test_worker_whose_band_passes_65535_is_rejected(self, worker):
        with self.assertRaises(ValueError):
            utility.worker_port_band_base(worker)


if __name__ == '__main__':
    unittest.main()
