"""Hardware probe -> tier. CPU cores, RAM, AVX2/AVX-512, NVIDIA VRAM via nvidia-smi, other GPUs via WMI."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass

import psutil

TIERS = ["T0", "T1", "T2", "T3", "T4"]


@dataclass
class Hardware:
    cpu: str
    physical_cores: int
    logical_cores: int
    ram_gb: float
    avx2: bool
    avx512: bool
    gpu_name: str | None
    vram_gb: float | None
    gpu_vendor: str | None  # nvidia | amd | intel | None

    def as_dict(self) -> dict:
        return asdict(self)


def _cpu_flags() -> tuple[bool, bool]:
    """AVX2 / AVX-512 detection without extra deps. Falls back to 'assume AVX2' on x86-64."""
    try:
        from cpuinfo import get_cpu_info  # optional

        flags = set(get_cpu_info().get("flags", []))
        return "avx2" in flags, any(f.startswith("avx512") for f in flags)
    except Exception:
        pass
    if platform.machine().lower() in ("amd64", "x86_64"):
        return True, False
    return False, False


def _nvidia() -> tuple[str, float] | None:
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    if not os.path.exists(exe):
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()
        if not out:
            return None
        name, mem = out[0].rsplit(",", 1)
        return name.strip(), float(mem) / 1024
    except Exception:
        return None


def _wmi_gpu() -> tuple[str, float | None, str | None] | None:
    """Any GPU via WMI (AdapterRAM caps at 4 GB on many drivers, so treat it as a hint)."""
    try:
        ps = ["powershell", "-NoProfile", "-Command",
              "Get-CimInstance Win32_VideoController | Select-Object -First 1 Name,AdapterRAM | ConvertTo-Json"]
        out = subprocess.run(ps, capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        import json

        d = json.loads(out)
        name = d.get("Name") or ""
        ram = d.get("AdapterRAM")
        vendor = "nvidia" if "nvidia" in name.lower() else "amd" if ("amd" in name.lower() or "radeon" in name.lower()) else "intel" if "intel" in name.lower() else None
        return name, (ram / 2**30 if ram else None), vendor
    except Exception:
        return None


def probe() -> Hardware:
    avx2, avx512 = _cpu_flags()
    gpu_name = vram = vendor = None
    nv = _nvidia()
    if nv:
        gpu_name, vram, vendor = nv[0], nv[1], "nvidia"
    else:
        w = _wmi_gpu()
        if w:
            gpu_name, vram, vendor = w
    return Hardware(
        cpu=platform.processor() or platform.machine(),
        physical_cores=psutil.cpu_count(logical=False) or 1,
        logical_cores=psutil.cpu_count(logical=True) or 1,
        ram_gb=round(psutil.virtual_memory().total / 2**30, 1),
        avx2=avx2, avx512=avx512,
        gpu_name=gpu_name, vram_gb=(round(vram, 1) if vram else None), gpu_vendor=vendor,
    )


def choose_tier(hw: Hardware) -> tuple[str, str]:
    """Returns (tier, human reason). Mirrors PLAN.md 2.6. Only NVIDIA/AMD/Intel dGPUs with reliable VRAM
    numbers unlock GPU tiers; everything else is CPU."""
    v = hw.vram_gb or 0
    if hw.gpu_vendor == "nvidia" and v >= 22:
        return "T4", f"{hw.gpu_name} with {v:.0f} GB VRAM"
    if hw.gpu_vendor == "nvidia" and v >= 11:
        return "T3", f"{hw.gpu_name} with {v:.0f} GB VRAM"
    if hw.gpu_vendor in ("nvidia", "amd") and v >= 6:
        return "T2", f"{hw.gpu_name} with {v:.0f} GB VRAM"
    if hw.ram_gb >= 15:
        return "T1", f"CPU only, {hw.ram_gb:.0f} GB RAM"
    return "T0", f"CPU only, {hw.ram_gb:.0f} GB RAM"
