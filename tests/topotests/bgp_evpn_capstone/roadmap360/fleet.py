# SPDX-License-Identifier: ISC
"""Fleet construction helpers (harness side, container root namespace).

Background robots are plain ``ip netns`` namespaces (no shell process each).
Radio model: the robot owns a local bridge ``br0`` (robot MAC/IP) with one
veth "radio" port pre-wired to every leaf it may roam to; exactly one robot
port is up.  A roam is ``old port down`` then ``new port up`` inside the robot
namespace (two sub-millisecond rtnetlink ops, no namespace move), so 64
roams can really be issued together.  While no port is up br0 loses carrier,
which (like host11's BBM move) flushes the robot's ARP cache.
Address plan: robot *i* lives in
subnet ``s = i % 5`` (VNI 1000+s, 45+s.0.0.0/24, gateway .1), host octet
``50 + i // 5``, attached to leaf ``1 + i % 4``.  VNI 1000 robots therefore
share host11's subnet but use .50-.249, never the topology's fixed hosts.
"""
import random
import subprocess

ACCESS_LEAVES = ['leaf1', 'leaf2', 'leaf3', 'leaf4']
EXTRA_VNIS = [1001, 1002, 1003, 1004]
ANYCAST_MAC = '00:00:5e:00:01:01'
LEAF_NS = 'r360-{}'
ROBOT_NS = 'r360f{}'


def robot_plan(count):
    robots = []
    for i in range(count):
        s = i % 5
        vni = 1000 + s
        robots.append(dict(index=i, name='f{}'.format(i), netns=ROBOT_NS.format(i),
                           netns_path='/run/netns/' + ROBOT_NS.format(i), port='f{}'.format(i),
                           leaf=ACCESS_LEAVES[i % 4], vni=vni, bridge='br{}'.format(vni),
                           ip='{}.0.0.{}'.format(45 + s, 50 + i // 5), gw='{}.0.0.1'.format(45 + s),
                           mac='02:36:{:02x}:{:02x}:{:02x}:{:02x}'.format(s, (i >> 16) & 255, (i >> 8) & 255, i & 255)))
    assert count <= 1000
    return robots


def extra_vni_dataplane(lo):
    lines = []
    for vni in EXTRA_VNIS:
        lines += ['ip link add br{v} type bridge stp_state 0'.format(v=vni),
                  'ip link set br{v} address {mac}'.format(v=vni, mac=ANYCAST_MAC),
                  'ip link set br{v} master VRF500'.format(v=vni),
                  'ip address add {n}.0.0.1/24 dev br{v}'.format(n=45 + vni - 1000, v=vni),
                  'ip link set br{v} up'.format(v=vni),
                  'ip link add vni{v} type vxlan id {v} dstport 4789 local {lo} nolearning'.format(v=vni, lo=lo),
                  'ip link set vni{v} master br{v}'.format(v=vni),
                  'ip link set vni{v} up'.format(v=vni)]
    return '\n'.join(lines)


def extra_vni_frr(config, lo):
    stanza = ''.join('  vni {v}\n   rd {lo}:{v}\n   route-target import {v}:{v}\n'
                     '   route-target export {v}:{v}\n  exit-vni\n'.format(v=v, lo=lo) for v in EXTRA_VNIS)
    marker = '  exit-vni\n exit-address-family'
    assert marker in config
    return config.replace(marker, '  exit-vni\n' + stanza + ' exit-address-family', 1)


def run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, text=True, capture_output=True, check=True)


def attach_leaves(tgen, nodes):
    run(['mkdir', '-p', '/run/netns'])
    paths = {}
    for node in nodes:
        name = LEAF_NS.format(node)
        subprocess.run(['ip', 'netns', 'delete', name], capture_output=True)
        run(['ip', 'netns', 'attach', name, str(tgen.net[node].pid)])
        paths[node] = '/run/netns/' + name
    return paths


def detach_leaves(nodes):
    for node in nodes:
        subprocess.run(['ip', 'netns', 'delete', LEAF_NS.format(node)], capture_output=True)


def leaf_port(robot, leaf):
    return '{}l{}'.format(robot['port'], leaf[-1])


def radio_port(leaf):
    return 'p' + leaf[-1]


def assign_ports(robots, profile):
    """Leaves each robot is pre-wired to (home first)."""
    for r in robots:
        home = ACCESS_LEAVES.index(r['leaf'])
        if profile == 'mob250':
            r['ports'] = [ACCESS_LEAVES[(home + k) % 4] for k in range(4)]
        elif profile.startswith('roam64'):
            r['ports'] = [r['leaf'], ACCESS_LEAVES[(home + 1) % 4]]
        else:
            r['ports'] = [r['leaf']]
    return robots


def create(robots):
    run(['ip', '-batch', '-'], '\n'.join('netns add {}'.format(r['netns']) for r in robots) + '\n')
    for leaf in ACCESS_LEAVES:
        lines = []
        for r in robots:
            if leaf not in r['ports']:
                continue
            lp = leaf_port(r, leaf)
            lines += ['link add {p} type veth peer name {rp} netns {ns}'.format(p=lp, rp=radio_port(leaf), ns=r['netns']),
                      'link set dev {p} addrgenmode none'.format(p=lp),
                      'link set {p} master {b}'.format(p=lp, b=r['bridge']),
                      'link set {p} up'.format(p=lp)]
        if lines:
            run(['ip', '-n', LEAF_NS.format(leaf), '-batch', '-'], '\n'.join(lines) + '\n')
    for r in robots:
        lines = ['link set lo up', 'link add br0 type bridge stp_state 0',
                 'link set br0 address {}'.format(r['mac']), 'link set dev br0 addrgenmode none']
        for leaf in r['ports']:
            rp = radio_port(leaf)
            lines += ['link set dev {} addrgenmode none'.format(rp), 'link set {} master br0'.format(rp)]
        lines += ['link set {} up'.format(radio_port(r['leaf'])), 'link set br0 up',
                  'addr add {}/24 dev br0'.format(r['ip']), 'route add default via {}'.format(r['gw'])]
        run(['ip', '-n', r['netns'], '-batch', '-'], '\n'.join(lines) + '\n')


def destroy(robots):
    subprocess.run(['ip', '-batch', '-'], input='\n'.join('netns delete {}'.format(r['netns']) for r in robots) + '\n',
                   text=True, capture_output=True)


def schedule(robots, profile, t0_ns, seed):
    """Return move list for the fleet profile (times are absolute monotonic ns)."""
    rng = random.Random(seed)
    moves = []
    if profile == 'mob250':
        # 25 moves/s from T0-5 s to T0+10 s; each robot used at most twice.
        order = robots[:]
        rng.shuffle(order)
        where = {r['name']: r['leaf'] for r in robots}  # every mob250 robot is wired to all four leaves
        count = 25 * 15
        for k in range(count):
            r = order[k % len(order)]
            dst = rng.choice([l for l in ACCESS_LEAVES if l != where[r['name']]])
            moves.append(dict(at_ns=t0_ns - 5_000_000_000 + k * 40_000_000, robot=r['name'], to=dst))
            where[r['name']] = dst
    elif profile in ('roam64-sync', 'roam64-staggered'):
        for k, r in enumerate(robots[:64]):
            dst = ACCESS_LEAVES[(ACCESS_LEAVES.index(r['leaf']) + 1) % 4]
            offset = 0 if profile == 'roam64-sync' else int(k * 5e9 / 64)
            moves.append(dict(at_ns=t0_ns + offset, robot=r['name'], to=dst))
    return moves
