"""netemu-lab command-line interface."""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from .config import ConfigError, TopologyConfig, load_config

if TYPE_CHECKING:
    import docker

console = Console()


def _load_or_die(path: str) -> TopologyConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        console.print(f"[red]Invalid config:[/red] {exc}")
        sys.exit(1)


def _get_client() -> "docker.DockerClient":
    import docker
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception as exc:
        console.print(f"[red]Cannot reach Docker daemon:[/red] {exc}")
        sys.exit(2)


@click.group(help="netemu-lab: declarative network simulation deployer")
@click.version_option(package_name="netemu-lab", prog_name="netemu")
def main() -> None:
    pass


@main.command(help="Validate a topology file without deploying")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
def validate(config: str) -> None:
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        console.print(f"[red]Invalid:[/red] {exc}")
        sys.exit(1)
    console.print(f"[green]OK[/green] topology [bold]{cfg.name}[/bold]: {len(cfg.nodes)} nodes, {len(cfg.links)} links")


@main.command(help="Deploy a topology from a YAML file")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.option("--dry-run", is_flag=True, help="Print plan without applying changes")
def up(config: str, dry_run: bool) -> None:
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        console.print(f"[red]Invalid config:[/red] {exc}")
        sys.exit(1)
    console.print(f"Deploying topology [bold]{cfg.name}[/bold] ({len(cfg.nodes)} nodes, {len(cfg.links)} links) ...")
    if dry_run:
        for n in cfg.nodes:
            console.print(f"  node {n.name:<16} image={n.image}")
        for i, link in enumerate(cfg.links):
            eps = " <-> ".join(ep.node for ep in link.endpoints)
            console.print(f"  link {i:<3} {eps}")
        return
    client = _get_client()
    from .deployer import Deployer
    dep = Deployer(cfg, client=client)
    try:
        result = dep.up()
    except Exception as exc:
        console.print(f"[red]Deploy failed:[/red] {exc}")
        sys.exit(1)
    console.print(f"[green]Deployed[/green] {len(result.nodes)} nodes and {result.links} links in {result.elapsed_s:.1f}s\nBaseline snapshot: [bold]{result.baseline_snapshot}[/bold]")


@main.command(help="Tear down a deployed topology")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
def down(config: str) -> None:
    cfg = _load_or_die(config)
    client = _get_client()
    from .deployer import Deployer
    dep = Deployer(cfg, client=client)
    dep.down()
    console.print(f"[green]Torn down[/green] resources for prefix [bold]{cfg.prefix}[/bold]")


@main.command(help="Show status of a deployed topology")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
def status(config: str) -> None:
    cfg = _load_or_die(config)
    client = _get_client()
    from .deployer import Deployer
    dep = Deployer(cfg, client=client)
    st = dep.status()
    table = Table(title=f"Nodes: {cfg.name}")
    for col in ["Node", "Container", "Status", "IPs", "Image"]:
        table.add_column(col)
    for row in st["nodes"]:
        table.add_row(row["node"], row["container"], row["status"], ", ".join(row["ips"]), row["image"])
    console.print(table)
    ltable = Table(title="Links")
    for col in ["#", "Endpoints", "Impairment"]:
        ltable.add_column(col)
    for row in st["links"]:
        ltable.add_row(str(row["link"]), " <-> ".join(row["endpoints"]), row["impairment"])
    console.print(ltable)


@main.command(help="Run ping between two nodes")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.argument("src")
@click.argument("dst")
@click.option("-c", "count", default=4, show_default=True)
def ping(config: str, src: str, dst: str, count: int) -> None:
    cfg = _load_or_die(config)
    client = _get_client()
    from .deployer import Deployer
    dep = Deployer(cfg, client=client)
    code, out = dep.ping(src, dst, count=count)
    console.print(out)
    sys.exit(code)


@main.command(help="Create a config snapshot")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.option("--label", default="manual")
def snapshot(config: str, label: str) -> None:
    cfg = _load_or_die(config)
    client = _get_client()
    from .deployer import Deployer
    dep = Deployer(cfg, client=client)
    snap = dep.snapshots.capture_all(cfg.nodes, label=label)
    console.print(f"[green]Snapshot saved[/green]: [bold]{snap.name}[/bold]")


@main.command(name="list-snapshots", help="List saved snapshots")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
def list_snapshots(config: str) -> None:
    cfg = _load_or_die(config)
    from .deployer import Deployer
    dep = Deployer(cfg)
    rows = dep.snapshots.list()
    if not rows:
        console.print("No snapshots.")
        return
    table = Table(title="Snapshots")
    for col in ["Name", "Label", "When", "Nodes"]:
        table.add_column(col)
    for r in rows:
        table.add_row(r["name"], r["label"], time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["timestamp"])), ", ".join(r["nodes"]))
    console.print(table)


@main.command(help="Restore a snapshot")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.argument("snapshot_name")
def restore(config: str, snapshot_name: str) -> None:
    cfg = _load_or_die(config)
    client = _get_client()
    from .deployer import Deployer
    dep = Deployer(cfg, client=client)
    nodes_map = {n.name: n for n in cfg.nodes}
    try:
        results = dep.snapshots.restore(snapshot_name, nodes_map)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    for node, msg in results.items():
        console.print(f"  {node}: {msg}")


@main.command(help="Start the Web UI")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8765, show_default=True, type=int)
def ui(config: str, host: str, port: int) -> None:
    cfg = _load_or_die(config)
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn not installed.[/red]")
        sys.exit(1)
    from .web import create_app
    app = create_app(cfg, state_dir=__import__("pathlib").Path.home() / ".netemu" / cfg.prefix)
    console.print(f"[green]Web UI running[/green] at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


@main.command(name="add-node", help="Add a node to a running lab")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.argument("name")
@click.option("--type", "ntype", default="linux", type=click.Choice(["linux", "cisco_iol", "cisco_iosv", "cisco_nxos", "cisco_xrd"]))
@click.option("--image", default=None)
@click.option("--cpu", type=float, default=None)
@click.option("--memory", default=None)
def add_node(config: str, name: str, ntype: str, image: str | None, cpu: float | None, memory: str | None) -> None:
    cfg = _load_or_die(config)
    client = _get_client()
    from .deployer import Deployer
    dep = Deployer(cfg, client=client)
    result = dep.add_node(name, node_type=ntype, image=image, cpu=cpu, memory=memory)
    console.print(f"[green]Added node[/green] {result['name']}")
    ssh = result.get("ssh") or {}
    if ssh:
        console.print(f"  SSH: ssh {ssh.get('user','root')}@{ssh.get('host')} -p {ssh.get('port')}  (password: {ssh.get('password')})")


@main.command(name="add-link", help="Connect two running nodes")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.argument("node_a")
@click.argument("node_b")
@click.option("--ip-a", default=None)
@click.option("--ip-b", default=None)
@click.option("--delay", type=float, default=None)
@click.option("--loss", type=float, default=None)
@click.option("--rate", type=int, default=None)
def add_link(config: str, node_a: str, node_b: str, ip_a: str | None, ip_b: str | None, delay: float | None, loss: float | None, rate: int | None) -> None:
    cfg = _load_or_die(config)
    client = _get_client()
    from .deployer import Deployer
    imp: dict[str, Any] = {}
    if delay is not None: imp["delay_ms"] = delay
    if loss is not None: imp["loss_percent"] = loss
    if rate is not None: imp["rate_kbit"] = rate
    dep = Deployer(cfg, client=client)
    result = dep.add_link(node_a, node_b, ip_a=ip_a, ip_b=ip_b, impairment=imp or None)
    console.print(f"[green]Added link[/green] #{result['link_id']}: {node_a} <-> {node_b}")


if __name__ == "__main__":
    main()
