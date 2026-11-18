# SPDX-License-Identifier: ISC
"""breakpoint360 Phase 1: core mobility workload planner.

Pure planning code (no root, no topology).  It decides which robots exist,
where each one starts, which robots are mobile, and when every *requested*
inter-leaf move is due.  bp_agent.py executes the plan and records what
actually happened in the attachment ledger (ledger.py); the achieved rate is
computed from that ledger, never from this plan.

Inherited from roadmap360/fleet.py (reused, not modified):
  * address plan: robot i lives in subnet s = i % 5 (VNI 1000+s, 45+s.0.0.0/24,
    gateway .1), host octet 50 + i // 5, MAC 02:36:<s>:<i>;
  * radio model: a robot owns bridge br0 with one veth "radio" port per leaf it
    is wired to; exactly one radio is up; a move is old-radio-down then
    new-radio-up inside the robot namespace (break-before-make).

Added here:
  * hosting_leaves: robots live on the first L access leaves (2..4);
  * active_pct: only that share of robots move (stratified by home leaf);
    static robots are wired to their home leaf only, mobile robots to every
    hosting leaf;
  * mode 'staggered' (move k due at k/rate) or 'synchronized' (the same due
    times quantised down to sync_period_s boundaries, so each batch is issued
    together; mean rate and move count are identical);
  * per-robot move cap (default 4).  FRR zebra flags a MAC duplicate when its
    per-VTEP DAD counter reaches max-moves (default 5 within 180 s); the
    counter only advances on real local<->remote transitions
    (zebra_evpn_mac.c), and each move is one departure plus one arrival, so a
    robot that moves at most 4 times can never reach 5 at any VTEP.
"""
import math
import random

try:
    from bgp_evpn_capstone.roadmap360 import fleet
except ImportError:  # unit tests / standalone use
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
    from roadmap360 import fleet

SCHEMA = 'bp360-workload-1'
ACCESS_LEAVES = list(fleet.ACCESS_LEAVES)
VTEP = {'leaf1': '10.0.0.1', 'leaf2': '10.0.0.2', 'leaf3': '10.0.0.3', 'leaf4': '10.0.0.4',
        'leaf5': '10.0.0.5'}
FRR_DAD_MAX_MOVES = 5
FRR_DAD_TIME_S = 180
MAX_ROBOTS = 1000  # address plan limit (5 subnets x 200 host octets)

DEFAULTS = dict(robots=64, active_pct=100.0, hosting_leaves=4, rate=8.0, duration_s=10.0,
                mode='staggered', arrival='uniform', sync_period_s=1.0, max_moves_per_robot=4,
                allow_dad_exceed=False, seed=1, telemetry_hz=1.0, garp=True, arp_offsets_ms=(),
                attach_timeout_ms=2000)


class WorkloadError(ValueError):
    """The requested workload cannot be planned as specified."""


def make_spec(**overrides):
    unknown = set(overrides) - set(DEFAULTS)
    if unknown:
        raise WorkloadError('unknown workload parameter(s): {}'.format(', '.join(sorted(unknown))))
    spec = dict(DEFAULTS, **overrides)
    spec['robots'] = int(spec['robots'])
    spec['active_pct'] = float(spec['active_pct'])
    spec['hosting_leaves'] = int(spec['hosting_leaves'])
    spec['rate'] = float(spec['rate'])
    spec['duration_s'] = float(spec['duration_s'])
    spec['sync_period_s'] = float(spec['sync_period_s'])
    spec['max_moves_per_robot'] = int(spec['max_moves_per_robot'])
    spec['seed'] = int(spec['seed'])
    spec['telemetry_hz'] = float(spec['telemetry_hz'])
    spec['attach_timeout_ms'] = int(spec['attach_timeout_ms'])
    spec['arp_offsets_ms'] = sorted(int(x) for x in spec['arp_offsets_ms'])
    validate(spec)
    return spec


def validate(spec):
    if not 1 <= spec['robots'] <= MAX_ROBOTS:
        raise WorkloadError('robots must be 1..{}'.format(MAX_ROBOTS))
    if not 0.0 <= spec['active_pct'] <= 100.0:
        raise WorkloadError('active_pct must be 0..100')
    if not 1 <= spec['hosting_leaves'] <= len(ACCESS_LEAVES):
        raise WorkloadError('hosting_leaves must be 1..{}'.format(len(ACCESS_LEAVES)))
    if spec['mode'] not in ('staggered', 'synchronized'):
        raise WorkloadError("mode must be 'staggered' or 'synchronized'")
    if spec['arrival'] not in ('uniform', 'poisson'):
        raise WorkloadError("arrival must be 'uniform' or 'poisson'")
    if spec['rate'] < 0 or spec['duration_s'] <= 0 or spec['sync_period_s'] <= 0:
        raise WorkloadError('rate must be >= 0; duration_s and sync_period_s must be > 0')
    if spec['max_moves_per_robot'] < 1:
        raise WorkloadError('max_moves_per_robot must be >= 1')
    if spec['rate'] > 0 and spec['hosting_leaves'] < 2:
        raise WorkloadError('inter-leaf moves need at least 2 hosting leaves')


def hosting(spec):
    return ACCESS_LEAVES[:spec['hosting_leaves']]


def active_count(spec):
    # round half up so that e.g. 64 robots x 12.5 % = 8 exactly
    return min(spec['robots'], int(math.floor(spec['robots'] * spec['active_pct'] / 100.0 + 0.5)))


def plan_robots(spec):
    """Robot dicts (fleet.robot_plan address plan) with home leaf, ports, active flag."""
    leaves = hosting(spec)
    robots = fleet.robot_plan(spec['robots'])
    for r in robots:
        r['home'] = r['leaf'] = leaves[r['index'] % len(leaves)]
    rng = random.Random('bp360-active-{}'.format(spec['seed']))
    want = active_count(spec)
    groups = {leaf: [r for r in robots if r['home'] == leaf] for leaf in leaves}
    for members in groups.values():
        rng.shuffle(members)
    # stratified by home leaf: deal robots round-robin across leaves
    dealt = []
    depth = max(len(m) for m in groups.values()) if groups else 0
    for k in range(depth):
        for leaf in leaves:
            if k < len(groups[leaf]):
                dealt.append(groups[leaf][k])
    active = {r['name'] for r in dealt[:want]}
    for r in robots:
        r['active'] = r['name'] in active
        r['ports'] = list(leaves) if r['active'] else [r['home']]
    return robots


def due_times(spec):
    """Requested due times (seconds after T0) for every move."""
    rate, duration = spec['rate'], spec['duration_s']
    if rate == 0:
        return []
    if spec['arrival'] == 'uniform':
        count = int(math.floor(rate * duration + 1e-9))
        times = [k / rate for k in range(count)]
    else:
        rng = random.Random('bp360-arrival-{}'.format(spec['seed']))
        times, t = [], rng.expovariate(rate)
        while t < duration:
            times.append(t)
            t += rng.expovariate(rate)
    if spec['mode'] == 'synchronized':
        period = spec['sync_period_s']
        times = [math.floor(t / period + 1e-9) * period for t in times]
    return times


def plan(**overrides):
    """Return the full workload plan (JSON-serialisable)."""
    spec = make_spec(**overrides)
    robots = plan_robots(spec)
    mobile = [r for r in robots if r['active']]
    times = due_times(spec)
    if times and not mobile:
        raise WorkloadError('rate > 0 but no active robots (robots x active_pct rounds to 0)')
    per_robot = int(math.ceil(len(times) / len(mobile))) if times else 0
    if per_robot > spec['max_moves_per_robot'] and not spec['allow_dad_exceed']:
        raise WorkloadError(
            '{} moves over {} active robots needs {} moves/robot > cap {} (FRR DAD max-moves {} in {} s); '
            'raise active robots, shorten duration or set allow_dad_exceed'.format(
                len(times), len(mobile), per_robot, spec['max_moves_per_robot'], FRR_DAD_MAX_MOVES,
                FRR_DAD_TIME_S))
    # Batches (synchronized) must use distinct robots.
    batch_sizes = {}
    for t in times:
        batch_sizes[t] = batch_sizes.get(t, 0) + 1
    if batch_sizes and max(batch_sizes.values()) > len(mobile):
        raise WorkloadError('a synchronized batch of {} moves exceeds {} active robots'.format(
            max(batch_sizes.values()), len(mobile)))

    rng = random.Random('bp360-moves-{}'.format(spec['seed']))
    order = mobile[:]
    rng.shuffle(order)  # fixed cyclic order: every robot moves once per pass, max spacing
    where = {r['name']: r['home'] for r in robots}
    counts = {r['name']: 0 for r in robots}
    batch_index, last_t = -1, None
    moves = []
    for seq, t in enumerate(times):
        if t != last_t:
            batch_index += 1
            last_t = t
        r = order[seq % len(order)]
        src = where[r['name']]
        choices = [leaf for leaf in r['ports'] if leaf != src]
        dst = choices[0] if len(choices) == 1 else rng.choice(choices)
        moves.append(dict(seq=seq, at_s=round(t, 9), batch=batch_index, robot=r['name'], planned_src=src, dst=dst))
        where[r['name']] = dst
        counts[r['name']] += 1

    gaps = []
    last_move = {}
    for m in moves:
        if m['robot'] in last_move:
            gaps.append(m['at_s'] - last_move[m['robot']])
        last_move[m['robot']] = m['at_s']
    stats = dict(requested_rate=spec['rate'], requested_duration_s=spec['duration_s'],
                 planned_moves=len(moves), planned_rate=(len(moves) / spec['duration_s']),
                 robots=len(robots), active_robots=len(mobile), static_robots=len(robots) - len(mobile),
                 hosting_leaves=hosting(spec), batches=batch_index + 1,
                 max_batch=max(batch_sizes.values()) if batch_sizes else 0,
                 max_moves_per_robot_planned=max(counts.values()) if counts else 0,
                 dad_safe=(max(counts.values()) if counts else 0) < FRR_DAD_MAX_MOVES,
                 min_same_robot_gap_s=min(gaps) if gaps else None,
                 veth_pairs=sum(len(r['ports']) for r in robots),
                 planned_final={r['name']: where[r['name']] for r in robots})
    return dict(schema=SCHEMA, spec=spec, robots=robots, moves=moves, stats=stats)


def agent_spec(workload, leaf_paths, lead_s=3.0, post_s=10.0, controller_ip='20.0.0.51'):
    """Translate a plan into bp_agent.py input.

    Times stay relative: the agent fixes T0 = (its own ready time) + lead_s with
    rebase(), so slow namespace opening cannot eat into the schedule.
    """
    spec = workload['spec']
    return dict(schema=SCHEMA, controller_ip=controller_ip, controller_port=5005,
                telemetry_hz=spec['telemetry_hz'], garp=spec['garp'], arp_offsets_ms=spec['arp_offsets_ms'],
                attach_timeout_ms=spec['attach_timeout_ms'], leaves=leaf_paths, robots=workload['robots'],
                moves=[dict(m) for m in workload['moves']], lead_s=float(lead_s), post_s=float(post_s),
                requested_rate=spec['rate'], duration_s=spec['duration_s'], mode=spec['mode'],
                sync_period_s=spec['sync_period_s'])


def rebase(agent, now_ns):
    """Fix T0 and absolute CLOCK_MONOTONIC due times (in place); returns the spec."""
    t0 = now_ns + int(agent['lead_s'] * 1e9)
    agent['t0_ns'] = t0
    agent['start_ns'] = now_ns
    agent['stop_ns'] = t0 + int((agent['duration_s'] + agent['post_s']) * 1e9)
    for m in agent['moves']:
        m['at_ns'] = t0 + int(round(m['at_s'] * 1e9))
    return agent
