"""Docker network lifecycle for point-to-point links."""

from __future__ import annotations

from typing import Any

import docker
from docker.errors import APIError

from .utils import network_name


class NetworkManager:
    """Manages per-link Docker bridge networks."""

    def __init__(self, client: docker.DockerClient, prefix: str):
        self.client = client
        self.prefix = prefix

    def create_link_network(self, link_index: int, subnet: str | None = None) -> dict[str, Any]:
        """Create an isolated bridge network for one point-to-point link.

        Returns a dict with ``name``, ``id`` and optional subnet/gw.
        """
        name = network_name(self.prefix, link_index)
        ipam_pool = None
        if subnet:
            ipam_pool = docker.types.IPAMPool(subnet=subnet)
        ipam_config = docker.types.IPAMConfig(pool_configs=[ipam_pool] if ipam_pool else None)

        # ``internal=True`` keeps traffic off the host's default route; DNS and
        # userland services still work. We add containers explicitly.
        net = self.client.networks.create(
            name=name,
            driver="bridge",
            ipam=ipam_config,
            internal=False,
            check_duplicate=True,
            labels={"netemu.link": str(link_index), "netemu.prefix": self.prefix},
        )
        return {"name": name, "id": net.id}

    def remove_link_network(self, link_index: int) -> None:
        name = network_name(self.prefix, link_index)
        try:
            net = self.client.networks.get(name)
            net.remove()
        except APIError:
            pass

    def list_lab_networks(self) -> list[Any]:
        return [n for n in self.client.networks.list() if n.name.startswith(f"{self.prefix}-link-")]

    def prune(self) -> None:
        for n in self.list_lab_networks():
            try:
                n.remove()
            except APIError:
                pass
