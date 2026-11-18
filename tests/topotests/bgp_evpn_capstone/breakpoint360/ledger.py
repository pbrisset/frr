# SPDX-License-Identifier: ISC
"""breakpoint360 Phase 1: authoritative per-robot attachment ledger.

bp_agent.py appends one JSON line per attachment event (CLOCK_MONOTONIC ns):

  init              robot starts attached to its home leaf (fleet.create)
  detach            old radio admin-down acknowledged by the kernel (RTM_NEWLINK ACK)
  detach_confirmed  leaf-side bridge port of the old leaf left FORWARDING (informational)
  attach            new radio admin-up acknowledged by the kernel
  attach_confirmed  leaf-side bridge port on the new leaf reached FORWARDING
                    (source=bridge_forwarding), or became operationally up when
                    no bridge port-state notification was seen (source=oper_up)
  attach_timeout    neither was seen within attach_timeout_ms

The robot is ATTACHED to a leaf only from attach_confirmed (the emulated
attachment has actually completed: the leaf bridge can learn and forward its
frames) until the next detach.  Between detach and attach_confirmed it is
IN_TRANSIT; after attach_timeout it is UNCONFIRMED (radio up, leaf side not
seen).  A confirmation that belongs to an older move is ignored.

The Phase 3 oracle will query ``state_at(robot, t_ns)`` / ``final()``.
"""
import bisect
import json
import math

ATTACHED, IN_TRANSIT, UNCONFIRMED, DETACHED = 'attached', 'in_transit', 'unconfirmed', 'detached'


class Ledger:
    def __init__(self):
        self.timeline = {}  # robot -> [(t_ns, state, leaf, move)]
        self.pending = {}   # robot -> move seq of the latest attach not yet confirmed
        self.events = 0
        self.ignored = []

    @classmethod
    def load(cls, path):
        ledger = cls()
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if line:
                    ledger.apply(json.loads(line))
        return ledger

    def _push(self, robot, t_ns, state, leaf, move):
        line = self.timeline.setdefault(robot, [])
        if line and t_ns < line[-1][0]:
            t_ns = line[-1][0]  # monotone per robot (receipt order is authoritative)
        line.append((t_ns, state, leaf, move))

    def apply(self, ev):
        kind, robot, t = ev.get('ev'), ev.get('robot'), ev.get('mono_ns')
        if kind in (None, 'final', 'meta') or robot is None:
            return
        self.events += 1
        move = ev.get('move')
        if kind == 'init':
            self._push(robot, t, ATTACHED, ev['leaf'], None)
        elif kind == 'detach':
            if ev.get('err', 0) == 0:
                self.pending.pop(robot, None)
                self._push(robot, t, IN_TRANSIT, None, move)
        elif kind == 'attach':
            if ev.get('err', 0) == 0:
                self.pending[robot] = move
            else:
                self._push(robot, t, DETACHED, None, move)
        elif kind == 'attach_confirmed':
            if self.pending.get(robot) == move:
                self.pending.pop(robot)
                self._push(robot, t, ATTACHED, ev['leaf'], move)
            else:
                self.ignored.append(ev)
        elif kind == 'attach_timeout':
            if self.pending.get(robot) == move:
                self._push(robot, t, UNCONFIRMED, ev['leaf'], move)
        # detach_confirmed is informational only

    def state_at(self, robot, t_ns):
        line = self.timeline.get(robot)
        if not line:
            return None
        k = bisect.bisect_right([e[0] for e in line], t_ns) - 1
        if k < 0:
            return None
        t, state, leaf, move = line[k]
        return dict(state=state, leaf=leaf, move=move, since_ns=t)

    def current(self, robot):
        line = self.timeline.get(robot)
        if not line:
            return None
        t, state, leaf, move = line[-1]
        return dict(state=state, leaf=leaf, move=move, since_ns=t)

    def final(self):
        return {robot: self.current(robot) for robot in sorted(self.timeline)}

    def where(self):
        """robot -> leaf for ATTACHED robots only (authoritative locations)."""
        return {r: s['leaf'] for r, s in self.final().items() if s and s['state'] == ATTACHED}


def percentile(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q / 100.0
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def _dist(values, scale=1e6):
    values = [v / scale for v in values if v is not None]
    if not values:
        return dict(n=0)
    return dict(n=len(values), min=min(values), p50=percentile(values, 50), p95=percentile(values, 95),
                max=max(values), mean=sum(values) / len(values))


def achieved_rates(records, requested_rate, t0_ns, duration_s, bin_s=1.0, planned=None, mode='staggered',
                   sync_period_s=1.0):
    """Requested vs achieved movement rate from executed move records.

    issued    : the move started (old radio down requested)
    completed : attach_confirmed (attachment actually completed), not timeouts
    Rates are reported over the requested window [T0, T0+duration) and over the
    whole span until the last completion (whichever is longer), plus 1-s bins.
    Moves sharing one due time form a batch: batch_start_lag is first start -
    due time; batch_issue_spread is last radio-up ACK - first start, i.e. how
    "synchronized" a synchronized batch really was.
    """
    planned = len(records) if planned is None else planned
    start = [r['start_ns'] for r in records if r.get('start_ns')]
    done = sorted(r['attach_confirmed_ns'] for r in records if r.get('attach_confirmed_ns'))
    end_window = t0_ns + int(duration_s * 1e9)
    in_window = sum(1 for t in done if t0_ns <= t < end_window)
    span_s = max(duration_s, ((done[-1] - t0_ns) / 1e9) if done else 0.0)
    nbins = int(math.ceil(span_s / bin_s - 1e-9)) if span_s > 0 else 0
    bins = [0] * nbins
    for t in done:
        k = int((t - t0_ns) / (bin_s * 1e9))
        if 0 <= k < nbins:
            bins[k] += 1
    window_bins = [b / bin_s for b in bins[:int(math.ceil(duration_s / bin_s - 1e-9))]]
    lag = [r['start_ns'] - r['at_ns'] for r in records if r.get('start_ns') and r.get('at_ns')]
    latency = [r['attach_confirmed_ns'] - r['start_ns'] for r in records
               if r.get('attach_confirmed_ns') and r.get('start_ns')]
    ack = [r['attach_ack_ns'] - r['start_ns'] for r in records if r.get('attach_ack_ns') and r.get('start_ns')]
    batches = {}
    for r in records:
        if r.get('start_ns') and r.get('at_ns') is not None:
            batches.setdefault((r['at_ns'], r.get('batch')), []).append(r)
    batch_lag = [min(x['start_ns'] for x in b) - key[0] for key, b in batches.items()]
    spread = [max(x.get('attach_ack_ns') or x['start_ns'] for x in b) - min(x['start_ns'] for x in b)
              for b in batches.values() if len(b) > 1]
    sources = {}
    for r in records:
        key = r.get('confirm_source') or ('timeout' if r.get('attach_timeout') else
                                         ('skipped' if r.get('skipped') else 'none'))
        sources[key] = sources.get(key, 0) + 1
    out = dict(requested_rate=requested_rate, requested_duration_s=duration_s, planned_moves=planned,
               issued_moves=len(start), completed_moves=len(done),
               errors=sum(1 for r in records if r.get('detach_err') or r.get('attach_err')),
               skipped=sum(1 for r in records if r.get('skipped')),
               timeouts=sum(1 for r in records if r.get('attach_timeout')),
               confirm_sources=sources,
               achieved_rate_window=in_window / duration_s if duration_s else None,
               achieved_rate_overall=(len(done) / span_s) if span_s else None,
               issued_rate=(len(start) / duration_s) if duration_s else None,
               completion_span_s=span_s, bin_s=bin_s, completed_per_bin=bins,
               window_bin_rate=dict(min=min(window_bins) if window_bins else None,
                                    p50=percentile(window_bins, 50), max=max(window_bins) if window_bins else None),
               schedule_lag_ms=_dist(lag), batch_start_lag_ms=_dist(batch_lag), batches=len(batches),
               batch_issue_spread_ms=_dist(spread), attach_ack_ms=_dist(ack),
               completion_latency_ms=_dist(latency))
    flags = []
    if planned and len(done) < planned:
        flags.append('incomplete: {}/{} moves completed'.format(len(done), planned))
    if requested_rate and out['achieved_rate_overall'] is not None and \
            out['achieved_rate_overall'] < 0.95 * min(requested_rate, planned / duration_s if duration_s else requested_rate):
        flags.append('achieved rate {:.2f}/s < 95% of requested {:.2f}/s'.format(
            out['achieved_rate_overall'], requested_rate))
    p95 = out['batch_start_lag_ms'].get('p95')
    if p95 is not None and p95 > 100.0:
        flags.append('batch start lag p95 {:.1f} ms > 100 ms'.format(p95))
    p95 = out['batch_issue_spread_ms'].get('p95')
    if mode == 'synchronized' and p95 is not None and p95 > 250.0 * sync_period_s:
        flags.append('synchronized batch spread p95 {:.1f} ms > 25% of sync period'.format(p95))
    out['generator_flags'] = flags
    out['generator_ok'] = not flags
    return out
