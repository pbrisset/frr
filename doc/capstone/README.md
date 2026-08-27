# FRR Capstone Program

Welcome to the **capstone** branch of FRR. This directory contains the
documentation, exercises, and reference material for university students
participating in the Capstone program.

## Where to Find Things

All Capstone materials live under [`doc/capstone/`](.) in this branch:

| Path | Contents |
| --- | --- |
| `doc/capstone/README.md` | This overview (start here) |
| `doc/capstone/student_cookbook.md` | Full build / topotest / lab workflow (macOS + Ubuntu) |
| `tests/topotests/bgp_evpn_capstone/` | The Capstone EVPN topotest and its topology |
| `tests/topotests/bgp_evpn_capstone/project_topology.pdf` | Fabric diagram for the lab |

> More material will be added here as the program progresses.

## The Capstone EVPN Lab

The lab lives in `tests/topotests/bgp_evpn_capstone/` and models an EVPN-VXLAN
fabric (see `project_topology.pdf`):

- **spine1, spine2** — eBGP-unnumbered underlay and EVPN transit (AS 65000)
- **leaf1–leaf4** — VTEPs on **L2VNI 1000** (subnet `45.0.0.0/24`, anycast SVI `45.0.0.1`)
- **leaf5** — VTEP on **L2VNI 2000** (SVI `20.0.0.1`), hosting the Controller
- **all leaves** — tenant **VRF500** with **L3VNI 500** (symmetric IRB)
- **host11/host12 … host41/host42** and the **Controller** as endpoints

`test_evpn_capstone.py` brings the fabric up and validates it:

1. every FRR process starts,
2. the eBGP underlay sessions reach Established,
3. the L2VPN EVPN sessions reach Established,
4. each leaf learns its L2VNI and the L3VNI (500),
5. host adjacencies are learned and exchanged as EVPN **Type-2 (RT-2)** routes,
6. the SVI anycast subnets are exchanged as EVPN **Type-5 (RT-5)** routes, and
7. remote leaves **import** those RT-2 (/32 host) and RT-5 (subnet) routes into VRF500.

Run it from inside the FRR development container:

```bash
cd /home/frr/frr/tests/topotests
sudo -E pytest -s bgp_evpn_capstone/test_evpn_capstone.py
```

An optional visualizer (`visualizer_server.py`) can display the topology and
live packet activity while the test runs. See the
[student cookbook](student_cookbook.md) for the visualizer and packet-chart
instructions.

## Getting Started

1. Clone the capstone branch of the fork:

   ```bash
   git clone --branch capstone https://github.com/pbrisset/frr.git
   cd frr
   ```

2. Follow the [student cookbook](student_cookbook.md) to set up your platform,
   build FRR in the development container, and run topotests.

3. Browse the Capstone materials in this directory (`doc/capstone/`).


## Contributing

Students should fork this repository, create a feature branch off `capstone`,
and open a pull request back to the `capstone` branch for review.

## Getting Help

Open an issue on the fork or reach out to the program maintainer.
