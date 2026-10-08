"""Offline, seeded discrete-event model of a non-atomic username transfer."""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Outcome(str, Enum):
    VERIFIED = "verified"
    CAPTURED = "competitor_capture"
    RETAINED = "seller_retained"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Scenario:
    seed: int = 1
    competitors: int = 0
    buyer_delay_ms: float = 5
    jitter_ms: float = 5
    competition_window_ms: float = 25
    release_allowed: bool = True
    buyer_allowed: bool = True
    verification_available: bool = True
    lost_claim_response: bool = False

    def __post_init__(self):
        if self.competitors < 0 or any(x < 0 for x in (
            self.buyer_delay_ms, self.jitter_ms, self.competition_window_ms
        )):
            raise ValueError("Counts and durations must be nonnegative")


class Platform(Protocol):
    def release(self) -> bool: ...
    def claim(self, account: str) -> bool: ...
    def verify(self) -> str | None: ...


class SimulatedPlatform:
    """Single username, with atomic individual claims but no atomic transfer."""
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.owner = "seller"

    def release(self):
        if not self.scenario.release_allowed:
            return False
        self.owner = None
        return True

    def claim(self, account):
        if account == "buyer" and not self.scenario.buyer_allowed:
            return False
        if self.owner is not None:
            return False
        self.owner = account
        return True

    def verify(self):
        if not self.scenario.verification_available:
            return None
        return self.owner


def transfer(scenario: Scenario) -> dict:
    rng = random.Random(scenario.seed)
    platform: Platform = SimulatedPlatform(scenario)
    log = [{"time_ms": 0, "state": "prepared"}]
    released = platform.release()
    log.append({"time_ms": 0, "state": "released" if released else "release_rejected"})
    events = [(scenario.buyer_delay_ms + rng.uniform(0, scenario.jitter_ms), "buyer")]
    events += [(rng.uniform(0, scenario.competition_window_ms), f"competitor_{i}")
               for i in range(scenario.competitors)]
    # Random tie breaker avoids granting the buyer preferential ordering.
    events = sorted((t, rng.random(), account) for t, account in events)
    elapsed = 0
    if released:
        for time_ms, _, account in events:
            elapsed = time_ms
            acquired = platform.claim(account)
            log.append({"time_ms": time_ms, "state": "claim_attempt", "account": account,
                        "response": "unknown" if account == "buyer" and scenario.lost_claim_response
                        else "accepted" if acquired else "rejected"})
    owner = platform.verify()
    if owner == "buyer":
        outcome = Outcome.VERIFIED
    elif owner == "seller":
        outcome = Outcome.RETAINED
    elif owner is not None:
        outcome = Outcome.CAPTURED
    else:
        outcome = Outcome.UNRESOLVED
    log.append({"time_ms": elapsed, "state": outcome.value, "verified_owner": owner})
    return {"scenario": scenario.__dict__, "outcome": outcome.value,
            "simulated_duration_ms": elapsed, "verified_owner": owner,
            "settlement_eligible": outcome == Outcome.VERIFIED, "log": log}


def benchmark(runs: int, seed: int) -> dict:
    if runs < 1:
        raise ValueError("runs must be positive")
    rows = []
    for competitors in (0, 100, 1000):
        for delay in (1, 5, 20):
            counts = {o.value: 0 for o in Outcome}
            for i in range(runs):
                result = transfer(Scenario(seed=seed + i, competitors=competitors,
                                           buyer_delay_ms=delay))
                counts[result["outcome"]] += 1
            rows.append({"competitors": competitors, "buyer_delay_ms": delay,
                         "runs": runs, "outcomes": counts,
                         "verified_rate": counts[Outcome.VERIFIED.value] / runs})
    return {"model": "offline discrete-event simulation; not real platform performance",
            "seed": seed, "jitter_ms": 5, "competition_window_ms": 25, "rows": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("demo", "benchmark"))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--competitors", type=int, default=0)
    args = parser.parse_args()
    result = (benchmark(args.runs, args.seed) if args.command == "benchmark"
              else transfer(Scenario(seed=args.seed, competitors=args.competitors)))
    print(json.dumps(result, indent=2))
