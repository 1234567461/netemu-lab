"""Configuration loading and schema validation for netemu-lab."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "netemu-lab topology",
    "type": "object",
    "required": ["name", "nodes", "links"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "description": "Topology / lab name"},
        "prefix": {
            "type": "string",
            "default": "netemu",
            "description": "Resource name prefix (containers, networks, ...)",
        },
        "default_image": {
            "type": "string",
            "default": "nicolaka/netshoot:latest",
            "description": "Default container image for nodes without explicit image",
        },
        "nodes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"},
                    "type": {
                        "type": "string",
                        "enum": ["linux", "cisco_iol", "cisco_iosv", "cisco_nxos", "cisco_xrd"],
                        "default": "linux",
                    },
                    "image": {"type": "string"},
                    "mgmt_ip": {"type": "string"},
                    "command": {"type": "string"},
                    "cpu": {"type": "number", "minimum": 0.1, "maximum": 64},
                    "memory": {"type": "string"},
                    "env": {"type": "object", "additionalProperties": {"type": "string"}},
                    "ports": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["host", "container"],
                            "properties": {
                                "host": {"type": "integer", "minimum": 1, "maximum": 65535},
                                "container": {"type": "integer", "minimum": 1, "maximum": 65535},
                                "protocol": {"type": "string", "enum": ["tcp", "udp"], "default": "tcp"},
                            },
                        },
                    },
                    "privileged": {"type": "boolean", "default": False},
                    "cap_add": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "links": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["endpoints"],
                "properties": {
                    "endpoints": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 2,
                        "items": {
                            "type": "object",
                            "required": ["node"],
                            "properties": {
                                "node": {"type": "string"},
                                "ip": {"type": "string"},
                            },
                        },
                    },
                    "impairment": {
                        "type": "object",
                        "properties": {
                            "delay_ms": {"type": "number", "minimum": 0},
                            "delay_jitter_ms": {"type": "number", "minimum": 0},
                            "delay_correlation_percent": {"type": "number", "minimum": 0, "maximum": 100, "default": 25},
                            "loss_percent": {"type": "number", "minimum": 0, "maximum": 100},
                            "loss_correlation_percent": {"type": "number", "minimum": 0, "maximum": 100, "default": 25},
                            "duplicate_percent": {"type": "number", "minimum": 0, "maximum": 100},
                            "corrupt_percent": {"type": "number", "minimum": 0, "maximum": 100},
                            "reorder_percent": {"type": "number", "minimum": 0, "maximum": 100},
                            "reorder_gap": {"type": "integer", "minimum": 1, "default": 1},
                            "rate_kbit": {"type": "integer", "minimum": 1},
                            "limit_packets": {"type": "integer", "minimum": 1},
                        },
                    },
                },
            },
        },
    },
}

DEFAULT_IMAGES: dict[str, str] = {
    "linux": "nicolaka/netshoot:latest",
    "cisco_iol": "vrnetlab/cisco_iol:17.12.01",
    "cisco_iosv": "vrnetlab/cisco_vios:15.9.3",
    "cisco_nxos": "vrnetlab/vr-n9kv:9.3.8",
    "cisco_xrd": "ios-xr/xrd-control-plane:7.11.1",
}

CISCO_TYPES = {"cisco_iol", "cisco_iosv", "cisco_nxos", "cisco_xrd"}


@dataclass
class PortMapping:
    host: int
    container: int
    protocol: str = "tcp"


@dataclass
class NodeConfig:
    name: str
    type: str = "linux"
    image: str = ""
    mgmt_ip: str | None = None
    command: str | None = None
    cpu: float | None = None
    memory: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    ports: list[PortMapping] = field(default_factory=list)
    privileged: bool = False
    cap_add: list[str] = field(default_factory=list)


@dataclass
class Endpoint:
    node: str
    ip: str | None = None


@dataclass
class Impairment:
    delay_ms: float | None = None
    delay_jitter_ms: float | None = None
    delay_correlation_percent: float = 25.0
    loss_percent: float | None = None
    loss_correlation_percent: float = 25.0
    duplicate_percent: float | None = None
    corrupt_percent: float | None = None
    reorder_percent: float | None = None
    reorder_gap: int = 1
    rate_kbit: int | None = None
    limit_packets: int | None = None


@dataclass
class LinkConfig:
    endpoints: list[Endpoint]
    impairment: Impairment | None = None


@dataclass
class TopologyConfig:
    name: str
    prefix: str
    default_image: str
    nodes: list[NodeConfig]
    links: list[LinkConfig]
    raw: dict[str, Any] = field(default_factory=dict)


class ConfigError(Exception):
    pass


def _validate_ip(ip: str, what: str) -> None:
    try:
        ipaddress.ip_interface(ip)
    except ValueError as exc:
        raise ConfigError(f"Invalid {what} IP {ip!r}: {exc}") from exc


def load_config(path: str | Path) -> TopologyConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Topology root must be a mapping")
    try:
        jsonschema.validate(instance=raw, schema=SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ConfigError(f"Schema validation failed: {exc.message}") from exc
    default_image = raw.get("default_image", "nicolaka/netshoot:latest")
    nodes: list[NodeConfig] = []
    node_names: set[str] = set()
    for n in raw["nodes"]:
        name = n["name"]
        if name in node_names:
            raise ConfigError(f"Duplicate node name: {name}")
        node_names.add(name)
        ports = [
            PortMapping(host=pm["host"], container=pm["container"], protocol=pm.get("protocol", "tcp"))
            for pm in n.get("ports", [])
        ]
        ntype = n.get("type", "linux")
        default_for_type = DEFAULT_IMAGES.get(ntype, default_image)
        nodes.append(NodeConfig(
            name=name, type=ntype,
            image=n.get("image") or default_for_type,
            mgmt_ip=n.get("mgmt_ip"), command=n.get("command"),
            cpu=n.get("cpu"), memory=n.get("memory"),
            env=dict(n.get("env", {})), ports=ports,
            privileged=bool(n.get("privileged", False)),
            cap_add=list(n.get("cap_add", [])),
        ))
    links: list[LinkConfig] = []
    for i, link in enumerate(raw["links"]):
        eps_raw = link["endpoints"]
        eps: list[Endpoint] = []
        for ep in eps_raw:
            node = ep["node"]
            if node not in node_names:
                raise ConfigError(f"Link {i+1} references unknown node {node!r}")
            ip = ep.get("ip")
            if ip:
                _validate_ip(ip, f"endpoint {node}")
            eps.append(Endpoint(node=node, ip=ip))
        if eps[0].node == eps[1].node:
            raise ConfigError(f"Link {i+1} connects a node to itself: {eps[0].node}")
        imp_raw = link.get("impairment")
        imp = None
        if imp_raw:
            imp = Impairment(
                delay_ms=imp_raw.get("delay_ms"),
                delay_jitter_ms=imp_raw.get("delay_jitter_ms"),
                delay_correlation_percent=imp_raw.get("delay_correlation_percent", 25.0),
                loss_percent=imp_raw.get("loss_percent"),
                loss_correlation_percent=imp_raw.get("loss_correlation_percent", 25.0),
                duplicate_percent=imp_raw.get("duplicate_percent"),
                corrupt_percent=imp_raw.get("corrupt_percent"),
                reorder_percent=imp_raw.get("reorder_percent"),
                reorder_gap=imp_raw.get("reorder_gap", 1),
                rate_kbit=imp_raw.get("rate_kbit"),
                limit_packets=imp_raw.get("limit_packets"),
            )
        links.append(LinkConfig(endpoints=eps, impairment=imp))
    seen_ips: dict[str, str] = {}
    for link in links:
        for ep in link.endpoints:
            if ep.ip:
                addr = ep.ip.split("/")[0]
                if addr in seen_ips:
                    raise ConfigError(f"IP {addr} used by both {seen_ips[addr]} and {ep.node}")
                seen_ips[addr] = ep.node
    return TopologyConfig(
        name=raw["name"], prefix=raw.get("prefix", "netemu"),
        default_image=default_image, nodes=nodes, links=links, raw=raw,
    )
