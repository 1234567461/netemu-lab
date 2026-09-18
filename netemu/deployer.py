"""Topology deployer: orchestrates nodes, networks and impairment."""

from __future__ import annotations

import ipaddress
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker

from .config import (
    CISCO_TYPES, DEFAULT_IMAGES, Endpoint, Impairment,
    LinkConfig, NodeConfig, TopologyConfig,
)
from .impairment import build_tc_commands, clear_tc_commands, describe
from .network import NetworkManager
from .node import NodeManager
from .snapshot import SnapshotManager
from .utils import container_name, network_name, run


@dataclass
class DeployResult:
    nodes: list[str]
    links: int
    elapsed_s: float
    baseline_snapshot: str = ""
    ssh: dict[str, dict[str, Any]] | None = None


class Deployer:
    """Deploys a declared topology onto the local Docker daemon."""

    SSH_PORT_BASE = 2222

    def __init__(self, cfg: TopologyConfig, client: docker.DockerClient | None = None,
                 state_dir: str | Path | None = None):
        self.cfg = cfg
        self.client = client or docker.from_env()
        self.nodes = NodeManager(self.client, cfg.prefix)
        self.networks = NetworkManager(self.client, cfg.prefix)
        state = Path(state_dir) if state_dir else Path.home() / ".netemu" / cfg.prefix
        self.snapshots = SnapshotManager(self.nodes, state)
        self._next_ssh_port = self.SSH_PORT_BASE
        self._ssh_info: dict[str, dict[str, Any]] = {}

    def up(self) -> DeployResult:
        start = time.time()
        node_names = [n.name for n in self.cfg.nodes]
        for n in self.cfg.nodes:
            if "NET_ADMIN" not in (n.cap_add or []):
                n.cap_add = list(n.cap_add or []) + ["NET_ADMIN"]
            if n.type in CISCO_TYPES and not n.privileged:
                n.privileged = True
        for n in self.cfg.nodes:
            ssh_port = self._next_ssh_port
            self._next_ssh_port += 1
            self.nodes.start(n, ssh_port=ssh_port)
            self._ssh_info[n.name] = {
                "host": "127.0.0.1", "port": ssh_port,
                "user": "root", "password": NodeManager.DEFAULT_SSH_PASSWORD,
            }
        for idx, link in enumerate(self.cfg.links):
            self._deploy_link(idx, link)
        baseline = self.snapshots.capture_all(self.cfg.nodes, label="baseline")
        elapsed = time.time() - start
        return DeployResult(
            nodes=node_names, links=len(self.cfg.links), elapsed_s=elapsed,
            baseline_snapshot=baseline.name, ssh=self._ssh_info,
        )

    def _deploy_link(self, idx: int, link: LinkConfig) -> None:
        subnet = self._subnet_for_link(idx, link)
        net_info = self.networks.create_link_network(idx, subnet=subnet)
        net = self.client.networks.get(net_info["name"])
        ips = [ep.ip for ep in link.endpoints]
        for ep, ip in zip(link.endpoints, ips):
            container = self.nodes.get(ep.node)
            net.connect(container, aliases=[ep.node], ipv4_address=ip or "")
        time.sleep(0.5)
        for ep, ip in zip(link.endpoints, ips):
            if link.impairment is None:
                continue
            iface = self._find_iface_for_network(ep.node, net_info["name"])
            if iface is None:
                continue
            for cmd in clear_tc_commands(iface):
                self.nodes.exec(ep.node, cmd)
            for cmd in build_tc_commands(iface, link.impairment):
                code, out = self.nodes.exec(ep.node, cmd)
                if code != 0:
                    raise RuntimeError(f"tc apply failed on {ep.node}/{iface}: {out}")

    def _subnet_for_link(self, idx: int, link: LinkConfig) -> str | None:
        ips = [ep.ip for ep in link.endpoints if ep.ip]
        if not ips:
            return None
        iface = ipaddress.ip_interface(ips[0])
        net = iface.network
        for ip in ips[1:]:
            if ipaddress.ip_interface(ip).network != net:
                raise RuntimeError(f"Link {idx} endpoints in different subnets: {ips}")
        return str(net)

    def _find_iface_for_network(self, node: str, net_name: str) -> str | None:
        c = self.nodes.get(node)
        c.reload()
        nets = c.attrs["NetworkSettings"]["Networks"]
        for n_name, settings in nets.items():
            if n_name == net_name or settings.get("NetworkID") == net_name:
                mac = (settings.get("MacAddress") or "").lower()
                code, out = self.nodes.exec(node, ["ip", "-o", "link"])
                if code != 0:
                    return None
                for line in out.splitlines():
                    parts = line.split()
                    iface = parts[1].split("@")[0]
                    if mac and mac in line.lower():
                        return iface
                for line in out.splitlines():
                    parts = line.split()
                    iface = parts[1].split("@")[0]
                    if iface not in ("lo", "eth0"):
                        return iface
        return None

    def down(self) -> None:
        for n in self.nodes.list_lab_nodes():
            try:
                n.remove(force=True)
            except Exception:
                pass
        for net in self.networks.list_lab_networks():
            try:
                net.remove()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        rows = []
        for c in self.nodes.list_lab_nodes():
            c.reload()
            ips: list[str] = []
            for settings in (c.attrs["NetworkSettings"]["Networks"] or {}).values():
                if settings.get("IPAddress"):
                    ips.append(settings["IPAddress"])
            rows.append({
                "node": (c.labels or {}).get("netemu.node", c.name),
                "container": c.name,
                "status": c.status,
                "ips": ips,
                "image": c.attrs.get("Config", {}).get("Image", ""),
                "ssh": self._ssh_info.get((c.labels or {}).get("netemu.node", ""), {}),
            })
        link_rows = []
        for idx, link in enumerate(self.cfg.links):
            link_rows.append({
                "link": idx,
                "endpoints": [f"{ep.node}{('/' + ep.ip) if ep.ip else ''}" for ep in link.endpoints],
                "impairment": describe(link.impairment) if link.impairment else "none",
            })
        return {"nodes": rows, "links": link_rows}

    def ping(self, src: str, dst: str, count: int = 4) -> tuple[int, str]:
        return self.nodes.exec(src, ["ping", "-c", str(count), "-W", "2", dst])

    def add_node(self, name: str, node_type: str = "linux",
                 image: str | None = None,
                 cpu: float | None = None,
                 memory: str | None = None) -> dict[str, Any]:
        if any(n.name == name for n in self.cfg.nodes):
            raise ValueError(f"node {name} already exists")
        image = image or DEFAULT_IMAGES.get(node_type, "nicolaka/netshoot:latest")
        node = NodeConfig(
            name=name, type=node_type, image=image,
            cpu=cpu, memory=memory,
            privileged=node_type in CISCO_TYPES, cap_add=["NET_ADMIN"],
        )
        ssh_port = self._next_ssh_port
        self._next_ssh_port += 1
        self.nodes.start(node, ssh_port=ssh_port)
        self.cfg.nodes.append(node)
        self._ssh_info[name] = {
            "host": "127.0.0.1", "port": ssh_port,
            "user": "root", "password": NodeManager.DEFAULT_SSH_PASSWORD,
        }
        return {"name": name, "ssh": self._ssh_info[name]}

    def add_link(self, node_a: str, node_b: str,
                 ip_a: str | None = None, ip_b: str | None = None,
                 impairment: dict[str, Any] | None = None) -> dict[str, Any]:
        new_idx = len(self.cfg.links)
        imp = Impairment(**impairment) if impairment else None
        link = LinkConfig(
            endpoints=[Endpoint(node=node_a, ip=ip_a), Endpoint(node=node_b, ip=ip_b)],
            impairment=imp,
        )
        self._deploy_link(new_idx, link)
        self.cfg.links.append(link)
        return {"link_id": new_idx, "endpoints": [node_a, node_b]}
