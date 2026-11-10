# SPDX-License-Identifier: ISC
import contextlib
import importlib.util
import io
import ipaddress
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('scan_roam', Path(__file__).with_name('scan_roam.py'))
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def run(snapshot):
    events = [{'kind': 'new_attached', 'monotonic_ns': 1000000},
              {'kind': 'snapshot', 'monotonic_ns': 2000000, 'leaf2': snapshot},
              {'kind': 'cleanup', 'monotonic_ns': 3000000}]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        scanner.scan(events, 'leaf2', '00:00:00:00:00:11',
                     ipaddress.ip_address('45.0.0.11'), 'mbb-leaf', {'vni1000'})
    return out.getvalue()


def test_missing_tables_are_unknown():
    fdb = [{'mac': '00:00:00:00:00:11', 'ifname': 'mbb-leaf', 'master': 'br1000'}]
    text = run({'fdb': fdb})
    assert 'FDB: local' in text
    assert 'Neighbor: not captured' in text
    assert 'Routes: not captured' in text
    assert 'SUSPECT' not in text


def test_bridge_neighbor_is_not_a_tunnel():
    fdb = [{'mac': '00:00:00:00:00:11', 'ifname': 'mbb-leaf', 'master': 'br1000'}]
    neighbor = {'dst': '45.0.0.11', 'dev': 'br1000', 'extern_learn': None, 'state': ['NOARP']}
    text = run({'fdb': fdb, 'neighbors': [neighbor], 'routes': []})
    assert 'externally learned NOARP neighbor coexist' in text
    assert 'SUSPECT' not in text


def test_explicit_tunnel_neighbor_is_reported():
    fdb = [{'mac': '00:00:00:00:00:11', 'ifname': 'mbb-leaf', 'master': 'br1000'}]
    neighbor = {'dst': '45.0.0.11', 'dev': 'vni1000', 'extern_learn': None, 'state': ['NOARP']}
    assert 'SUSPECT' in run({'fdb': fdb, 'neighbors': [neighbor]})
    with unittest.TestCase().assertRaisesRegex(AssertionError, 'extern_learn NOARP on tunnel vni1000'):
        scanner.validate_local_neighbor({'fdb': fdb, 'neighbors': [neighbor]},
                                        '00:00:00:00:00:11', '45.0.0.11',
                                        'mbb-leaf', {'vni1000'})


def test_concatenated_snapshot_decoding():
    import json
    fdb = [{'mac': '00:00:00:00:00:11', 'ifname': 'mbb-leaf', 'master': 'br1000'}]
    neighbor = [{'dst': '45.0.0.11', 'dev': 'br1000', 'lladdr': '00:00:00:00:00:11', 'state': ['NOARP'], 'extern_learn': None}]
    text = run(json.dumps([{'ifindex': 1, 'ifname': 'mbb-leaf'}]) + '\n' + json.dumps(fdb) + '\n' + json.dumps(neighbor))
    assert 'FDB: local' in text
    assert 'externally learned NOARP neighbor coexist' in text


def test_new_snapshot_contains_l3_and_accepts_bridge_neighbor():
    import json
    fdb = [{'mac': '00:00:00:00:00:11', 'ifname': 'mbb-leaf', 'master': 'br1000'}]
    neighbor = {'dst': '45.0.0.11', 'dev': 'br1000', 'extern_learn': None, 'state': ['NOARP']}
    snapshot = {'raw': json.dumps([{'ifindex': 1, 'ifname': 'mbb-leaf'}]) + '\n' + json.dumps(fdb),
                'neighbors': [neighbor], 'routes': [],
                'sample_start_ns': 1000000, 'sample_end_ns': 2000000}
    text = run(snapshot)
    assert 'Neighbor: not captured' not in text
    assert 'Routes: not captured' not in text
    scanner.validate_local_neighbor(snapshot, '00:00:00:00:00:11',
                                    '45.0.0.11', 'mbb-leaf', {'vni1000'})


def test_validation_fails_if_neighbors_missing():
    fdb = [{'mac': '00:00:00:00:00:11', 'ifname': 'mbb-leaf', 'master': 'br1000'}]
    with unittest.TestCase().assertRaisesRegex(AssertionError, 'not captured'):
        scanner.validate_local_neighbor({'fdb': fdb}, '00:00:00:00:00:11',
                                        '45.0.0.11', 'mbb-leaf', {'vni1000'})
