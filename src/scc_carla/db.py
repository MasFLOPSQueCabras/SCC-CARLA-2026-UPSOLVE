from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import turso


class NodeLifecycle(StrEnum):
    UNPROVISIONED = "UNPROVISIONED"
    INSTALLING = "INSTALLING"
    BOOTSTRAPPED = "BOOTSTRAPPED"
    READY = "READY"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True)
class NodeState:
    node_id: int
    hostname: str
    os_ip: str
    bmc_ip: str
    state: NodeLifecycle
    pubkey: str | None = None
    bios_profile: str | None = None
    last_updated: str | None = None


@contextmanager
def get_db(db_path: Path) -> Generator[turso.Connection]:
    conn = turso.connect(str(db_path))
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path, team_id: int) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    with get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                node_id INTEGER PRIMARY KEY,
                hostname TEXT NOT NULL,
                os_ip TEXT NOT NULL,
                bmc_ip TEXT NOT NULL,
                state TEXT NOT NULL,
                pubkey TEXT,
                bios_profile TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.executemany(
            """
            INSERT OR IGNORE INTO nodes (node_id, hostname, os_ip, bmc_ip, state, bios_profile, last_updated)
            VALUES (?, ?, ?, ?, ?, 'factory_baseline', ?)
            """,
            [
                (
                    node_id,
                    f"node{node_id}",
                    f"10.2.{team_id}.{node_id}",
                    f"10.1.{team_id}.{node_id}",
                    NodeLifecycle.UNPROVISIONED.value,
                    now,
                )
                for node_id in (1, 2, 3)
            ],
        )


def ensure_db(db_path: Path, team_id: int) -> None:
    if not db_path.exists():
        init_db(db_path, team_id)


def get_all_nodes(db_path: Path, team_id: int) -> list[NodeState]:
    ensure_db(db_path, team_id)
    with get_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT node_id, hostname, os_ip, bmc_ip, state, pubkey, bios_profile, last_updated FROM nodes ORDER BY node_id"
        )
        return [
            NodeState(
                node_id=r[0],
                hostname=r[1],
                os_ip=r[2],
                bmc_ip=r[3],
                state=(
                    NodeLifecycle(r[4])
                    if r[4] in NodeLifecycle._value2member_map_
                    else NodeLifecycle.UNPROVISIONED
                ),
                pubkey=r[5],
                bios_profile=r[6],
                last_updated=r[7],
            )
            for r in cur.fetchall()
        ]


def reset_cluster_state(db_path: Path) -> None:
    if not db_path.exists():
        return
    with get_db(db_path) as conn:
        cur = conn.cursor()
        now = datetime.now(UTC).isoformat()
        cur.execute(
            "UPDATE nodes SET state = ?, pubkey = NULL, bios_profile = 'factory_baseline', last_updated = ?",
            (NodeLifecycle.UNPROVISIONED.value, now),
        )


def update_node_state(
    db_path: Path,
    node_id: int,
    state: NodeLifecycle,
    pubkey: str | None = None,
) -> None:
    with get_db(db_path) as conn:
        cur = conn.cursor()
        now = datetime.now(UTC).isoformat()
        if pubkey is not None:
            cur.execute(
                "UPDATE nodes SET state = ?, pubkey = ?, last_updated = ? WHERE node_id = ?",
                (state.value, pubkey, now, node_id),
            )
        else:
            cur.execute(
                "UPDATE nodes SET state = ?, last_updated = ? WHERE node_id = ?",
                (state.value, now, node_id),
            )
