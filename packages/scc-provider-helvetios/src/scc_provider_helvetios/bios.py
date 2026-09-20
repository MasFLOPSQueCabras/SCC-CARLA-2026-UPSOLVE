import json
from enum import StrEnum
from pathlib import Path
from typing import Any


class BiosProfile(StrEnum):
    HPC = "hpc"
    BASELINE = "baseline"
    LOW_LATENCY = "low_latency"


BIOS_PROFILES: dict[BiosProfile, dict[str, Any]] = {
    BiosProfile.HPC: {
        "WorkloadProfile": "HighPerformanceCompute(HPC)",
        "PowerRegulator": "StaticHighPerf",
        "EnergyPerfBias": "MaxPerf",
        "EnergyEfficientTurbo": "Disabled",
        "ProcTurbo": "Enabled",
        "ProcHyperthreading": "Enabled",
        "NumaGroupSizeOpt": "Clustered",
        "UncoreFreqScaling": "Maximum",
        "SubNumaClustering": "Disabled",
    },
    BiosProfile.BASELINE: {
        "WorkloadProfile": "GeneralPowerEfficientCompute",
        "PowerRegulator": "DynamicPowerSavings",
        "EnergyPerfBias": "BalancedPerf",
        "EnergyEfficientTurbo": "Enabled",
        "ProcTurbo": "Enabled",
        "ProcHyperthreading": "Enabled",
    },
    BiosProfile.LOW_LATENCY: {
        "WorkloadProfile": "LowLatency",
        "PowerRegulator": "StaticHighPerf",
        "EnergyPerfBias": "MaxPerf",
        "EnergyEfficientTurbo": "Disabled",
        "ProcTurbo": "Enabled",
        "ProcHyperthreading": "Disabled",
        "MinProcIdlePower": "NoCStates",
    },
}


def get_profile_attributes(profile: BiosProfile | str) -> dict[str, Any]:
    """Returns the attribute dictionary for a given BIOS profile."""
    if isinstance(profile, str):
        try:
            profile_enum = BiosProfile(profile)
            return BIOS_PROFILES.get(profile_enum, BIOS_PROFILES[BiosProfile.HPC])
        except ValueError:
            return BIOS_PROFILES[BiosProfile.HPC]
    return BIOS_PROFILES.get(profile, BIOS_PROFILES[BiosProfile.HPC])


def load_bios_file(file_path: Path) -> dict[str, Any]:
    """Loads BIOS settings from a JSON file."""
    content = file_path.read_text(encoding="utf-8")
    data = json.loads(content)
    match data:
        case {"Attributes": dict() as attrs}:
            return attrs
        case dict() as direct_attrs:
            return direct_attrs
        case _:
            raise ValueError(f"Invalid BIOS settings JSON file at {file_path}")
