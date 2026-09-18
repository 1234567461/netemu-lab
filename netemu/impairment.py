"""tc / netem impairment builder.

Generates the ``tc qdisc add ...`` command list needed to emulate delay,
jitter, loss, duplication, corruption, reordering and rate limiting on a
given interface inside a container.
"""

from __future__ import annotations

from typing import Sequence

from .config import Impairment


def build_tc_commands(iface: str, imp: Impairment) -> list[list[str]]:
    """Return a list of tc commands to apply impairment on ``iface``.

    Commands are intended to run *inside* the target container (which needs
    ``CAP_NET_ADMIN``).
    """
    cmds: list[list[str]] = []

    # Root qdisc: one-to-one for later netem / tbf attachment.
    root = ["tc", "qdisc", "add", "dev", iface, "root", "handle", "10:"]
    has_netem = any(
        v is not None
        for v in (
            imp.delay_ms,
            imp.delay_jitter_ms,
            imp.loss_percent,
            imp.duplicate_percent,
            imp.corrupt_percent,
            imp.reorder_percent,
        )
    )

    if has_netem:
        root += ["netem"]
        if imp.delay_ms is not None:
            root += ["delay", f"{imp.delay_ms}ms"]
            if imp.delay_jitter_ms is not None:
                root[-1] += f" {imp.delay_jitter_ms}ms"
                root += [f"{imp.delay_correlation_percent}%"]
        if imp.loss_percent is not None:
            root += ["loss", f"{imp.loss_percent}%"]
            root += [f"{imp.loss_correlation_percent}%"]
        if imp.duplicate_percent is not None:
            root += ["duplicate", f"{imp.duplicate_percent}%"]
        if imp.corrupt_percent is not None:
            root += ["corrupt", f"{imp.corrupt_percent}%"]
        if imp.reorder_percent is not None:
            root += [
                "reorder",
                f"{imp.reorder_percent}%",
                f"{imp.delay_correlation_percent}%",
                "gap",
                str(imp.reorder_gap),
            ]
        if imp.limit_packets is not None:
            root += ["limit", str(imp.limit_packets)]
    else:
        # No netem features: use a plain prio/quick qdisc so rate can attach.
        root += ["pfifo", "limit", str(imp.limit_packets or 1000)]

    cmds.append(root)

    # Rate limiting via tbf as a child of the root.
    if imp.rate_kbit is not None:
        cmds.append(
            [
                "tc",
                "qdisc",
                "add",
                "dev",
                iface,
                "parent",
                "10:1",
                "handle",
                "20:",
                "tbf",
                "rate",
                f"{imp.rate_kbit}kbit",
                "burst",
                "32kb",
                "latency",
                "50ms",
            ]
        )

    return cmds


def clear_tc_commands(iface: str) -> list[list[str]]:
    """Return commands to remove all tc rules on ``iface``."""
    return [["tc", "qdisc", "del", "dev", iface, "root"]]


def describe(imp: Impairment) -> str:
    """Human-readable summary, used in status output."""
    parts: list[str] = []
    if imp.delay_ms is not None:
        s = f"{imp.delay_ms}ms"
        if imp.delay_jitter_ms:
            s += f" ±{imp.delay_jitter_ms}ms"
        parts.append(f"delay {s}")
    if imp.loss_percent:
        parts.append(f"loss {imp.loss_percent}%")
    if imp.duplicate_percent:
        parts.append(f"dup {imp.duplicate_percent}%")
    if imp.corrupt_percent:
        parts.append(f"corrupt {imp.corrupt_percent}%")
    if imp.reorder_percent:
        parts.append(f"reorder {imp.reorder_percent}%")
    if imp.rate_kbit:
        parts.append(f"rate {imp.rate_kbit}kbit/s")
    return ", ".join(parts) if parts else "none"
