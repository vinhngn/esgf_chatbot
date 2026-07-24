from __future__ import annotations

import select
import socketserver
import threading
from typing import Any

from services.control_center.models import SshTunnel


class _ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class _ForwardHandler(socketserver.BaseRequestHandler):
    transport: Any
    target_host: str
    target_port: int

    def handle(self) -> None:
        channel = self.transport.open_channel(
            "direct-tcpip",
            (self.target_host, self.target_port),
            self.request.getpeername(),
        )
        if channel is None:
            return
        try:
            while True:
                readable, _, _ = select.select([self.request, channel], [], [], 1.0)
                if self.request in readable:
                    data = self.request.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(65536)
                    if not data:
                        break
                    self.request.sendall(data)
        finally:
            channel.close()
            self.request.close()


def _handler_type(transport: Any, target_host: str, target_port: int) -> type[_ForwardHandler]:
    return type(
        "ForwardHandler",
        (_ForwardHandler,),
        {
            "transport": transport,
            "target_host": target_host,
            "target_port": target_port,
        },
    )


class SshTunnelSession:
    def __init__(self, config: SshTunnel, password: str) -> None:
        self.config = config
        self.password = password
        self._client: Any = None
        self._server: _ForwardServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._server and self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        if not self.config.ssh_username:
            raise ValueError("SSH username is required")
        if not self.password:
            raise ValueError("SSH password is required")

        try:
            import paramiko
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install paramiko to use SSH tunnel connections") from exc

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.config.jump_host,
            port=self.config.jump_port,
            username=self.config.ssh_username,
            password=self.password,
            look_for_keys=False,
            allow_agent=False,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
        )
        transport = client.get_transport()
        if transport is None:
            client.close()
            raise ConnectionError("SSH transport could not be established")

        handler = _handler_type(
            transport,
            self.config.target_host,
            self.config.remote_port,
        )
        try:
            server = _ForwardServer(("127.0.0.1", self.config.local_port), handler)
        except Exception:
            client.close()
            raise

        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name=f"neo4j-tunnel-{self.config.local_port}",
        )
        thread.start()
        self._client = client
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._client:
            self._client.close()
        self._server = None
        self._thread = None
        self._client = None

    def __enter__(self) -> SshTunnelSession:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
