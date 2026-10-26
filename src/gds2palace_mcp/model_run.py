"""generate_model tool: run a model script and check what it wrote."""

import json
import os
import re
import subprocess
import sys
import time
import uuid

from ._run_model import MARKER, SAVE_GEOMETRY_ENV
from .common import open_log_window

TAIL_LINES = 40
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# Palace unknowns (degrees of freedom) per tetrahedron and FEM order: median and range over the
# 163 Palace runs without AMR in the gds2palace repository (palace.json DegreesOfFreedom /
# MeshElements, 2026-10-22). Palace's MeshElements are the .msh tetrahedra (element type 4).
DOF_PER_TET = {1: (1.350, 1.319, 1.387), 2: (6.775, 6.336, 7.222), 3: (19.875, 19.875, 19.875)}

# Palace peak memory, summed over all MPI processes: setupEM's base fit (setupEM.py, 92 order-2
# runs) 0.5 GB + 11.7 GB per million unknowns, times a factor per (order, complex coarse solve):
# median (typical) and maximum + 10 % (worst case) of real / base over the 158 Palace runs without
# AMR in the gds2palace repository that report PeakMemoryMegabytes (2026-10-22): order 1 n=10/5,
# order 2 n=71/70. Order 1 needs about twice the memory per unknown. Order 3 (2 runs) uses the
# order-2 factors.
RAM_OFFSET_GB, RAM_GB_PER_MDOF = 0.5, 11.7
RAM_FACTORS = {(1, False): (2.14, 2.65), (1, True): (2.97, 3.6),
               (2, False): (0.99, 1.41), (2, True): (1.13, 1.46)}


def _msh_counts(msh_path):
    """Node, element and tetrahedron counts from a gmsh 2.2 ASCII mesh (the format gds2palace writes)."""
    counts = {}
    with open(msh_path, encoding="ascii", errors="replace") as f:
        for line in f:
            section = line.strip()
            if section == "$Nodes":
                counts["nodes"] = int(f.readline().split()[0])
            elif section == "$Elements":
                n = int(f.readline().split()[0])
                counts["elements"] = n
                counts["tetrahedra"] = sum(f.readline().split(None, 2)[1] == "4" for _ in range(n))
                break
    return counts


def _ram_gb(mdof, factors):
    base = RAM_OFFSET_GB + RAM_GB_PER_MDOF * mdof
    return round(factors[0] * base, 1), round(factors[1] * base, 1)


def estimate_resources(tetrahedra, config_path):
    """Palace unknowns and peak RAM from the tetrahedron count and the model's config.json."""
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    order = int(config.get("Solver", {}).get("Order", 2))
    complex_coarse = bool(config.get("Solver", {}).get("Linear", {}).get("ComplexCoarseSolve", False))
    refinement = config.get("Model", {}).get("Refinement", {})
    amr_iterations = int(refinement.get("MaxIts", 0) or 0)
    median, low, high = DOF_PER_TET.get(order, DOF_PER_TET[2])
    dof = tetrahedra * median
    factors = RAM_FACTORS[(min(order, 2), complex_coarse)]
    typical, worst = _ram_gb(dof / 1e6, factors)
    estimate = {
        "order": order,
        "unknowns": int(round(dof, -3)),
        "unknowns_range": [int(round(tetrahedra * low, -3)), int(round(tetrahedra * high, -3))],
        "peak_ram_gb": {"typical": typical, "worst_case": worst},
        "complex_coarse_solve": complex_coarse,
        "notes": [f"Unknowns = tetrahedra x {median:g} for order {order} (range {low:g}-{high:g}), from "
                  "Palace runs without AMR in the gds2palace repository (within +-7 %).",
                  "Peak RAM summed over all MPI processes, calibrated on the same runs per FEM order and coarse "
                  "solve type; real runs were never above worst_case."],
    }
    if order == 3:
        estimate["notes"].append("Order 3: only 2 calibration runs; the order-2 RAM factors are used, which "
                                 "is likely too high.")
    if amr_iterations:
        max_dof = float(refinement.get("MaxSize", 0) or 0)
        estimate["amr"] = {"iterations": amr_iterations, "max_unknowns": int(max_dof)}
        if max_dof > dof:
            amr_typical, amr_worst = _ram_gb(max_dof / 1e6, factors)
            estimate["amr"]["peak_ram_gb_at_max_unknowns"] = {"typical": amr_typical, "worst_case": amr_worst}
            estimate["notes"].append("With AMR the mesh grows each iteration, up to max_unknowns (amr_max_dof): "
                                     "the RAM at that size is the upper bound.")
    return estimate


def _important_lines(lines):
    keys = ("error", "warning", "traceback", "exception", "not found", "note:")
    return [line for line in lines if any(k in line.lower() for k in keys)][:40]


# meshing jobs of this server process: job id -> {"process", "log", "log_path", "script", "started", "log_window"}
JOBS = {}
DEFAULT_WAIT_S = 300
FINISHED = "[gds2palace_mcp] model generation finished"


def generate_model(script_path, timeout_s=DEFAULT_WAIT_S, save_geometry=False, show_log=True):
    """Start the model script and wait up to timeout_s. If it is still meshing then, it keeps running and the
    result says so (job_id): the caller asks the user whether to wait longer (wait_for_model). This MCP server never
    stops a running model generation. The output goes to <script>_generate.log next to the script; show_log
    opens a window on the user's screen that shows it live."""
    script_path = os.path.abspath(script_path)
    if not os.path.isfile(script_path):
        raise FileNotFoundError(script_path)

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", MPLBACKEND="Agg")
    env.pop(SAVE_GEOMETRY_ENV, None)
    if save_geometry:
        env[SAVE_GEOMETRY_ENV] = "1"
    job_id = uuid.uuid4().hex[:8]
    log_path = os.path.splitext(script_path)[0] + "_generate.log"
    log = open(log_path, "w", encoding="utf-8", errors="replace")
    log.write(f"[gds2palace_mcp] python {os.path.basename(script_path)} (gmsh runs without windows). This window "
              "only shows the log: closing it does not stop the run.\n")
    log.flush()
    process = subprocess.Popen([sys.executable, "-u", "-m", "gds2palace_mcp._run_model", script_path],
                               cwd=os.path.dirname(script_path), env=env, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT)
    job = JOBS[job_id] = {"process": process, "log": log, "log_path": log_path, "script": script_path,
                          "started": time.monotonic(), "log_window": None}
    if show_log:
        try:
            job["log_window"] = open_log_window(log_path, f"gds2palace: {os.path.basename(script_path)}")
        except OSError as e:
            job["log_window"] = f"none ({e})"
    return wait_for_model(job_id, timeout_s)


def _read_log(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def wait_for_model(job_id, timeout_s=DEFAULT_WAIT_S):
    """Wait up to timeout_s more for a model generation: the full result when it finished, else its status."""
    job = JOBS.get(job_id)
    if job is None:
        raise ValueError(f"No running model job '{job_id}' (it finished, or the MCP server was restarted).")
    try:
        job["process"].wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - job["started"]
        tail = [l for l in ANSI_RE.sub("", _read_log(job["log_path"])).splitlines() if not l.startswith(MARKER)]
        return {"script": job["script"], "still_running": True, "job_id": job_id,
                "elapsed_min": round(elapsed / 60, 1), "log_file": job["log_path"], "log_window": job["log_window"],
                "output_tail": tail[-15:],
                "note": (f"Model generation (meshing) has run for {elapsed / 60:.0f} min and is still running. Ask the "
                         "user whether to keep waiting (wait_for_model). This MCP server does not stop it; the log window "
                         "on the user's screen shows its progress. Meshing this long usually means the layout is "
                         "too complex: check_repeated_patterns shows what simplify_layout could reduce first.")}
    JOBS.pop(job_id)
    returncode = job["process"].returncode
    job["log"].write(f"\n{FINISHED} (exit code {returncode}, {(time.monotonic() - job['started']) / 60:.1f} min)\n")
    job["log"].close()
    result = _model_result(job["script"], _read_log(job["log_path"]), returncode, False)
    result["log_file"] = job["log_path"]
    result["log_window"] = job["log_window"]
    return result


def _model_result(script_path, output, returncode, timed_out):
    lines = ANSI_RE.sub("", output).splitlines()
    models = [json.loads(line[len(MARKER):]) for line in lines if line.startswith(MARKER)]
    log = [line for line in lines if not line.startswith(MARKER)]

    result = {
        "script": script_path,
        "returncode": returncode,
        "timed_out": timed_out,
        "models": [],
        "important_lines": _important_lines(log),
        "output_tail": log[-TAIL_LINES:],
    }

    for model in models:
        sim_path = model["sim_path"]
        suffix = model.get("config_suffix") or ""
        basename = model.get("model_basename") or ""
        files = {name: os.path.isfile(os.path.join(sim_path, name))
                 for name in (f"config{suffix}.json", f"port_information{suffix}.json", "run_sim")}
        msh = os.path.join(sim_path, f"{basename}.msh")
        entry = {"sim_path": sim_path, "model_basename": basename, "files": files,
                 "gds2palace_version": model.get("gds2palace_version"),
                 "gds2palace_file": model.get("gds2palace_file")}
        if os.path.isfile(msh):
            entry["mesh_file"] = msh
            entry["mesh_size_MB"] = round(os.path.getsize(msh) / 1e6, 1)
            try:
                entry.update(_msh_counts(msh))
                config = os.path.join(sim_path, f"config{suffix}.json")
                if entry.get("tetrahedra") and os.path.isfile(config):
                    entry["estimate"] = estimate_resources(entry["tetrahedra"], config)
            except (OSError, ValueError, IndexError, KeyError):
                pass
        else:
            files[f"{basename}.msh"] = False
        geometry = os.path.join(sim_path, f"{basename}.geo_unrolled")
        if os.path.isfile(geometry):
            entry["geometry_file"] = geometry
        result["models"].append(entry)

    complete = bool(models) and all(all(m["files"].values()) for m in result["models"])
    result["ok"] = returncode == 0 and complete
    if not models and returncode == 0:
        result["note"] = "The script finished, but create_palace()/create_elmer() was not called."
    elif models and not complete:
        result["note"] = ("A model file is missing (see models[].files). run_sim is only written if the script "
                          "calls utilities.create_run_script(sim_path); Elmer models have no config.json.")
    return result
