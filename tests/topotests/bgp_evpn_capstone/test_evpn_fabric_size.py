# SPDX-License-Identifier: ISC
"""Experiment 1 fabric bring-up and opt-in route amplification trial."""

import os
from functools import partial
from itertools import product
from pathlib import Path

import pytest

from bgp_evpn_capstone.fabric_topology import (
    CONTROLLER_IP,
    L3VNI,
    LEAF_COUNTS,
    SPINE_COUNTS,
    TENANT_VRF,
    FabricTopology,
)
from lib import topotest
from lib.topogen import Topogen

pytestmark = [pytest.mark.bgpd, pytest.mark.evpn]


@pytest.fixture(
    scope="module",
    params=list(product(LEAF_COUNTS, SPINE_COUNTS)),
    ids=lambda size: f"{size[0]}x{size[1]}",
)
def fabric(request, tmp_path_factory):
    leaves, spines = request.param
    topology = FabricTopology(leaves, spines)
    run_name = f"{__name__}.{leaves}x{spines}"
    measure = os.environ.get("EVPN_EXP1_OUT")

    def build(tgen):
        request.addfinalizer(tgen.stop_topology)
        topology.build(tgen)
        if measure:
            from bgp_evpn_capstone.fabric_mobility import build_radios

            build_radios(topology, tgen)

    tgen = Topogen(build, run_name)
    tgen.start_topology()
    topology.configure(tgen, tmp_path_factory.mktemp(run_name))
    tgen.start_router()
    assert not tgen.routers_have_failure(), tgen.errors
    return topology, tgen


@pytest.mark.skipif(not os.environ.get("EVPN_EXP1_OUT"), reason="opt-in measurement")
def test_route_amplification(fabric):
    import route_counter
    from bgp_evpn_capstone.fabric_mobility import measure_moves

    topology, tgen = fabric
    for family in ("ipv4 unicast", "l2vpn evpn"):
        test_fabric_peering(fabric, family)
    result = measure_moves(
        topology,
        tgen,
        route_counter,
        Path(os.environ["EVPN_EXP1_OUT"]),
        int(os.environ["EVPN_EXP1_SEED"]),
        int(os.environ.get("EVPN_EXP1_MOVES", "6")),
    )
    assert result["status"] == "valid", result


def _check_peers(topology, router, family):
    summary = router.vtysh_cmd(f"show bgp {family} summary json", isjson=True)
    peers = summary.get("peers", {})
    expected = topology.peers(router.name)
    if peers.keys() != expected.keys():
        return (
            f"{router.name} {family}: peers {sorted(peers)}, expected {sorted(expected)}"
        )
    for interface, neighbor in expected.items():
        peer = peers[interface]
        wanted = ("Established", topology.asns[router.name], topology.asns[neighbor])
        actual = (peer.get("state"), peer.get("localAs"), peer.get("remoteAs"))
        if actual != wanted:
            return f"{router.name} {family} {interface}: {actual}, expected {wanted}"
    return None


@pytest.mark.parametrize("family", ["ipv4 unicast", "l2vpn evpn"])
def test_fabric_peering(fabric, family):
    topology, tgen = fabric
    for router in tgen.routers().values():
        _, error = topotest.run_and_expect(
            partial(_check_peers, topology, router, family), None, count=60, wait=1
        )
        assert error is None, error


def _missing_routes(router, prefixes, vrf=""):
    vrf_arg = f" vrf {vrf}" if vrf else ""
    routes = router.vtysh_cmd(f"show ip route{vrf_arg} bgp json", isjson=True)
    missing = [
        prefix
        for prefix in prefixes
        if not any(route.get("installed") for route in routes.get(prefix, []))
    ]
    if missing:
        return f"{router.name} {vrf or 'underlay'} missing installed routes {missing}"
    return None


def test_vtep_loopback_reachability(fabric):
    topology, tgen = fabric
    for leaf in topology.leaves:
        router = tgen.gears[leaf]
        remote_vteps = [
            topology.loopbacks[remote] for remote in topology.leaves if remote != leaf
        ]
        _, error = topotest.run_and_expect(
            partial(_missing_routes, router, [f"{ip}/32" for ip in remote_vteps]),
            None,
            count=60,
            wait=1,
        )
        assert error is None, error

    controller_leaf = tgen.gears[topology.controller_leaf]
    for leaf in topology.robot_leaves:
        controller_leaf.cmd_raises(
            f"ping -c 1 -W 2 -I {topology.loopbacks[topology.controller_leaf]} "
            f"{topology.loopbacks[leaf]}"
        )


def test_vrf500_overlay(fabric):
    topology, tgen = fabric
    for leaf in topology.leaves:
        router = tgen.gears[leaf]
        expected = {
            "vni": L3VNI,
            "type": "L3",
            "tenantVrf": TENANT_VRF,
            "localVtepIp": topology.loopbacks[leaf],
            "vxlanIntf": f"vni{L3VNI}",
            "l2Vnis": [topology.l2vni(leaf)],
        }
        _, error = topotest.run_and_expect(
            partial(
                topotest.router_json_cmp, router, f"show evpn vni {L3VNI} json", expected
            ),
            None,
            count=60,
            wait=1,
        )
        assert error is None, f"{leaf}: {error}"
        assert "Duplicate address detection: Disable" in router.vtysh_cmd(
            "show evpn"
        ), leaf

        remote_subnet = "45.0.0.0/24" if leaf == topology.controller_leaf else "20.0.0.0/24"
        _, error = topotest.run_and_expect(
            partial(_missing_routes, router, [remote_subnet], TENANT_VRF),
            None,
            count=60,
            wait=1,
        )
        assert error is None, error

    # Stationary hosts seed Type-2 MAC/IP routes and exercise symmetric IRB.
    for index in range(1, len(topology.robot_leaves) + 1):
        robot = tgen.net[f"robot{index}"]
        robot.cmd_raises("ping -c 2 -W 2 45.0.0.1")
    controller = tgen.net["controller"]
    controller.cmd_raises(f"ping -c 2 -W 2 -I {CONTROLLER_IP} 20.0.0.1")

    controller_leaf = tgen.gears[topology.controller_leaf]
    robot_prefixes = [f"{topology.robot_ip(leaf)}/32" for leaf in topology.robot_leaves]
    _, error = topotest.run_and_expect(
        partial(_missing_routes, controller_leaf, robot_prefixes, TENANT_VRF),
        None,
        count=60,
        wait=1,
    )
    assert error is None, error

    for leaf in topology.robot_leaves:
        ip = topology.robot_ip(leaf)
        status, stdout, stderr = controller.cmd_status(f"ping -c 4 -W 2 {ip}")
        assert status == 0, f"controller to {leaf}/{ip}: {stdout}{stderr}"
