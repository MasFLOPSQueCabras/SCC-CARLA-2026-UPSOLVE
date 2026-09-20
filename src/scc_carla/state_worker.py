import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import turso


def get_file_hash(path: Path | str | None = None) -> str:
    """Computes the SHA-256 hash of the state worker script."""
    target_path = Path(path) if path else Path(__file__)
    return hashlib.sha256(target_path.read_bytes()).hexdigest()


def execute_action(conn: turso.Connection, action: str, args: dict[str, Any]) -> Any:
    """Executes a cluster database action against a Turso connection."""
    cur = conn.cursor()

    if action == "init_db":
        team_id = args["team_id"]
        cur.execute("""
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
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS locks (
                resource TEXT PRIMARY KEY,
                holder TEXT NOT NULL,
                operation TEXT NOT NULL,
                acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                timeout_sec INTEGER NOT NULL DEFAULT 3600
            );
        """)
        for node_id in (1, 2, 3):
            cur.execute(
                """
                INSERT OR IGNORE INTO nodes (node_id, hostname, os_ip, bmc_ip, state, bios_profile, last_updated)
                VALUES (?, ?, ?, ?, ?, "factory_baseline", CURRENT_TIMESTAMP)
                """,
                (
                    node_id,
                    f"node{node_id}",
                    f"10.2.{team_id}.{node_id}",
                    f"10.1.{team_id}.{node_id}",
                    "UNPROVISIONED",
                ),
            )
        conn.commit()
        return True

    elif action == "get_all_nodes":
        execute_action(conn, "init_db", args)
        cur.execute(
            "SELECT node_id, hostname, os_ip, bmc_ip, state, pubkey, bios_profile, last_updated "
            "FROM nodes ORDER BY node_id"
        )
        rows = cur.fetchall()
        return [
            {
                "node_id": r[0],
                "hostname": r[1],
                "os_ip": r[2],
                "bmc_ip": r[3],
                "state": r[4],
                "pubkey": r[5],
                "bios_profile": r[6],
                "last_updated": str(r[7]),
            }
            for r in rows
        ]

    elif action == "update_node_state":
        node_id = args["node_id"]
        fields = ["last_updated = CURRENT_TIMESTAMP"]
        params: list[Any] = []
        if args.get("state") is not None:
            fields.append("state = ?")
            params.append(args["state"])
        if args.get("pubkey") is not None:
            fields.append("pubkey = ?")
            params.append(args["pubkey"])
        if args.get("bios_profile") is not None:
            fields.append("bios_profile = ?")
            params.append(args["bios_profile"])
        params.append(node_id)
        set_clause = ", ".join(fields)
        cur.execute(f"UPDATE nodes SET {set_clause} WHERE node_id = ?", tuple(params))
        conn.commit()
        return True

    elif action == "reset_cluster_state":
        cur.execute(
            'UPDATE nodes SET state = "UNPROVISIONED", pubkey = NULL, '
            'bios_profile = "factory_baseline", last_updated = CURRENT_TIMESTAMP'
        )
        conn.commit()
        return True

    elif action == "acquire_locks":
        resources: list[str] = args["resources"]
        holder: str = args["holder"]
        operation: str = args["operation"]
        timeout_sec: int = args.get("timeout_sec", 1800)
        force: bool = args.get("force", False)

        cur.execute(
            'DELETE FROM locks WHERE (unixepoch("now") - unixepoch(acquired_at)) >= timeout_sec'
        )

        if force:
            for r in resources:
                if r == "cluster":
                    cur.execute("DELETE FROM locks")
                else:
                    cur.execute(
                        "DELETE FROM locks WHERE resource IN (?, 'cluster')", (r,)
                    )
        else:
            for r in resources:
                if r == "cluster":
                    cur.execute("""
                        SELECT resource, holder, operation, acquired_at, timeout_sec,
                               (unixepoch("now") - unixepoch(acquired_at)) AS elapsed_sec
                        FROM locks
                    """)
                else:
                    cur.execute(
                        """
                        SELECT resource, holder, operation, acquired_at, timeout_sec,
                               (unixepoch("now") - unixepoch(acquired_at)) AS elapsed_sec
                        FROM locks WHERE resource IN (?, 'cluster')
                    """,
                        (r,),
                    )
                conflicts = cur.fetchall()
                for c in conflicts:
                    c_res, c_holder, c_op, c_acq, c_timeout, c_elapsed = c
                    if (c_holder != holder or c_op != operation) and (
                        c_elapsed < c_timeout
                    ):
                        return {
                            "acquired": False,
                            "conflict": (
                                f"Resource '{c_res}' is locked by {c_holder} for '{c_op}' "
                                f"since {c_acq} ({c_elapsed}s ago, timeout {c_timeout}s)"
                            ),
                        }

        for r in resources:
            cur.execute(
                """
                INSERT OR REPLACE INTO locks (resource, holder, operation, acquired_at, timeout_sec)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
                """,
                (r, holder, operation, timeout_sec),
            )
        conn.commit()
        return {"acquired": True, "conflict": None}

    elif action == "release_locks":
        resources = args["resources"]
        holder = args.get("holder")
        force = args.get("force", False)
        for r in resources:
            if force or not holder:
                cur.execute("DELETE FROM locks WHERE resource = ?", (r,))
            else:
                cur.execute(
                    "DELETE FROM locks WHERE resource = ? AND holder = ?", (r, holder)
                )
        conn.commit()
        return True

    elif action == "get_active_locks":
        cur.execute("""
            SELECT resource, holder, operation, acquired_at, timeout_sec,
                   (unixepoch("now") - unixepoch(acquired_at)) AS elapsed_sec,
                   CASE WHEN (unixepoch("now") - unixepoch(acquired_at)) >= timeout_sec THEN 1 ELSE 0 END AS is_expired
            FROM locks ORDER BY acquired_at
        """)
        return [
            {
                "resource": r[0],
                "holder": r[1],
                "operation": r[2],
                "acquired_at": str(r[3]),
                "timeout_sec": r[4],
                "elapsed_sec": max(0, r[5]),
                "is_expired": bool(r[6]),
            }
            for r in cur.fetchall()
        ]

    elif action == "break_lock":
        resource = args["resource"]
        if resource == "all":
            cur.execute("DELETE FROM locks")
        else:
            cur.execute("DELETE FROM locks WHERE resource = ?", (resource,))
        conn.commit()
        return True

    raise ValueError(f"Unknown action: {action}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--hash":
        print(get_file_hash())
        sys.exit(0)

    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            print(
                json.dumps(
                    {"status": "error", "error": "No JSON payload provided on stdin"}
                )
            )
            sys.exit(1)

        req = json.loads(raw_input)
        db_path = os.path.expanduser(req["db_path"])
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = turso.connect(db_path)
        try:
            res = execute_action(conn, req["action"], req.get("args", {}))
            print(json.dumps({"status": "ok", "result": res}))
        finally:
            conn.close()
    except (turso.Error, json.JSONDecodeError, KeyError, ValueError, OSError) as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
