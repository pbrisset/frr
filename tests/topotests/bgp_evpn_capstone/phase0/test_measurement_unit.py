"""Pure tests, runnable without Mininet/FRR using unittest discovery."""
import unittest
from measurement import forwarding_vteps, loss_summary
from probe import checksum


class Measurements(unittest.TestCase):
    def test_routed_rmac_resolution(self):
        route = [{'gateway': '10.0.0.2', 'dev': 'br500'}]
        neigh = [{'dst': '10.0.0.2', 'dev': 'br500', 'lladdr': 'aa', 'state': ['PERMANENT']}]
        fdb = [{'mac': 'aa', 'ifname': 'vni500', 'dst': '10.0.0.2'},
               {'mac': 'robot', 'dev': 'vni1000', 'dst': '10.0.0.1'}]
        self.assertEqual(forwarding_vteps(route, neigh, fdb), ['10.0.0.2'])
        self.assertEqual(forwarding_vteps(route, [], fdb), [])
        route[0]['nhid'] = 10
        self.assertEqual(forwarding_vteps(route, neigh, fdb), [])

    def test_bursts_and_unrecovered_loss(self):
        rows = [dict(sequence=i, success=ok, send_ns=i*1000000,
                     receive_ns=(i*1000000+100) if ok else None)
                for i, ok in enumerate([True, False, False, True, False])]
        result = loss_summary(rows)
        self.assertEqual(result['lost'], 3)
        self.assertEqual([b['count'] for b in result['bursts']], [2, 1])
        self.assertIsNone(result['bursts'][-1]['recovery_receive_ns'])

    def test_icmp_checksum(self):
        self.assertEqual(checksum(bytes.fromhex('0001f203f4f5f6f7')), 0x220d)


if __name__ == '__main__':
    unittest.main()
