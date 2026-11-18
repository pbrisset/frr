# SPDX-License-Identifier: ISC
"""Generate the baseline Capstone EVPN fabric at different sizes."""

import re
from pathlib import Path

BASELINE = Path(__file__).resolve().parent
LEAF_COUNTS = (5, 9, 17)
SPINE_COUNTS = (2, 4)
L3VNI = 500
TENANT_VRF = "VRF500"
ANYCAST_MAC = "00:00:5e:00:01:01"
ROBOT_SVI = "45.0.0.1/24"
CONTROLLER_SVI = "20.0.0.1/24"
CONTROLLER_IP = "20.0.0.51"


class FabricTopology:
    """Topogen builder and FRR/Linux configuration for one fabric size."""

    def __init__(self, leaf_count, spine_count):
        if leaf_count not in LEAF_COUNTS or spine_count not in SPINE_COUNTS:
            raise ValueError(f"Unsupported fabric size {leaf_count}x{spine_count}")

        self.leaves = tuple(f"leaf{i}" for i in range(1, leaf_count + 1))
        self.spines = tuple(f"spine{i}" for i in range(1, spine_count + 1))
        self.controller_leaf = self.leaves[-1]
        self.robot_leaves = self.leaves[:-1]
        self.loopbacks = {
            **{leaf: f"10.0.0.{i}" for i, leaf in enumerate(self.leaves, 1)},
            **{spine: f"10.0.0.{i}" for i, spine in enumerate(self.spines, 101)},
        }
        self.asns = {
            **{leaf: i for i, leaf in enumerate(self.leaves, 65011)},
            **dict.fromkeys(self.spines, 65000),
        }

    def peers(self, router):
        """Map the router's unnumbered interface names to their peers."""
        neighbors = self.spines if router in self.leaves else self.leaves
        return {f"eth-{neighbor}": neighbor for neighbor in neighbors}

    def l2vni(self, leaf):
        return 2000 if leaf == self.controller_leaf else 1000

    def robot_ip(self, leaf):
        return f"45.0.0.{11 + self.robot_leaves.index(leaf)}"

    def build(self, tgen):
        """Create routers, point-to-point fabric links and access networks."""
        for router in (*self.spines, *self.leaves):
            tgen.add_router(router)

        for leaf in self.leaves:
            for leaf_interface, spine in self.peers(leaf).items():
                tgen.add_link(
                    tgen.gears[leaf], tgen.gears[spine], leaf_interface, f"eth-{leaf}"
                )

        for index, leaf in enumerate(self.robot_leaves, 1):
            ap_name = f"ap{index}"
            ap = tgen.add_switch(ap_name)
            robot = tgen.add_host(
                f"robot{index}", f"{self.robot_ip(leaf)}/24", "via 45.0.0.1"
            )
            tgen.add_link(tgen.gears[leaf], ap, "eth-ap", f"{ap_name}-up")
            tgen.add_link(robot, ap, f"w{index}", f"{ap_name}-radio")

        controller = tgen.add_host("controller", f"{CONTROLLER_IP}/24", "via 20.0.0.1")
        tgen.add_link(
            tgen.gears[self.controller_leaf], controller, "eth-controller", "eth0"
        )

    def render_frr_config(self, router):
        """Expand a baseline router config for its address, ASN and links."""
        if router == self.controller_leaf:
            template, loopback, asn = "leaf5", "10.0.0.5", 65015
        elif router in self.leaves:
            template, loopback, asn = "leaf1", "10.0.0.1", 65011
        else:
            template, loopback, asn = "spine1", "10.0.0.101", 65000

        config = (BASELINE / template / "frr.conf").read_text()
        config = config.replace(f"hostname {template}\n", f"hostname {router}\n")
        config = config.replace(loopback, self.loopbacks[router])
        config = config.replace(f"router bgp {asn}", f"router bgp {self.asns[router]}")
        peers = "".join(
            f" neighbor {interface} interface peer-group fabric\n"
            for interface in self.peers(router)
        )
        config = re.sub(
            r"(?:^ neighbor eth-\S+ interface peer-group fabric\n)+",
            peers,
            config,
            flags=re.MULTILINE,
        )
        # Keep the baseline advertisement interval and sender loop detection.
        return config.replace(
            "  advertise-all-vni\n", "  advertise-all-vni\n  no dup-addr-detection\n"
        )

    def configure(self, tgen, config_dir):
        """Set up the VTEP dataplane and load generated configs before FRR starts."""
        for leaf in self.leaves:
            l2vni = self.l2vni(leaf)
            l2bridge = f"br{l2vni}"
            controller = leaf == self.controller_leaf
            svi = CONTROLLER_SVI if controller else ROBOT_SVI
            access = "eth-controller" if controller else "eth-ap"
            tgen.net[leaf].cmd_raises(
                f"""set -e
ip link add {TENANT_VRF} type vrf table {L3VNI}
ip link set {TENANT_VRF} up
ip link add br{L3VNI} type bridge stp_state 0
ip link set br{L3VNI} master {TENANT_VRF}
ip link set br{L3VNI} up
ip link add {l2bridge} type bridge stp_state 0
ip link set {l2bridge} address {ANYCAST_MAC}
ip link set {l2bridge} master {TENANT_VRF}
ip address add {svi} dev {l2bridge}
ip link set {l2bridge} up
ip link set {access} master {l2bridge}
ip link set {access} up
for vni in {L3VNI} {l2vni}; do
    ip link add vni$vni type vxlan id $vni dstport 4789 local {self.loopbacks[leaf]} nolearning
    ip link set vni$vni master br$vni
    ip link set vni$vni up
done"""
            )

        for name, router in tgen.routers().items():
            config = config_dir / f"{name}.conf"
            config.write_text(self.render_frr_config(name))
            router.load_frr_config(str(config))
