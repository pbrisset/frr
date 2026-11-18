# SPDX-License-Identifier: ISC
"""Apply matched move plans to the archived 64-robot workload."""

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mobility_scale as mobility
import pytest
from lib.topogen import get_topogen

BaseFleet = mobility.Fleet


class MatchedFleet(BaseFleet):
    def __init__(self, n, seed, behavior):
        super().__init__(n, seed, behavior)
        self.plan_rng = random.Random(seed)
        self.phase_rng = random.Random(seed + 1)

    def place_all(self):
        placements = [self.plan_rng.choice(mobility.ROBOT_LEAVES) for _ in range(self.n)]
        with ThreadPoolExecutor(16) as pool:
            list(pool.map(lambda i: self._attach(i, None, placements[i], 0), range(self.n)))
        self.reap()

    def run(self, duration, dwell, sync=False, gap_s=0.0, workers=32):
        rounds = int(duration / dwell)
        assert rounds > 0 and rounds * dwell == duration
        order = list(range(self.n))
        self.phase_rng.shuffle(order)
        start = time.monotonic()
        with ThreadPoolExecutor(workers) as pool:
            for cycle in range(rounds):
                destinations = [
                    self.plan_rng.choice([leaf for leaf in mobility.ROBOT_LEAVES
                                          if leaf != self.where[i]])
                    for i in range(self.n)
                ]
                jobs = []
                for phase, i in enumerate(order):
                    offset = 0 if sync else dwell * phase / self.n
                    time.sleep(max(0, start + dwell * cycle + offset - time.monotonic()))
                    jobs.append(pool.submit(self._attach, i, self.where[i], destinations[i], gap_s))
                for job in jobs:
                    job.result()
                self.reap()
        time.sleep(max(0, start + duration - time.monotonic()))


mobility.Fleet = MatchedFleet


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    is_experiment = item.name == "test_mobility_experiment"
    if is_experiment:
        item.module.QUIET_S = max(item.module.QUIET_S, item.module.CFG["dwell_s"])
    result = yield
    if not is_experiment or result.excinfo:
        return

    leaf = get_topogen().net["leaf5"]
    snapshot = {
        "t": time.time(),
        "routes": json.loads(leaf.cmd_raises("ip -j -4 route show table 500")),
        "nexthops": json.loads(leaf.cmd_raises("ip -j nexthop show")),
    }
    (Path(item.module.OUTDIR) / "leaf5_final_fib.json").write_text(
        json.dumps(snapshot, indent=2) + "\n"
    )
