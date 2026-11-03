"""Non-invasive Phase 0 observation helpers; no FRR changes."""
import json
import time


def decode(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def forwarding_vteps(routes, neighbors, fdb):
    """Resolve a route-get gateway through neighbor RMAC to VXLAN destination.

    Return unknown for nexthop objects/multipath/unresolved paths. Never substitute
    BGP's next hop or the robot MAC for an unresolved routed forwarding path.
    """
    if not isinstance(routes, list) or len(routes) != 1:
        return []
    route = routes[0]
    if any(k in route for k in ('nhid', 'nexthops', 'multipath')):
        return []
    gateway, dev = route.get('gateway'), route.get('dev')
    if not gateway or not dev:
        return []
    macs = {n.get('lladdr') for n in neighbors or []
            if n.get('dst') == gateway and n.get('dev') == dev
            and not set(n.get('state', [])) & {'FAILED', 'INCOMPLETE'}} - {None}
    return sorted({f['dst'] for f in fdb or [] if f.get('mac') in macs
                   and f.get('ifname', f.get('dev')) == 'vni500' and 'dst' in f})


def loss_summary(rows):
    rows = sorted(rows, key=lambda r: r['sequence'])
    bursts, active = [], []
    for row in rows:
        if not row['success']:
            active.append(row)
        elif active:
            bursts.append(dict(first_sequence=active[0]['sequence'], count=len(active),
                last_sequence=active[-1]['sequence'], recovery_receive_ns=row['receive_ns'],
                first_failed_send_to_recovery_ms=(row['receive_ns']-active[0]['send_ns'])/1e6))
            active = []
    if active:
        bursts.append(dict(first_sequence=active[0]['sequence'], count=len(active),
                           last_sequence=active[-1]['sequence'], recovery_receive_ns=None))
    return dict(sent=len(rows), lost=sum(not r['success'] for r in rows), bursts=bursts)


class Recorder:
    def __init__(self, directory):
        self.directory = directory
        self.log = (directory / 'events.jsonl').open('x', buffering=1)

    def event(self, kind, **data):
        item = dict(kind=kind, observed_ns=time.monotonic_ns(), **data)
        self.log.write(json.dumps(item) + '\n')
        return item

    def command(self, node, command, category):
        start = time.monotonic_ns()
        try:
            output = node.cmd_raises(command)
            error = None
        except Exception as exc:  # retain unavailable commands as evidence
            output, error = '', str(exc)
        return self.event(category, device=node.name, command=command,
                          start_ns=start, end_ns=time.monotonic_ns(), output=output, error=error)
