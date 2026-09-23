"""Explicit, repeatable host setup. No privileged shell or root Python process."""

import getpass
import json
import os
import platform
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

FEDORA = [
    "qemu-kvm",
    "qemu-img",
    "libvirt-daemon-kvm",
    "libvirt-client",
    "libvirt-devel",
    "gcc",
    "python3-devel",
    "pkgconf-pkg-config",
    "edk2-ovmf",
    "xorriso",
    "openssh-clients",
    "curl",
    "policycoreutils-python-utils",
]
UBUNTU = [
    "qemu-kvm",
    "qemu-utils",
    "libvirt-daemon-system",
    "libvirt-clients",
    "libvirt-dev",
    "build-essential",
    "python3-dev",
    "pkg-config",
    "ovmf",
    "xorriso",
    "openssh-client",
    "curl",
    "apparmor",
    "apparmor-utils",
]


@dataclass
class Step:
    description: str
    argv: list[str]
    privileged: bool = True

    def command(self) -> list[str]:
        return (["sudo"] if self.privileged and os.geteuid() != 0 else []) + self.argv


def probe(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def distro() -> str:
    release = platform.freedesktop_os_release()
    key = (release.get("ID"), release.get("VERSION_ID"))
    if key not in [("fedora", "44"), ("ubuntu", "26.04")]:
        raise ValueError(
            f"Automatic setup supports Fedora 44 and Ubuntu 26.04, found {key}. See docs/host-installation.md for manual setup."
        )
    if platform.machine() != "x86_64":
        raise ValueError("Automatic setup currently supports x86-64 hosts")
    return str(key[0])


def package_steps(system: str, full: bool = False) -> list[Step]:
    packages = list(FEDORA if system == "fedora" else UBUNTU)
    if full:
        packages += [
            "mtools",
            "dosfstools",
            "zstd",
            "guestfs-tools" if system == "fedora" else "libguestfs-tools",
        ]
    if system == "fedora":
        return [
            Step(
                "Install virtualization prerequisites",
                ["dnf", "install", "-y", *packages],
            )
        ]
    return [
        Step("Refresh package indexes", ["apt-get", "update"]),
        Step(
            "Install virtualization prerequisites",
            ["apt-get", "install", "-y", *packages],
        ),
    ]


def daemon_steps(system: str) -> list[Step]:
    modular = any(
        probe(["systemctl", "is-active", unit]).returncode == 0
        for unit in ("virtqemud.socket", "virtqemud.service")
    )
    monolithic = any(
        probe(["systemctl", "is-active", unit]).returncode == 0
        for unit in ("libvirtd.socket", "libvirtd.service")
    )
    if modular and monolithic:
        raise ValueError(
            "Both modular and monolithic libvirt daemons are active; resolve this before setup"
        )
    installed_modular = (
        probe(
            ["systemctl", "show", "-p", "LoadState", "--value", "virtqemud.socket"]
        ).stdout.strip()
        == "loaded"
    )
    units = (
        ["libvirtd.socket"]
        if monolithic or (not modular and not installed_modular and system == "ubuntu")
        else [
            "virtqemud.socket",
            "virtnetworkd.socket",
            "virtstoraged.socket",
            "virtnodedevd.socket",
            "virtnwfilterd.socket",
            "virtsecretd.socket",
        ]
    )
    return [
        Step(
            "Enable the existing libvirt daemon family",
            ["systemctl", "enable", "--now", *units],
        )
    ]


def storage_steps() -> list[Step]:
    return [
        Step(
            "Define dedicated storage pool if absent",
            [
                "virsh",
                "-c",
                "qemu:///system",
                "pool-define-as",
                "cabrita",
                "dir",
                "--target",
                "/var/lib/libvirt/images/cabrita",
            ],
        ),
        Step(
            "Create pool directory if absent",
            ["virsh", "-c", "qemu:///system", "pool-build", "cabrita"],
        ),
        Step(
            "Start storage pool if inactive",
            ["virsh", "-c", "qemu:///system", "pool-start", "cabrita"],
        ),
        Step(
            "Enable pool autostart",
            ["virsh", "-c", "qemu:///system", "pool-autostart", "cabrita"],
        ),
    ]


def run_steps(steps: list[Step], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as output:
        for step in steps:
            argv = step.command()
            # Inspect with the same authorization used for mutations.
            if "pool-" in " ".join(step.argv):
                prefix = (["sudo"] if os.geteuid() != 0 else []) + [
                    "virsh",
                    "-c",
                    "qemu:///system",
                ]
                import xml.etree.ElementTree as ET

                existing = probe([*prefix, "pool-dumpxml", "cabrita"])
                if existing.returncode == 0:
                    root = ET.fromstring(existing.stdout)
                    if (
                        root.get("type") != "dir"
                        or root.findtext("target/path")
                        != "/var/lib/libvirt/images/cabrita"
                    ):
                        raise ValueError(
                            "Existing cabrita pool has a different target/type; refusing to adopt it"
                        )
                    if "pool-define-as" in step.argv:
                        continue
                if (
                    "pool-build" in step.argv
                    and Path("/var/lib/libvirt/images/cabrita").exists()
                ):
                    continue
                if "pool-start" in step.argv:
                    active = probe([*prefix, "pool-list", "--name"]).stdout.splitlines()
                    if "cabrita" in active:
                        continue
            output.write("$ " + shlex.join(argv) + "\n")
            output.flush()
            result = subprocess.run(
                argv, stdout=output, stderr=subprocess.STDOUT, check=False
            )
            if result.returncode:
                raise RuntimeError(
                    f"{step.description} failed ({result.returncode}); see {log}"
                )


def authorization_step() -> Step:
    return Step(
        "Allow this user to manage system libvirt (new login required)",
        ["usermod", "-aG", "libvirt", os.environ.get("SUDO_USER", getpass.getuser())],
    )


def save_plan(steps: list[Step]) -> str:
    return json.dumps([asdict(step) for step in steps], indent=2)


def shell_plan(steps: list[Step]) -> str:
    return "\n".join(
        f"# {step.description}\n{shlex.join(step.command())}" for step in steps
    )


def active_firewall() -> str:
    firewalld = probe(["firewall-cmd", "--state"]).stdout.strip() == "running"
    ufw = probe(["ufw", "status"]) if shutil.which("ufw") else None
    if ufw and ufw.returncode:
        ufw = probe(["sudo", "-n", "ufw", "status"])
    if ufw and ufw.returncode:
        raise RuntimeError(
            "Cannot inspect UFW status; authenticate sudo and rerun host setup/doctor. Firewall state is unknown."
        )
    ufw_active = ufw is not None and "Status: active" in ufw.stdout
    if firewalld and ufw_active:
        raise ValueError(
            "Both firewalld and UFW are active; resolve the competing managers first"
        )
    if firewalld:
        return "firewalld"
    if ufw_active:
        return "ufw"
    # nft list may be inaccessible; never report inaccessible policy as empty.
    if shutil.which("nft"):
        rules = probe(["nft", "-j", "list", "ruleset"])
        if rules.returncode:
            rules = probe(["sudo", "-n", "nft", "-j", "list", "ruleset"])
        if rules.returncode:
            return "unknown"
        try:
            entries = json.loads(rules.stdout).get("nftables", [])
        except ValueError, AttributeError:
            return "unknown"
        chains = {
            (chain.get("family"), chain.get("table"), chain.get("name"))
            for entry in entries
            if (chain := entry.get("chain"))
        }
        occupied = {
            (rule.get("family"), rule.get("table"), rule.get("chain"))
            for entry in entries
            if (rule := entry.get("rule"))
        }
        for entry in entries:
            rule = entry.get("rule")
            chain = entry.get("chain")
            if rule and not rule.get("table", "").startswith("libvirt"):
                # Disabled UFW retains counters and jumps into empty chains.
                # Ignore only those verified inert rules, never real policy.
                expressions = rule.get("expr", [])
                inert = bool(expressions) and ufw is not None
                for expression in expressions:
                    if set(expression) == {"counter"}:
                        continue
                    target = expression.get("jump", {}).get("target", "")
                    key = (rule.get("family"), rule.get("table"), target)
                    if not (
                        set(expression) == {"jump"}
                        and target.startswith(("ufw-", "ufw6-"))
                        and key in chains
                        and key not in occupied
                    ):
                        inert = False
                if not inert:
                    return "custom"
            if (
                chain
                and chain.get("policy", "accept") != "accept"
                and not chain.get("table", "").startswith("libvirt")
            ):
                return "custom"
    return "none"
