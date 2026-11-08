"""Benchmark monitor: run a command and sample vitals for its whole process tree.

Development tool only. It is never imported by the `videocompress` package and
is not bundled into the PyInstaller executables. Requires the `dev` extra
(`pip install -e .[dev]`) for `psutil`.

Measures, for the command and every process it starts:
  - CPU time: exact on Linux/macOS (OS resource accounting of finished child
    processes); sampled every interval on Windows, where very short-lived
    processes can be missed.
  - Host-wide CPU, peak resident memory (sampled).
  - GPU: per-engine busy time from DRM fdinfo on Linux (Intel i915/xe and AMD
    amdgpu, no root needed), and NVIDIA SM/encoder/decoder utilization from
    `nvidia-smi` on any OS (first NVIDIA GPU, GPU-wide rather than per process).

The command's own output is discarded; a single JSON summary line is printed
when it exits.

Usage:
    python tools/bench_vitals.py --label v120_4k --output out.mp4 -- python -m videocompress input-4K60.mp4 out.mp4 100 hevc

Options:
    --label     Name stored in the JSON summary (required).
    --output    Encoded file whose size is reported as output_mb.
    --csv       Write per-sample rows to this CSV file.
    --interval  Sampling interval in seconds (default 0.5).
"""

import argparse
import csv
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple, Union

import psutil

try:
    import resource
except ImportError:
    resource = None

log = logging.getLogger("vitals")

EngineTotals = Dict[str, int]
Summary = Dict[str, Union[float, str]]
IDLE_FIELDS = ("idle", "iowait")
GUEST_FIELDS = ("guest", "guest_nice")


def host_busy_seconds(times: NamedTuple) -> float:
    """Return busy CPU seconds from psutil.cpu_times() on any OS.

    Idle/iowait are excluded, and guest time is skipped because Linux already
    counts it inside user/nice.

    Example:
        >>> host_busy_seconds(psutil.cpu_times()) >= 0
        True
    """
    return sum(
        value for name, value in zip(times._fields, times)
        if name not in IDLE_FIELDS + GUEST_FIELDS
    )


def children_cpu_seconds() -> Optional[float]:
    """Return user+system CPU seconds of all finished, waited-for descendants (POSIX only).

    Example:
        >>> children_cpu_seconds() is None or children_cpu_seconds() >= 0
        True
    """
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def read_drm_engines(pid: int) -> Dict[str, EngineTotals]:
    """Return cumulative engine ns per DRM client id for one process.

    Example:
        >>> isinstance(read_drm_engines(1), dict)
        True
    """
    clients: Dict[str, EngineTotals] = {}
    fdinfo_dir = Path(f"/proc/{pid}/fdinfo")
    try:
        entries = list(fdinfo_dir.iterdir())
    except OSError:
        return clients
    for entry in entries:
        try:
            text = entry.read_text()
        except OSError:
            continue
        if "drm-client-id" not in text:
            continue
        fields = dict(
            line.split(":", 1) for line in text.splitlines() if ":" in line
        )
        client_id = fields["drm-client-id"].strip()
        engines = {
            key.replace("drm-engine-", ""): int(value.strip().split()[0])
            for key, value in fields.items()
            if key.startswith("drm-engine-") and not key.startswith("drm-engine-capacity")
        }
        clients[client_id] = engines
    return clients


def read_nvidia() -> Optional[Tuple[float, float, float]]:
    """Return (sm%, enc%, dec%) from nvidia-smi, or None when unavailable.

    Example:
        >>> read_nvidia() is None or len(read_nvidia()) == 3
        True
    """
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0, check=True,
        ).stdout.splitlines()[0]
        sm, enc, dec = (float(v) for v in out.split(","))
        return sm, enc, dec
    except (subprocess.SubprocessError, ValueError, IndexError) as exc:
        log.debug("nvidia-smi sample failed: %s", exc)
        return None


def tree(root: psutil.Process) -> List[psutil.Process]:
    """Return the root process plus all live descendants.

    Example:
        >>> len(tree(psutil.Process())) >= 1
        True
    """
    try:
        return [root] + root.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def monitor(cmd: List[str], interval: float, csv_path: Optional[Path]) -> Summary:
    """Run cmd to completion while sampling vitals; return a summary dict.

    Example:
        >>> import sys
        >>> monitor([sys.executable, "-c", "import time; time.sleep(1)"], 0.5, None)["wall_s"] > 0.9
        True
    """
    psutil.cpu_percent(None)
    host_t0 = psutil.cpu_times()
    rusage_t0 = children_cpu_seconds()
    start = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        root: Optional[psutil.Process] = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        root = None
    cpu_time_seen: Dict[int, float] = {}
    engine_seen: Dict[Tuple[int, str], EngineTotals] = {}
    rows: List[Dict[str, float]] = []
    prev_engine_sum: EngineTotals = {}
    prev_t = start
    peak_rss = 0

    while proc.poll() is None:
        time.sleep(interval)
        now = time.monotonic()
        procs = tree(root) if root is not None else []
        rss = 0
        for p in procs:
            try:
                with p.oneshot():
                    t = p.cpu_times()
                    cpu_time_seen[p.pid] = t.user + t.system
                    rss += p.memory_info().rss
                for client, engines in read_drm_engines(p.pid).items():
                    engine_seen[(p.pid, client)] = engines
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        peak_rss = max(peak_rss, rss)
        engine_sum: EngineTotals = {}
        for engines in engine_seen.values():
            for name, ns in engines.items():
                engine_sum[name] = engine_sum.get(name, 0) + ns
        dt = now - prev_t
        row: Dict[str, float] = {
            "t": round(now - start, 2),
            "host_cpu_pct": psutil.cpu_percent(None),
            "tree_cpu_s": round(sum(cpu_time_seen.values()), 2),
            "rss_mb": round(rss / 2**20, 1),
        }
        for name, ns in engine_sum.items():
            row[f"gpu_{name}_pct"] = round(100.0 * (ns - prev_engine_sum.get(name, 0)) / (dt * 1e9), 1)
        nv = read_nvidia()
        if nv is not None:
            row["nv_sm_pct"], row["nv_enc_pct"], row["nv_dec_pct"] = nv
        rows.append(row)
        prev_engine_sum, prev_t = engine_sum, now

    wall = time.monotonic() - start
    host_busy_s = host_busy_seconds(psutil.cpu_times()) - host_busy_seconds(host_t0)
    rusage_t1 = children_cpu_seconds()
    if rusage_t0 is not None and rusage_t1 is not None:
        cpu_s, cpu_source = rusage_t1 - rusage_t0, "exact"
    else:
        cpu_s, cpu_source = sum(cpu_time_seen.values()), "sampled"
    logical = psutil.cpu_count() or 1
    summary: Summary = {
        "exit": proc.returncode,
        "wall_s": round(wall, 1),
        "tree_cpu_s": round(cpu_s, 1),
        "tree_cpu_source": cpu_source,
        "host_busy_cpu_s": round(host_busy_s, 1),
        "avg_tree_cpu_cores": round(cpu_s / wall, 2) if wall else 0.0,
        "avg_tree_cpu_pct_of_host": round(100.0 * cpu_s / wall / logical, 1) if wall else 0.0,
        "avg_host_cpu_pct": round(sum(r["host_cpu_pct"] for r in rows) / len(rows), 1) if rows else 0.0,
        "peak_rss_mb": round(peak_rss / 2**20, 1),
    }
    for name, ns in prev_engine_sum.items():
        summary[f"gpu_{name}_busy_pct"] = round(100.0 * ns / (wall * 1e9), 1) if wall else 0.0
    nv_rows = [r for r in rows if "nv_sm_pct" in r]
    for key in ("nv_sm_pct", "nv_enc_pct", "nv_dec_pct"):
        if nv_rows:
            summary[f"avg_{key}"] = round(sum(r[key] for r in nv_rows) / len(nv_rows), 1)

    if csv_path is not None and rows:
        keys = sorted({k for r in rows for k in r}, key=lambda k: (k != "t", k))
        with csv_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
    return summary


def main() -> None:
    """Parse args, run the monitored command, print a JSON summary line."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None, help="Encoded file to report size of")
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        parser.error("no command given; put it after `--`")
    try:
        summary = monitor(cmd, args.interval, args.csv)
    except OSError as e:
        parser.error(f"could not start {cmd[0]!r}: {e}")
    if args.output is not None and args.output.exists():
        summary["output_mb"] = round(args.output.stat().st_size / 2**20, 2)
    print(json.dumps({"label": args.label, **summary}))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
