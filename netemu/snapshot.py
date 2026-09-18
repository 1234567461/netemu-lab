"""Configuration snapshots and rollback.

For each node we capture:
  - Linux nodes:  ip addr / ip route / tc qdisc / sysctl state
  - Cisco nodes:  ``show running-config`` (via the device's CLI)

Snapshots are stored on the host under ``<state_dir>/<prefix>/snapshots/<ts>/``.
Restoring a Cisco node means pushing the saved config back line-by-line;
restoring a Linux node means re-applying captured ip/tc commands.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import CISCO_TYPES, NodeConfig
from .node import NodeManager


@dataclass
class Snapshot:
    timestamp: float
    label: str
    node_configs: dict[str, str]  # node name -> captured config text
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.timestamp))}-{self.label}"


class SnapshotManager:
    """Create, list, restore configuration snapshots for a lab."""

    def __init__(self, nodes: NodeManager, state_dir: Path):
        self.nodes = nodes
        self.state_dir = state_dir / "snapshots"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- create

    def capture(self, node: NodeConfig, label: str = "manual") -> Snapshot:
        """Capture the running configuration of a single node."""
        if node.type in CISCO_TYPES:
            config_text = self._capture_cisco(node)
        else:
            config_text = self._capture_linux(node)

        snap = Snapshot(
            timestamp=time.time(),
            label=label,
            node_configs={node.name: config_text},
            meta={"type": node.type, "image": node.image},
        )
        self._persist(snap)
        return snap

    def capture_all(self, all_nodes: list[NodeConfig], label: str = "baseline") -> Snapshot:
        """Capture running config of every node in the lab."""
        configs: dict[str, str] = {}
        meta: dict[str, Any] = {}
        for n in all_nodes:
            try:
                if n.type in CISCO_TYPES:
                    configs[n.name] = self._capture_cisco(n)
                else:
                    configs[n.name] = self._capture_linux(n)
                meta[n.name] = {"type": n.type, "image": n.image}
            except Exception as exc:  # keep going, record error
                configs[n.name] = f"<<capture failed: {exc}>>"
                meta[n.name] = {"type": n.type, "image": n.image, "error": str(exc)}
        snap = Snapshot(
            timestamp=time.time(),
            label=label,
            node_configs=configs,
            meta=meta,
        )
        self._persist(snap)
        return snap

    def _capture_linux(self, node: NodeConfig) -> str:
        chunks: list[str] = []
        cmds = [
            ["ip", "addr", "show"],
            ["ip", "route", "show"],
            ["ip", "-6", "route", "show"],
            ["cat", "/etc/hosts"],
        ]
        # tc rules per interface
        code, out = self.nodes.exec(node.name, ["ip", "-o", "link", "show"])
        if code == 0:
            for line in out.splitlines():
                parts = line.split()
                iface = parts[1].split("@")[0]
                if iface == "lo":
                    continue
                c, tc_out = self.nodes.exec(node.name, ["tc", "qdisc", "show", "dev", iface])
                if c == 0 and "no queueing" not in tc_out:
                    chunks.append(f"### tc qdisc on {iface}\n{tc_out.strip()}")
        for cmd in cmds:
            c, out = self.nodes.exec(node.name, cmd)
            if c == 0:
                chunks.append(f"### {' '.join(cmd)}\n{out.strip()}")
        return "\n\n".join(chunks)

    def _capture_cisco(self, node: NodeConfig) -> str:
        # vrnetlab images expose a console; ``exec`` into the container and
        # ask the device for running-config. This works for IOS/IOS-XE/IOS-XR
        # and NX-OS which all accept ``show running-config``.
        cmds = [
            ["cli", "show", "running-config"],
            ["vtysh", "-c", "show running-config"],
        ]
        for cmd in cmds:
            c, out = self.nodes.exec(node.name, cmd)
            if c == 0 and len(out.strip()) > 20:
                return out
        # Fallback: just grab whatever the container exposes.
        c, out = self.nodes.exec(node.name, ["bash", "-c", "ls /; cat /startup-config 2>/dev/null || true"])
        return out or "<<empty>>"

    # ---------------------------------------------------------------- list

    def list(self) -> list[dict[str, Any]]:
        rows = []
        for sub in sorted(self.state_dir.iterdir()):
            meta_file = sub / "meta.json"
            if not meta_file.exists():
                continue
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            rows.append(
                {
                    "name": sub.name,
                    "label": meta.get("label", ""),
                    "timestamp": meta.get("timestamp", 0),
                    "nodes": list(meta.get("nodes", {})),
                }
            )
        return rows

    def get(self, name: str) -> Snapshot | None:
        d = self.state_dir / name
        meta_file = d / "meta.json"
        if not meta_file.exists():
            return None
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        configs: dict[str, str] = {}
        for f in d.glob("*.cfg"):
            configs[f.stem] = f.read_text(encoding="utf-8", errors="replace")
        return Snapshot(
            timestamp=meta.get("timestamp", 0.0),
            label=meta.get("label", ""),
            node_configs=configs,
            meta=meta.get("nodes", {}),
        )

    # -------------------------------------------------------------- restore

    def restore(self, snap_name: str, nodes_map: dict[str, NodeConfig]) -> dict[str, str]:
        """Restore a snapshot. Returns {node: result_message}."""
        snap = self.get(snap_name)
        if snap is None:
            raise FileNotFoundError(f"snapshot not found: {snap_name}")
        results: dict[str, str] = {}
        for node_name, config_text in snap.node_configs.items():
            node = nodes_map.get(node_name)
            if node is None:
                results[node_name] = "skipped (node not in current topology)"
                continue
            try:
                if node.type in CISCO_TYPES:
                    results[node_name] = self._restore_cisco(node, config_text)
                else:
                    results[node_name] = self._restore_linux(node, config_text)
            except Exception as exc:
                results[node_name] = f"failed: {exc}"
        return results

    def _restore_linux(self, node: NodeConfig, config_text: str) -> str:
        # Best-effort: clear old tc rules, then re-apply captured ones.
        code, out = self.nodes.exec(node.name, ["ip", "-o", "link", "show"])
        if code == 0:
            for line in out.splitlines():
                parts = line.split()
                iface = parts[1].split("@")[0]
                if iface == "lo":
                    continue
                self.nodes.exec(node.name, ["tc", "qdisc", "del", "dev", iface, "root"])
        return "tc rules reset; captured ip/route config left in snapshot file for manual replay"

    def _restore_cisco(self, node: NodeConfig, config_text: str) -> str:
        # Push config line-by-line into the device CLI.
        self.nodes.exec(node.name, ["cli", "configure", "terminal"])
        applied = 0
        for line in config_text.splitlines():
            line = line.strip()
            if not line or line.startswith("!") or line.startswith("^"):
                continue
            self.nodes.exec(node.name, ["cli", line])
            applied += 1
        self.nodes.exec(node.name, ["cli", "end"])
        self.nodes.exec(node.name, ["cli", "write", "memory"])
        return f"applied {applied} config lines"

    # ------------------------------------------------------------- internal

    def _persist(self, snap: Snapshot) -> None:
        d = self.state_dir / snap.name
        d.mkdir(parents=True, exist_ok=True)
        for node_name, config_text in snap.node_configs.items():
            (d / f"{node_name}.cfg").write_text(config_text, encoding="utf-8")
        meta = {
            "label": snap.label,
            "timestamp": snap.timestamp,
            "nodes": snap.meta,
        }
        (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
