#!/usr/bin/env python
# SPDX-License-Identifier: ISC

#
# test_evpn_capstone.py
# Part of NetDEF Topology Tests
#
# Copyright (c) 2026 by the FRR Capstone Program
#

"""
test_evpn_capstone.py: Bring-up test for the Capstone EVPN fabric.

The topology matches project_topology.pdf:

  * spine1, spine2           - eBGP underlay, EVPN transit (AS 65000)
  * leaf1 .. leaf4           - VTEPs, L2VNI 1000 (subnet 45.0.0.0/24, SVI 45.0.0.1)
  * leaf5                    - VTEP, L2VNI 2000 (SVI 20.0.0.1), hosts the Controller
  * all leaves               - L3VNI 500 in tenant VRF500 (symmetric IRB)
  * host11/host12 .. host41/host42 - dual hosts behind leaf1..leaf4
  * Controller               - behind leaf5

The underlay runs eBGP-unnumbered between each leaf and both spines. EVPN is
carried over the same sessions with explicit route-targets so that routes
import correctly across the different leaf ASNs.

This test only validates that the fabric comes up:
  * every FRR process starts,
  * the eBGP underlay sessions reach Established,
  * the L2VPN EVPN sessions reach Established, and
  * every leaf has learned its L2VNI and the L3VNI (500).
"""

import json
import os
import sys

import pytest

# Save the Current Working Directory to find configuration files.
CWD = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(CWD, "../"))

# pylint: disable=C0413
# Import topogen and topotest helpers
from lib import topotest
from lib.topogen import Topogen, get_topogen
from lib.topolog import logger

pytestmark = [pytest.mark.bgpd, pytest.mark.evpn]

# Shared anycast gateway MAC used by every SVI.
ANYCAST_MAC = "00:00:5e:00:01:01"

SPINES = {
    "spine1": "10.0.0.101",
    "spine2": "10.0.0.102",
}

# Per-leaf description. "hosts" maps host name -> (ip/prefix, mac).
LEAVES = {
    "leaf1": {
        "lo": "10.0.0.1",
        "l2vni": 1000,
        "l2br": "br1000",
        "svi": "45.0.0.1/24",
        "hosts": {
            "host11": ("45.0.0.11/24", "00:00:00:00:00:11"),
            "host12": ("45.0.0.12/24", "00:00:00:00:00:12"),
        },
        "gw": "45.0.0.1",
    },
    "leaf2": {
        "lo": "10.0.0.2",
        "l2vni": 1000,
        "l2br": "br1000",
        "svi": "45.0.0.1/24",
        "hosts": {
            "host21": ("45.0.0.21/24", "00:00:00:00:00:21"),
            "host22": ("45.0.0.22/24", "00:00:00:00:00:22"),
        },
        "gw": "45.0.0.1",
    },
    "leaf3": {
        "lo": "10.0.0.3",
        "l2vni": 1000,
        "l2br": "br1000",
        "svi": "45.0.0.1/24",
        "hosts": {
            "host31": ("45.0.0.31/24", "00:00:00:00:00:31"),
            "host32": ("45.0.0.32/24", "00:00:00:00:00:32"),
        },
        "gw": "45.0.0.1",
    },
    "leaf4": {
        "lo": "10.0.0.4",
        "l2vni": 1000,
        "l2br": "br1000",
        "svi": "45.0.0.1/24",
        "hosts": {
            "host41": ("45.0.0.41/24", "00:00:00:00:00:41"),
            "host42": ("45.0.0.42/24", "00:00:00:00:00:42"),
        },
        "gw": "45.0.0.1",
    },
    "leaf5": {
        "lo": "10.0.0.5",
        "l2vni": 2000,
        "l2br": "br2000",
        "svi": "20.0.0.1/24",
        "hosts": {
            "controller": ("45.0.0.51/24", "00:00:00:00:00:51"),
        },
        "gw": "45.0.0.1",
    },
}

L3VNI = 500
L3BR = "br500"
TENANT_VRF = "VRF500"


def build_topo(tgen):
    "Build the Capstone EVPN fabric."

    for spine in SPINES:
        tgen.add_router(spine)
    for leaf in LEAVES:
        tgen.add_router(leaf)

    # Full spine-leaf mesh: every leaf to every spine.
    # Use direct point-to-point links so the auto-generated switch interface
    # names do not exceed the 15-character Linux interface name limit.
    for spine in SPINES:
        for leaf in LEAVES:
            tgen.add_link(
                tgen.gears[spine],
                tgen.gears[leaf],
                "eth-{}".format(leaf),
                "eth-{}".format(spine),
            )

    # Hosts hang off their leaf access ports.
    for leaf, spec in LEAVES.items():
        for host in spec["hosts"]:
            ip = spec["hosts"][host][0]
            tgen.add_host(host, ip, "via {}".format(spec["gw"]))
            tgen.add_link(
                tgen.gears[host],
                tgen.gears[leaf],
                "eth0",
                "eth-{}".format(host),
            )


def _setup_leaf_dataplane(tgen, leaf, spec):
    "Create VRF, L3VNI, L2VNI, SVI and enslave access ports on a leaf."
    node = tgen.net[leaf]
    lo = spec["lo"]
    l2vni = spec["l2vni"]
    l2br = spec["l2br"]

    node.cmd_raises(
        """
ip link add {vrf} type vrf table {l3vni}
ip link set {vrf} up

ip link add {l3br} type bridge stp_state 0
ip link set {l3br} master {vrf}
ip link set {l3br} up
ip link add vni{l3vni} type vxlan id {l3vni} dstport 4789 local {lo} nolearning
ip link set vni{l3vni} master {l3br}
ip link set vni{l3vni} up

ip link add {l2br} type bridge stp_state 0
ip link set {l2br} address {mac}
ip link set {l2br} master {vrf}
ip address add {svi} dev {l2br}
ip link set {l2br} up
ip link add vni{l2vni} type vxlan id {l2vni} dstport 4789 local {lo} nolearning
ip link set vni{l2vni} master {l2br}
ip link set vni{l2vni} up
""".format(
            vrf=TENANT_VRF,
            l3vni=L3VNI,
            l3br=L3BR,
            l2vni=l2vni,
            l2br=l2br,
            lo=lo,
            svi=spec["svi"],
            mac=ANYCAST_MAC,
        )
    )

    for host in spec["hosts"]:
        node.cmd_raises(
            "ip link set eth-{host} master {l2br}; "
            "ip link set eth-{host} up".format(host=host, l2br=l2br)
        )


def setup_module(mod):
    "Sets up the pytest environment"
    tgen = Topogen(build_topo, mod.__name__)
    tgen.start_topology()

    # Program the VXLAN/bridge/SVI dataplane on every leaf before FRR starts.
    for leaf, spec in LEAVES.items():
        logger.info("Programming EVPN dataplane on %s", leaf)
        _setup_leaf_dataplane(tgen, leaf, spec)

    # Fix host MAC addresses to match the diagram.
    for spec in LEAVES.values():
        for host in spec["hosts"]:
            mac = spec["hosts"][host][1]
            tgen.net[host].cmd_raises(
                "ip link set dev eth0 address {}".format(mac)
            )

    for rname, router in tgen.routers().items():
        logger.info("Loading router %s", rname)
        router.load_frr_config(os.path.join(CWD, "{}/frr.conf".format(rname)))

    tgen.start_router()


def teardown_module(_mod):
    "Teardown the pytest environment"
    tgen = get_topogen()
    tgen.stop_topology()


def test_routers_running():
    "Verify that every FRR router process is up."
    tgen = get_topogen()
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    for rname, router in tgen.routers().items():
        logger.info("Checking router %s is running", rname)
        output = router.vtysh_cmd("show version")
        assert "FRRouting" in output, "{} is not running FRR".format(rname)


def _peers_established(router, afi, expected_peers):
    "Return None when all expected peers are Established, else a message."
    output = router.vtysh_cmd("show bgp {} summary json".format(afi), isjson=True)
    peers = output.get("peers", {})
    for peer in expected_peers:
        state = peers.get(peer, {}).get("state")
        if state != "Established":
            return "{} {} peer {} state {}".format(router.name, afi, peer, state)
    return None


def _expected_peers():
    "Return dict of router -> list of expected unnumbered peer interface names."
    expected = {}
    for spine in SPINES:
        expected[spine] = ["eth-{}".format(leaf) for leaf in LEAVES]
    for leaf in LEAVES:
        expected[leaf] = ["eth-{}".format(spine) for spine in SPINES]
    return expected


def test_bgp_underlay_established():
    "Verify all eBGP (IPv4 unicast) underlay sessions reach Established."
    tgen = get_topogen()
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    for rname, peers in _expected_peers().items():
        router = tgen.gears[rname]
        _, result = topotest.run_and_expect(
            lambda r=router, p=peers: _peers_established(r, "ipv4 unicast", p),
            None,
            count=90,
            wait=1,
        )
        assert result is None, "Underlay did not converge: {}".format(result)


def test_evpn_sessions_established():
    "Verify all L2VPN EVPN sessions reach Established."
    tgen = get_topogen()
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    for rname, peers in _expected_peers().items():
        router = tgen.gears[rname]
        _, result = topotest.run_and_expect(
            lambda r=router, p=peers: _peers_established(r, "l2vpn evpn", p),
            None,
            count=90,
            wait=1,
        )
        assert result is None, "EVPN sessions did not converge: {}".format(result)


def test_evpn_vnis_present():
    "Verify every leaf learned its L2VNI and the L3VNI (500)."
    tgen = get_topogen()
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    for leaf, spec in LEAVES.items():
        router = tgen.gears[leaf]
        wanted = {str(L3VNI), str(spec["l2vni"])}

        def _check(router=router, wanted=wanted):
            output = router.vtysh_cmd("show evpn vni json", isjson=True)
            missing = wanted - set(output.keys())
            if missing:
                return "{} missing VNIs {}".format(router.name, sorted(missing))
            return None

        _, result = topotest.run_and_expect(_check, None, count=60, wait=1)
        assert result is None, result


def _prime_host_adjacencies(tgen):
    "Generate host traffic so every leaf learns its local host MAC/IP."
    for spec in LEAVES.values():
        gw = spec["gw"]
        for host in spec["hosts"]:
            # Best effort: pinging the gateway triggers ARP so the leaf
            # learns the host adjacency and originates a Type-2 route.
            tgen.net[host].cmd("ping -c 2 -W 1 {}".format(gw))


def _l2vni_host_macs(l2vni):
    "Return the set of host MACs attached to leaves that use the given L2VNI."
    macs = set()
    for spec in LEAVES.values():
        if spec["l2vni"] != l2vni:
            continue
        for host in spec["hosts"]:
            macs.add(spec["hosts"][host][1])
    return macs


def _l2vni_host_ips(l2vni):
    "Return the set of host IPs (no prefix) attached to leaves for an L2VNI."
    ips = set()
    for spec in LEAVES.values():
        if spec["l2vni"] != l2vni:
            continue
        for host in spec["hosts"]:
            ips.add(spec["hosts"][host][0].split("/")[0])
    return ips


def test_evpn_type2_host_adjacencies():
    """Verify host adjacencies are learned on the leaves and exchanged as RT-2.

    Remote host MACs can only appear in a leaf's EVPN MAC table if they were
    received as EVPN Type-2 (MAC/IP) routes, so a full L2VNI 1000 MAC table
    proves both local learning and RT-2 exchange between leaf1..leaf4.
    """
    tgen = get_topogen()
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    _prime_host_adjacencies(tgen)

    l2vni = 1000
    wanted_macs = _l2vni_host_macs(l2vni)
    wanted_ips = _l2vni_host_ips(l2vni)
    leaves = [name for name, spec in LEAVES.items() if spec["l2vni"] == l2vni]

    # Every leaf in the L2VNI must know all host MACs (local + remote via RT-2).
    for leaf in leaves:
        router = tgen.gears[leaf]

        def _check_macs(router=router, wanted_macs=wanted_macs, l2vni=l2vni):
            output = router.vtysh_cmd(
                "show evpn mac vni {} json".format(l2vni), isjson=True
            )
            learned = set(output.get("macs", output).keys())
            missing = {m for m in wanted_macs if m not in learned}
            if missing:
                return "{} missing host MACs {}".format(
                    router.name, sorted(missing)
                )
            return None

        _, result = topotest.run_and_expect(_check_macs, None, count=90, wait=1)
        assert result is None, result

    # The RT-2 (MAC/IP) routes themselves must be present in the EVPN table.
    for leaf in leaves:
        router = tgen.gears[leaf]

        def _check_type2(router=router, wanted_ips=wanted_ips):
            output = router.vtysh_cmd(
                "show bgp l2vpn evpn route type macip json", isjson=True
            )
            blob = json.dumps(output)
            missing = [ip for ip in wanted_ips if ip not in blob]
            if missing:
                return "{} missing Type-2 routes for {}".format(
                    router.name, sorted(missing)
                )
            return None

        _, result = topotest.run_and_expect(_check_type2, None, count=90, wait=1)
        assert result is None, result


def test_evpn_type5_anycast_prefixes():
    """Verify each leaf's SVI anycast subnet is exchanged as EVPN RT-5.

    The connected SVI subnets are redistributed into VRF500 and advertised as
    EVPN Type-5 (IP Prefix) routes, so every leaf must see both the L2VNI 1000
    anycast subnet (45.0.0.0/24) and the leaf5 subnet (20.0.0.0/24).
    """
    tgen = get_topogen()
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    wanted_subnets = ["45.0.0.0", "20.0.0.0"]

    for leaf in LEAVES:
        router = tgen.gears[leaf]

        def _check_type5(router=router, wanted_subnets=wanted_subnets):
            output = router.vtysh_cmd(
                "show bgp l2vpn evpn route type prefix json", isjson=True
            )
            blob = json.dumps(output)
            missing = [s for s in wanted_subnets if s not in blob]
            if missing:
                return "{} missing Type-5 prefixes for {}".format(
                    router.name, sorted(missing)
                )
            return None

        _, result = topotest.run_and_expect(_check_type5, None, count=90, wait=1)
        assert result is None, result


def _vrf_has_bgp_route(router, prefix):
    "Return None if prefix is imported as a BGP route in VRF500, else a message."
    output = router.vtysh_cmd(
        "show ip route vrf {} {} json".format(TENANT_VRF, prefix), isjson=True
    )
    for entry in output.get(prefix, []):
        if entry.get("protocol") == "bgp":
            return None
    return "{} did not import {} into {}".format(router.name, prefix, TENANT_VRF)


def test_evpn_type5_routes_imported():
    """Verify remote SVI subnets (RT-5) are imported into VRF500 on other leaves.

    Each leaf must install the *other* fabric's anycast subnet into its tenant
    VRF via the L3VNI: leaf1..leaf4 import 20.0.0.0/24 (leaf5) and leaf5 imports
    45.0.0.0/24 (leaf1..leaf4).
    """
    tgen = get_topogen()
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    for leaf, spec in LEAVES.items():
        remote_subnet = "45.0.0.0/24" if spec["l2vni"] != 1000 else "20.0.0.0/24"
        router = tgen.gears[leaf]
        _, result = topotest.run_and_expect(
            lambda r=router, p=remote_subnet: _vrf_has_bgp_route(r, p),
            None,
            count=90,
            wait=1,
        )
        assert result is None, result


def test_evpn_type2_routes_imported():
    """Verify RT-2 host adjacencies are imported as /32 routes on a remote L3VNI.

    leaf5 is in a different L2VNI (2000), so it has no L2 adjacency to the
    VNI 1000 hosts. Their reachability must therefore be imported into VRF500
    as /32 host routes through the L3VNI (symmetric IRB).
    """
    tgen = get_topogen()
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    _prime_host_adjacencies(tgen)

    leaf5 = tgen.gears["leaf5"]
    for ip in _l2vni_host_ips(1000):
        prefix = "{}/32".format(ip)
        _, result = topotest.run_and_expect(
            lambda p=prefix: _vrf_has_bgp_route(leaf5, p),
            None,
            count=90,
            wait=1,
        )
        assert result is None, result


if __name__ == "__main__":
    args = ["-s"] + sys.argv[1:]
    sys.exit(pytest.main(args))
