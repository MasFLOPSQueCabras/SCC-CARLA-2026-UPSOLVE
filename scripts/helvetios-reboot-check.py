#!/usr/bin/env python3
"""Reboot the three installed team nodes and verify new boot IDs and sudo access."""

import json
import subprocess
import time
from pathlib import Path
from tempfile import mkdtemp

ROOT = Path(__file__).resolve().parents[1]


def ssh(node, command):
    return subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            "-i",
            str(Path.home() / ".ssh/carla_scc_ed25519"),
            "-J",
            "scc-bastion",
            f"scct-2672@10.2.72.{node}",
            command,
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def main():
    output = Path(
        mkdtemp(prefix="reboot.", dir=ROOT / "test-results/helvetios-submission")
    )
    before = {}
    for node in (1, 2, 3):
        result = ssh(node, "sudo -n true && cat /proc/sys/kernel/random/boot_id")
        result.check_returncode()
        before[node] = result.stdout.strip()
    (output / "before.json").write_text(json.dumps(before, indent=2))
    for node in before:
        result = ssh(node, "sudo -n systemctl reboot")
        (output / f"request-node{node}.log").write_text(result.stdout + result.stderr)
        if result.returncode not in (0, 255):
            result.check_returncode()
    deadline = time.monotonic() + 600
    remaining = set(before)
    after = {}
    while remaining and time.monotonic() < deadline:
        for node in sorted(remaining):
            try:
                result = ssh(
                    node, "sudo -n true && cat /proc/sys/kernel/random/boot_id"
                )
            except subprocess.TimeoutExpired:
                continue
            if result.returncode == 0 and result.stdout.strip() != before[node]:
                after[node] = result.stdout.strip()
                remaining.remove(node)
                print(
                    f"node{node}: new boot ID and passwordless sudo verified",
                    flush=True,
                )
        if remaining:
            time.sleep(10)
    (output / "after.json").write_text(json.dumps(after, indent=2))
    if remaining:
        raise SystemExit(
            f"Reboot verification timed out for {sorted(remaining)}; evidence: {output}"
        )
    print(f"Reboot evidence: {output}")


if __name__ == "__main__":
    main()
