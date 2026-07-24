from __future__ import annotations

import argparse
import os
import select
import socketserver
import sys
import threading
import time
from dataclasses import dataclass

DEFAULT_PORTS = {
    "company": 15062,
    "fictional_character": 15063,
    "flight_accident": 15064,
    "geography": 15065,
}


@dataclass(frozen=True)
class TunnelConfig:
    local_port: int
    remote_host: str
    remote_port: int


class ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class ForwardHandler(socketserver.BaseRequestHandler):
    ssh_transport: paramiko.Transport
    remote_host: str
    remote_port: int

    def handle(self) -> None:
        try:
            channel = self.ssh_transport.open_channel(
                "direct-tcpip",
                (self.remote_host, self.remote_port),
                self.request.getpeername(),
            )
        except Exception as exc:
            print(f"[tunnel] open_channel failed: {exc}", file=sys.stderr, flush=True)
            return

        if channel is None:
            print("[tunnel] open_channel returned no channel", file=sys.stderr, flush=True)
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


def _connect_ssh(
    *,
    hostname: str,
    username: str,
    password: str,
    sock=None,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=hostname,
        username=username,
        password=password,
        sock=sock,
        look_for_keys=False,
        allow_agent=False,
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
    )
    return client


def _make_handler(transport: paramiko.Transport, remote_host: str, remote_port: int):
    return type(
        "Handler",
        (ForwardHandler,),
        {
            "ssh_transport": transport,
            "remote_host": remote_host,
            "remote_port": remote_port,
        },
    )


def _parse_ports(raw: str) -> list[int]:
    if not raw.strip():
        return list(DEFAULT_PORTS.values())
    values: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if item in DEFAULT_PORTS:
            values.append(DEFAULT_PORTS[item])
        else:
            values.append(int(item))
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="Open local CypherBench Neo4j tunnels through the Temple jump host.")
    parser.add_argument("--jump-host", default="cis-linux2.temple.edu")
    parser.add_argument("--target-host", default="exxacta100.cis.temple.edu")
    parser.add_argument("--username", default=os.getenv("CYPHERBENCH_SSH_USERNAME", ""))
    parser.add_argument("--password-env", default="CYPHERBENCH_SSH_PASSWORD")
    parser.add_argument("--ports", default="company,fictional_character,flight_accident,geography")
    parser.add_argument(
        "--target-ssh",
        action="store_true",
        help="Also SSH-authenticate to the target host. Not needed for plain local port forwarding.",
    )
    args = parser.parse_args()

    global paramiko
    try:
        import paramiko
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "paramiko is required to open the SSH tunnel. Install it in this Python "
            "environment or run this helper with a Python environment that has paramiko."
        ) from exc

    password = os.getenv(args.password_env, "")
    if not args.username or not password:
        raise SystemExit("Set --username and CYPHERBENCH_SSH_PASSWORD before starting the tunnel.")

    jump = _connect_ssh(hostname=args.jump_host, username=args.username, password=password)
    jump_transport = jump.get_transport()
    if jump_transport is None:
        raise SystemExit("Could not open jump transport.")

    target = None
    tunnel_transport = jump_transport
    remote_host = args.target_host
    if args.target_ssh:
        target_sock = jump_transport.open_channel("direct-tcpip", (args.target_host, 22), ("127.0.0.1", 0))
        target = _connect_ssh(
            hostname=args.target_host,
            username=args.username,
            password=password,
            sock=target_sock,
        )
        target_transport = target.get_transport()
        if target_transport is None:
            raise SystemExit("Could not open target transport.")
        tunnel_transport = target_transport
        remote_host = "127.0.0.1"

    servers: list[ForwardServer] = []
    for port in _parse_ports(args.ports):
        handler = _make_handler(tunnel_transport, remote_host, port)
        server = ForwardServer(("127.0.0.1", port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        print(f"[tunnel] 127.0.0.1:{port} -> {args.target_host}:{port}", flush=True)

    print("[tunnel] ready", flush=True)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        if target is not None:
            target.close()
        jump.close()


if __name__ == "__main__":
    main()
