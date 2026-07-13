"""Hardware evaluation + model recommendation.

probe() inspects the machine GT is running on (RAM, CPU, GPU/VRAM) using only
stdlib + shell commands — no psutil, so it stays dependency-free and works the
same on the Windows target PC and the Mac build machine.

recommend() maps that hardware to a model line-up. GT deliberately caps out at
14B: anything bigger (27B+) is too slow for interactive coding on consumer
hardware, so the largest tier is 1.5B + 8B + 14B, all served by Ollama.
"""

import os
import platform
import re
import subprocess


def _run(cmd, timeout=8):
    """Run a probe command quietly; return stdout or '' on any failure."""
    try:
        out = subprocess.run(cmd, shell=isinstance(cmd, str),
                             capture_output=True, text=True, errors="replace",
                             timeout=timeout)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _ram_gb():
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024 ** 3)
        if system == "Darwin":
            out = _run(["sysctl", "-n", "hw.memsize"])
            return int(out) / (1024 ** 3) if out else 0
        # Linux
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024 ** 2)
    except Exception:
        pass
    return 0


def _cpu_name():
    system = platform.system()
    if system == "Darwin":
        out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if out:
            return out
    if system == "Windows":
        out = _run('powershell -NoProfile -Command '
                   '"(Get-CimInstance Win32_Processor).Name"')
        if out:
            return out.splitlines()[0].strip()
    if system == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return platform.processor() or platform.machine()


def _gpu():
    """Return (name, vram_gb or None). Best-effort — a missing GPU is fine."""
    # NVIDIA (Windows/Linux) — the case that matters most for Ollama speed.
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits"], timeout=10)
    if out:
        first = out.splitlines()[0]
        parts = [p.strip() for p in first.split(",")]
        if len(parts) >= 2:
            try:
                return parts[0], float(parts[1]) / 1024  # MiB -> GiB
            except ValueError:
                return parts[0], None

    if platform.system() == "Darwin" and platform.machine() == "arm64":
        # Apple Silicon: GPU shares system RAM, so "VRAM" = unified memory.
        return "Apple Silicon (unified memory)", None

    if platform.system() == "Windows":
        out = _run('powershell -NoProfile -Command '
                   '"(Get-CimInstance Win32_VideoController).Name"')
        if out:
            return out.splitlines()[0].strip(), None
    return None, None


def probe() -> dict:
    ram = _ram_gb()
    gpu_name, vram = _gpu()
    return {
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "cpu": _cpu_name(),
        "cores": os.cpu_count() or 0,
        "ram_gb": round(ram, 1),
        "gpu": gpu_name,
        "vram_gb": round(vram, 1) if vram else None,
    }


def slow_for_large_models(hw: dict) -> bool:
    """True when a 14B would run on the CPU and crawl (single-digit tok/s).

    What makes a large model usable is GPU acceleration:
      - a discrete GPU with VRAM (NVIDIA) — fast, and
      - Apple Silicon's Metal GPU over unified memory — slow but workable.
    Anything else is an x86 box with only an integrated / absent GPU, so Ollama
    runs on the CPU where a 14B is painfully slow — prefer the 8B there.
    """
    if hw.get("vram_gb"):                       # real VRAM (NVIDIA) → fast
        return False
    arch = (hw.get("arch") or "").lower()
    if "Darwin" in (hw.get("os") or "") and arch in ("arm64", "aarch64"):
        return False                            # Apple Silicon Metal → workable
    return True                                 # CPU-only inference → slow


# --------------------------------------------------------------------------- #
#  Model catalogue + tiering
# --------------------------------------------------------------------------- #

# Everything is served by Ollama — one local provider.
# dl_gb ≈ download size (q4 quant), so the wizard can warn before pulling.
# All Apache-2.0 (Qwen + nomic) — no Meta Llama licence anywhere in the lineup.
CATALOG = {
    "qwen2.5:0.5b":     {"params": "0.5B", "dl_gb": 0.4, "job": "/turbo speed profile"},
    "qwen2.5:1.5b":     {"params": "1.5B", "dl_gb": 1.0, "job": "router + quick answers + analyst"},
    "qwen3:8b":         {"params": "8B",  "dl_gb": 5.2, "job": "everyday coding"},
    "qwen3:14b":        {"params": "14B", "dl_gb": 9.3, "job": "heavy coding & planning"},
    "nomic-embed-text": {"params": "137M", "dl_gb": 0.3, "job": "memory / RAG embeddings"},
}

TIERS = {
    "full": {
        "label": "Full (1.5B + 8B + 14B)",
        # reviewer rides on the 1.5B, not the 8B: small-model-first keeps ONE
        # model resident, and a background 8B reviewer would evict it every task.
        "lineup": {"brain": "qwen3:14b", "fast": "qwen3:8b",
                   "tiny": "qwen2.5:1.5b", "reviewer": "qwen2.5:1.5b",
                   "embed": "nomic-embed-text"},
    },
    "standard": {
        "label": "Standard (1.5B + 8B)",
        "lineup": {"brain": "qwen3:8b", "fast": "qwen3:8b",
                   "tiny": "qwen2.5:1.5b", "reviewer": "qwen2.5:1.5b",
                   "embed": "nomic-embed-text"},
    },
    "minimum": {
        "label": "Minimum (1.5B only)",
        "lineup": {"brain": "qwen2.5:1.5b", "fast": "qwen2.5:1.5b",
                   "tiny": "qwen2.5:1.5b", "reviewer": "qwen2.5:1.5b",
                   "embed": "nomic-embed-text"},
    },
}


def recommend(hw: dict) -> dict:
    """Pick the biggest tier this machine can run comfortably.

    A dedicated GPU with enough VRAM qualifies for a tier even when system RAM
    alone wouldn't (Ollama loads the weights into VRAM). 27B+ is never
    recommended — too slow for an interactive agent on consumer hardware.
    """
    ram = hw.get("ram_gb") or 0
    vram = hw.get("vram_gb") or 0

    if ram >= 16 or vram >= 11:
        tier = "full"
        reason = (f"{ram:.0f} GB RAM"
                  + (f" + {vram:.0f} GB VRAM" if vram else "")
                  + " comfortably fits a 14B model — the sweet spot between "
                    "quality and speed. Bigger models (27B+) would be too slow.")
    elif ram >= 10 or vram >= 7:
        tier = "standard"
        reason = (f"{ram:.0f} GB RAM fits an 8B model well; a 14B would swap "
                  "and crawl. 8B is the best brain for this machine.")
    else:
        tier = "minimum"
        reason = (f"Only {ram:.0f} GB RAM — a 3B model is the safe choice; "
                  "anything bigger would starve the rest of the system.")

    lineup = dict(TIERS[tier]["lineup"])
    needed = sorted(set(lineup.values()),
                    key=lambda m: CATALOG.get(m, {}).get("dl_gb", 0))
    return {
        "tier": tier,
        "label": TIERS[tier]["label"],
        "reason": reason,
        "lineup": lineup,
        "needed": needed,
        "total_dl_gb": round(sum(CATALOG.get(m, {}).get("dl_gb", 0)
                                 for m in needed), 1),
    }
