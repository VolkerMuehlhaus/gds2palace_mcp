"""generate_model tool: run a model script and check what it wrote."""

import json
import os
import re
import subprocess
import sys

from ._run_model import MARKER

TAIL_LINES = 40
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _msh_counts(msh_path):
    """Node and element counts from a gmsh 2.2 ASCII mesh (the format gds2palace writes)."""
    counts = {}
    with open(msh_path, encoding="ascii", errors="replace") as f:
        for line in f:
            section = line.strip()
            if section in ("$Nodes", "$Elements"):
                counts[section[1:].lower()] = int(f.readline().split()[0])
                if len(counts) == 2:
                    break
    return counts


def _important_lines(lines):
    keys = ("error", "warning", "traceback", "exception", "not found", "note:")
    return [line for line in lines if any(k in line.lower() for k in keys)][:40]


def generate_model(script_path, timeout_s=1800):
    script_path = os.path.abspath(script_path)
    if not os.path.isfile(script_path):
        raise FileNotFoundError(script_path)

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", MPLBACKEND="Agg")
    try:
        proc = subprocess.run([sys.executable, "-u", "-m", "gds2palace_mcp._run_model", script_path],
                              cwd=os.path.dirname(script_path), env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding="utf-8", errors="replace", timeout=timeout_s)
        output, returncode, timed_out = proc.stdout, proc.returncode, False
    except subprocess.TimeoutExpired as e:
        output = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        returncode, timed_out = None, True

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
            except (OSError, ValueError, IndexError):
                pass
        else:
            files[f"{basename}.msh"] = False
        result["models"].append(entry)

    complete = bool(models) and all(all(m["files"].values()) for m in result["models"])
    result["ok"] = returncode == 0 and complete
    if not models and returncode == 0:
        result["note"] = "The script finished, but create_palace()/create_elmer() was not called."
    elif models and not complete:
        result["note"] = ("A model file is missing (see models[].files). run_sim is only written if the script "
                          "calls utilities.create_run_script(sim_path); Elmer models have no config.json.")
    return result
