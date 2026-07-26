from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from services.control_center.models import ConnectionKind  # noqa: E402
from services.control_center.profile_service import build_connection_profile  # noqa: E402
from services.control_center.secrets import KeyringSecretStore  # noqa: E402
from services.control_center.store import ConnectionStore  # noqa: E402


class RebuildSecrets:
    def __init__(
        self,
        keyring: KeyringSecretStore,
        *,
        ssh_password: str,
    ) -> None:
        self._keyring = keyring
        self._ssh_password = ssh_password

    def get(self, connection_id: str, name: str) -> str:
        if name == "ssh_password" and self._ssh_password:
            return self._ssh_password
        return self._keyring.get(connection_id, name)

    def set(self, connection_id: str, name: str, value: str) -> None:
        self._keyring.set(connection_id, name, value)

    def delete(self, connection_id: str, name: str) -> None:
        self._keyring.delete(connection_id, name)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild profiles from the database connections saved by the Studio.",
    )
    parser.add_argument(
        "profiles",
        nargs="*",
        help="Logical profile names to rebuild. Omit to rebuild every saved preset.",
    )
    parser.add_argument(
        "--ssh-username",
        default="",
        help="SSH username used for tunnel connections when it is not saved.",
    )
    parser.add_argument(
        "--skip-tunnels",
        action="store_true",
        help="Only rebuild direct Neo4j connections.",
    )
    parser.add_argument(
        "--input-directory",
        type=Path,
        default=Path(
            os.getenv(
                "T2C_FRAMEWORK_ROOT",
                str(PROJECT_ROOT.parent / "t2c_eval_framework"),
            )
        )
        / "inputs",
        help=(
            "Directory containing optional <profile>.csv learned-query datasets. "
            "Matching files are composed with live schema evidence."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    store = ConnectionStore()
    keyring = KeyringSecretStore()
    requested = {name.casefold() for name in args.profiles}
    connections = [
        connection
        for connection in store.list()
        if connection.id.startswith("preset-")
        and (not requested or connection.profile_name.casefold() in requested)
        and not (args.skip_tunnels and connection.kind == ConnectionKind.SSH_TUNNEL)
    ]
    if requested:
        found = {connection.profile_name.casefold() for connection in connections}
        missing = sorted(requested - found)
        if missing:
            raise SystemExit(f"Unknown profile(s): {', '.join(missing)}")

    tunnel_connections = [
        connection
        for connection in connections
        if connection.kind == ConnectionKind.SSH_TUNNEL
    ]
    ssh_password = ""
    if tunnel_connections:
        stored_passwords = {
            keyring.get(connection.id, "ssh_password")
            for connection in tunnel_connections
        }
        stored_passwords.discard("")
        if len(stored_passwords) == 1:
            ssh_password = stored_passwords.pop()
        else:
            ssh_password = getpass.getpass("SSH password for tunnel profiles: ")

    secrets = RebuildSecrets(keyring, ssh_password=ssh_password)
    failures: list[tuple[str, str]] = []
    for connection in connections:
        if connection.kind == ConnectionKind.SSH_TUNNEL:
            if connection.ssh is None:
                failures.append((connection.profile_name, "SSH configuration is missing"))
                continue
            connection.ssh.ssh_username = (
                connection.ssh.ssh_username or args.ssh_username
            )
            if not connection.ssh.ssh_username:
                failures.append((connection.profile_name, "SSH username is required"))
                continue

        print(f"BUILD {connection.profile_name}", flush=True)
        try:
            output, profile = build_connection_profile(
                project_root=PROJECT_ROOT,
                connection=connection,
                secrets=secrets,
                dataset_directory=args.input_directory,
            )
        except Exception as exc:
            failures.append((connection.profile_name, str(exc)))
            print(f"FAILED {connection.profile_name}: {exc}", flush=True)
            continue

        summary = profile.get("schema_profile", {}).get("summary", {})
        print(
            "DONE "
            f"{connection.profile_name}: "
            f"labels={summary.get('label_count', 0)} "
            f"relationships={summary.get('relationship_type_count', 0)} "
            f"paths={summary.get('schema_path_count', 0)} "
            f"vectors={summary.get('vector_index_count', 0)} "
            f"output={output}",
            flush=True,
        )

    if failures:
        print("\nFailures:", flush=True)
        for profile_name, message in failures:
            print(f"- {profile_name}: {message}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
