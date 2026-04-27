"""Runtime enforcement of constitution rules — scans learner turns, returns injections."""
from __future__ import annotations

import time
from typing import Optional


class TriggerResult:
    """Result of scanning a learner message against a constitution."""

    def __init__(
        self,
        *,
        injections: list[str],
        events: list[dict],  # (rule_id, pattern_matched) per trigger
    ):
        self.injections = injections
        self.events = events

    def has_triggers(self) -> bool:
        return bool(self.events)


def scan_message(
    learner_msg: str,
    constitution: dict,
    *,
    distress_cooldown_until: float | None,
    now_ts: float | None = None,
) -> tuple[TriggerResult, float | None]:
    """Scan a learner message against active constitution patterns.

    Returns (result, new_distress_cooldown_until). Caller persists cooldown on session state.

    - Distress scan respects `distress.cooldown_seconds`.
    - Harm disclosure scan ignores cooldown (always triggers).
    """
    now = now_ts if now_ts is not None else time.time()
    lower = learner_msg.lower()
    injections: list[str] = []
    events: list[dict] = []
    new_cooldown = distress_cooldown_until

    # Distress (respects cooldown)
    distress_cfg = constitution.get("distress", {})
    if distress_cooldown_until is None or now >= distress_cooldown_until:
        for pattern in distress_cfg.get("patterns", []):
            if pattern in lower:
                injections.append(distress_cfg["inject_on_trigger"].strip())
                events.append({"rule_id": "distress_response", "pattern_matched": pattern})
                new_cooldown = now + float(distress_cfg.get("cooldown_seconds", 60))
                break  # one distress trigger per turn

    # Harm disclosure (no cooldown — intentional)
    harm_cfg = constitution.get("harm_disclosure", {})
    for pattern in harm_cfg.get("patterns", []):
        if pattern in lower:
            injections.append(harm_cfg["inject_on_trigger"].strip())
            events.append({"rule_id": "harm_disclosure", "pattern_matched": pattern})
            break  # one harm trigger per turn

    return TriggerResult(injections=injections, events=events), new_cooldown


def struggle_injection(constitution: dict, consecutive_failures: int) -> Optional[str]:
    """Return remediation instruction when consecutive_failures reaches threshold.

    Called from probe scoring path, not from message scan.
    """
    cfg = constitution.get("struggle_tracker", {})
    threshold = int(cfg.get("consecutive_failures_threshold", 3))
    if consecutive_failures >= threshold:
        return cfg["inject_on_trigger"].strip()
    return None
