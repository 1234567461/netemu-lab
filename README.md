# netemu-lab

Declarative, containerized **network simulation environment** deployer.
Describe a topology in YAML, run one command, and get a set of Docker
containers wired together with point-to-point links, each link carrying
its own delay / loss / bandwidth / jitter profile via `tc` + `netem`.

Supports **generic Linux hosts** and **virtual Cisco devices** (IOL / IOSv /
NX-OS / XRd via vrnetlab), with built-in **configuration snapshots &
rollback**, **SSH access to every node**, and a **Web UI** for topology
visualization, device console, and runtime device/link addition.

## Features

- **Declarative topology** -- YAML file checked into git, schema-validated.
- **Per-link impairment** -- delay, jitter, loss, duplicate, corrupt, reorder, bandwidth (TBF).
- **Cisco virtual devices** -- node `type: cisco_iol | cisco_iosv | cisco_nxos | cisco_xrd`.
- **Config snapshots & rollback** -- capture running-config of every node, restore in one click.
- **SSH to every node** -- sequential host ports from 2222, root / netemu.
- **Dynamic add** -- add nodes and links to a running lab via CLI or Web UI.
- **Web UI** -- FastAPI app with SVG topology, xterm.js terminal, snapshot panel.
- **Lightweight** -- pure Docker + Linux `tc`, no VMs for Linux nodes.

## Requirements

- Linux (netem / `tc` are Linux-only)
- Docker daemon accessible from the current user
- Python 3.9+
- Optional Cisco images: `vrnetlab/cisco_iol:17.12.01`, `vrnetlab/cisco_vios:15.9.3`, `vrnetlab/vr-n9kv:9.3.8`, `ios-xr/xrd-control-plane:7.11.1` (build via <https://containerlab.dev/manual/vrnetlab/>).

## Install

```bash
git clone https://github.com/1234567461/netemu-lab.git
cd netemu-lab
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Quick start

```bash
netemu validate examples/star.yaml          # schema check
sudo netemu up examples/star.yaml            # deploy + baseline snapshot
netemu ui examples/star.yaml                # Web UI at http://127.0.0.1:8765
netemu status examples/star.yaml             # show nodes / links
netemu ping examples/star.yaml server client-a   # measure simulated RTT
netemu snapshot examples/star.yaml --label after-bgp
netemu list-snapshots examples/star.yaml
netemu restore examples/star.yaml 20260918-xxxxxx-after-bgp
netemu add-node examples/star.yaml h3 --type linux
netemu add-link examples/star.yaml server h3 --delay 30
sudo netemu down examples/star.yaml
```

## Topology file

```yaml
name: my-lab
prefix: mylab
nodes:
  - name: r1
    type: cisco_iol
  - name: h1
    type: linux
links:
  - endpoints:
      - { node: r1, ip: 10.0.10.1/24 }
      - { node: h1, ip: 10.0.10.2/24 }
    impairment:
      delay_ms: 50
      loss_percent: 0.5
      rate_kbit: 10000
```

## CLI

```
netemu validate FILE
netemu up FILE [--dry-run]
netemu down FILE
netemu status FILE
netemu ping FILE SRC DST [-c N]
netemu snapshot FILE [--label X]
netemu list-snapshots FILE
netemu restore FILE SNAPSHOT
netemu ui FILE [--host H --port P]
netemu add-node FILE NAME [--type T] [--cpu C] [--memory M]
netemu add-link FILE A B [--delay D] [--loss L] [--rate R]
```

## SSH

Every node gets an SSH port starting at 2222 (2222, 2223, 2224, ...).
User: `root`, password: `netemu`.

```bash
ssh root@127.0.0.1 -p 2222
```

## Project layout

```
netemu/
  config.py       # YAML schema + dataclasses
  network.py      # per-link Docker bridge networks
  node.py         # container lifecycle + SSH bootstrap
  impairment.py   # tc/netem command builder
  snapshot.py     # running-config capture / restore
  deployer.py     # orchestrator
  web.py          # FastAPI app + embedded Web UI
  cli.py          # Click entrypoint
examples/
tests/
```

## License

MIT
