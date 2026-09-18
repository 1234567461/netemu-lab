"""Unit tests for config parsing (no Docker required)."""

from pathlib import Path

import pytest

from netemu.config import ConfigError, load_config


REPO = Path(__file__).resolve().parent.parent


def test_load_star_example():
    cfg = load_config(REPO / "examples" / "star.yaml")
    assert cfg.name == "star-demo"
    assert [n.name for n in cfg.nodes] == ["server", "client-a", "client-b"]
    assert len(cfg.links) == 2
    assert cfg.links[0].impairment.delay_ms == 50
    assert cfg.links[0].impairment.rate_kbit == 10000


def test_load_linear_example():
    cfg = load_config(REPO / "examples" / "linear.yaml")
    assert len(cfg.nodes) == 5
    assert len(cfg.links) == 4


def test_load_mesh_example():
    cfg = load_config(REPO / "examples" / "mesh.yaml")
    assert len(cfg.links) == 3


def test_rejects_duplicate_nodes(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: t\nnodes:\n  - name: a\n  - name: a\nlinks:\n  - endpoints: [{node: a}, {node: a}]\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_rejects_unknown_node_in_link(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: t\nnodes:\n  - name: a\nlinks:\n  - endpoints: [{node: a}, {node: ghost}]\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_rejects_self_loop(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: t\nnodes:\n  - name: a\nlinks:\n  - endpoints: [{node: a}, {node: a}]\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_rejects_duplicate_ips(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: t\nnodes: [{name: a}, {name: b}, {name: c}]\nlinks:\n  - endpoints: [{node: a, ip: 10.0.0.1/24}, {node: b, ip: 10.0.0.2/24}]\n  - endpoints: [{node: b, ip: 10.0.0.1/24}, {node: c, ip: 10.0.0.3/24}]\n")
    with pytest.raises(ConfigError):
        load_config(p)
