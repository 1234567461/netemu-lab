"""Container node lifecycle, SSH bootstrap and interactive exec."""

from __future__ import annotations

import secrets
from typing import Any, Iterator

import docker
from docker.errors import APIError, ImageNotFound

from .config import NodeConfig
from .utils import container_name


class NodeManager:
    """Creates, starts, inspects and removes container nodes."""

    DEFAULT_SSH_PASSWORD = "netemu"

    def __init__(self, client: docker.DockerClient, prefix: str):
        self.client = client
        self.prefix = prefix

    def _ensure_image(self, image: str) -> None:
        try:
            self.client.images.get(image)
        except ImageNotFound:
            self.client.images.pull(image)

    def create(self, node: NodeConfig, ssh_port: int | None = None) -> Any:
        """Create (but do not start) a node container.

        If ``ssh_port`` is given, TCP/22 is published to that host port so
        the node can be SSH'd into.
        """
        self._ensure_image(node.image)
        name = container_name(self.prefix, node.name)

        host_config_kwargs: dict[str, Any] = {
            "auto_remove": False,
            "privileged": node.privileged,
        }
        if node.cpu is not None:
            host_config_kwargs["cpu_quota"] = int(node.cpu * 100_000)
            host_config_kwargs["cpu_period"] = 100_000
        if node.memory is not None:
            host_config_kwargs["mem_limit"] = node.memory

        port_bindings: dict[str, Any] = {}
        if ssh_port is not None:
            port_bindings["22/tcp"] = ssh_port
        for p in node.ports:
            port_bindings[f"{p.container}/{p.protocol}"] = p.host
        if port_bindings:
            host_config_kwargs["port_bindings"] = port_bindings

        if node.cap_add:
            host_config_kwargs["cap_add"] = node.cap_add

        command = node.command or "sleep infinity"

        container = self.client.containers.create(
            image=node.image,
            command=command,
            name=name,
            hostname=node.name,
            detach=True,
            environment=node.env or None,
            stdin_open=True,
            tty=True,
            labels={
                "netemu.node": node.name,
                "netemu.prefix": self.prefix,
                "netemu.ssh_port": str(ssh_port or ""),
            },
            **host_config_kwargs,
        )
        return container

    def start(self, node: NodeConfig, ssh_port: int | None = None) -> Any:
        c = self.create(node, ssh_port=ssh_port)
        c.start()
        c.reload()
        if ssh_port is not None:
            self.bootstrap_ssh(node.name)
        return c

    def get(self, node: str) -> Any:
        return self.client.containers.get(container_name(self.prefix, node))

    # ------------------------------------------------------------ ssh

    def bootstrap_ssh(self, node: str, password: str = DEFAULT_SSH_PASSWORD) -> dict[str, Any]:
        """Install + start sshd inside the container, set root password.

        Returns connection info {host, port, user, password}.
        """
        c = self.get(node)
        # Best-effort: debian/alpine style installs. netshoot is debian-based.
        bootstrap = (
            "sh -c '"
            "command -v apt-get >/dev/null && "
            "  (apt-get update -qq && apt-get install -y -qq openssh-server >/dev/null 2>&1); "
            "command -v apk >/dev/null && apk add --no-cache openssh >/dev/null 2>&1; "
            "mkdir -p /run/sshd /root/.ssh; "
            "echo root:'" + password + "' | chpasswd; "
            "sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config 2>/dev/null; "
            "sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config 2>/dev/null; "
            "(sshd 2>/dev/null || /usr/sbin/sshd 2>/dev/null) || true"
            "'"
        )
        c.exec_run(["bash", "-c", bootstrap], stdout=True, stderr=True)
        c.reload()
        ports = c.attrs["NetworkSettings"]["Ports"] or {}
        mapped = (ports.get("22/tcp") or [{}])[0]
        return {
            "host": "127.0.0.1",
            "port": int(mapped.get("HostPort", 22)),
            "user": "root",
            "password": password,
        }

    # ------------------------------------------------------------ exec

    def exec(self, node: str, cmd: list[str]) -> tuple[int, str]:
        """Run a command inside a node, returning (exit_code, output)."""
        c = self.get(node)
        result = c.exec_run(cmd, stdout=True, stderr=True, demux=False)
        output = result.output.decode("utf-8", errors="replace") if result.output else ""
        return result.exit_code, output

    def exec_interactive(self, node: str, cmd: list[str]) -> Iterator[bytes]:
        """Yield a multiplexed stream from an interactive exec.

        Used by the WebSocket terminal.
        """
        c = self.get(node)
        stream = c.exec_run(
            cmd,
            stdout=True,
            stderr=True,
            stdin=True,
            tty=True,
            stream=True,
            demux=False,
        )
        return stream.output  # type: ignore[return-value]

    def exec_resize(self, node: str, exec_id: str, rows: int, cols: int) -> None:
        try:
            self.client.api.exec_resize(exec_id, height=rows, width=cols)
        except APIError:
            pass

    # ------------------------------------------------------------ misc

    def remove(self, node: str, force: bool = True) -> None:
        try:
            c = self.client.containers.get(container_name(self.prefix, node))
            c.remove(force=force)
        except APIError:
            pass

    def list_lab_nodes(self) -> list[Any]:
        return [
            c
            for c in self.client.containers.list(all=True)
            if (c.labels or {}).get("netemu.prefix") == self.prefix
        ]

    def find_ssh_port(self, node: str) -> int | None:
        c = self.get(node)
        c.reload()
        ports = c.attrs["NetworkSettings"]["Ports"] or {}
        mapping = ports.get("22/tcp")
        if mapping:
            return int(mapping[0]["HostPort"])
        return None
