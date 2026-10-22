"""summarize_results tool: Palace run summary via gds2palace's palace_summary.py."""

import importlib.util
import os
import re
import sys
import tempfile

from .common import captured_output

MAX_RUNS = 20
TOUCHSTONE_RE = re.compile(r"\.s\d+p$", re.IGNORECASE)
NOT_CONVERGED = "did NOT converge"


def _palace_summary_module():
    """palace_summary.py as shipped with gds2palace 0.9.2 and newer (or from a clone)."""
    from gds2palace import utilities
    if not hasattr(utilities, "get_scripts_dir"):
        raise ValueError("summarize_results needs gds2palace 0.9.2 or newer (pip install --upgrade gds2palace).")
    scripts_dir = utilities.get_scripts_dir()
    if scripts_dir is None:
        raise FileNotFoundError("palace_summary.py was not found in this gds2palace installation.")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)   # palace_summary imports combine_extend_snp from its own folder
    spec = importlib.util.spec_from_file_location("palace_summary", os.path.join(scripts_dir, "palace_summary.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _search_root(path):
    path = os.path.abspath(path)
    if path.endswith(".py"):
        basename = os.path.splitext(os.path.basename(path))[0]
        return os.path.join(os.path.dirname(path), "palace_model", f"{basename}_data")
    return path if os.path.isdir(path) else os.path.dirname(path)


def _run_files(run_dir):
    """All files in a run directory, without the (large) paraview field dumps."""
    for dirpath, dirnames, filenames in os.walk(run_dir):
        dirnames[:] = [d for d in dirnames if d != "paraview"]
        for name in filenames:
            yield os.path.join(dirpath, name)


def _not_converged(files):
    """Count 'did NOT converge' lines in log files saved inside the run directory."""
    counts = {}
    for name in files:
        if not name.endswith((".log", ".out", ".txt")):
            continue
        try:
            with open(name, encoding="utf-8", errors="replace") as f:
                counts[name] = sum(NOT_CONVERGED in line for line in f)
        except OSError:
            continue
    return counts


def summarize_results(path, plot=False):
    root = _search_root(path)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"{root} does not exist: generate the model and run Palace first.")
    palace_summary = _palace_summary_module()
    runs = palace_summary._find_run_dirs(root)
    if not runs:
        raise FileNotFoundError(f"No Palace run directory (with config.json) in or below {root}.")

    results, images = [], []
    for run in runs[:MAX_RUNS]:
        basename = palace_summary._derive_model_basename(run)
        with captured_output():
            text, rows = palace_summary.build_results_summary(run, basename)
        files = list(_run_files(run))
        touchstone = sorted(f for f in files if TOUCHSTONE_RE.search(f))
        logs = _not_converged(files)
        entry = {"run": run, "model": basename, "summary": text, "touchstone_files": touchstone,
                 "amr_iterations": None if rows is None else len(rows)}
        if logs:
            entry["logs_checked"] = len(logs)
            entry["did_not_converge_lines"] = {name: n for name, n in logs.items() if n}
        if not touchstone and not text.startswith("No "):
            entry["note"] = "No Touchstone file yet: run combine_snp (or python -m gds2palace.scripts.combine_extend_snp) in the run folder."
        if plot and rows:
            with tempfile.TemporaryDirectory() as tmp:
                png = palace_summary.plot_convergence(rows, basename, os.path.join(tmp, "convergence.png"))
                with open(png, "rb") as f:
                    images.append(f.read())
        results.append(entry)

    notes = ["Max |dS| between AMR iterations is the convergence measure; Palace's error indicator is a "
             "relative residual, not an accuracy estimate.",
             "If the Palace log was not saved in the run folder, 'did NOT converge' could not be checked: "
             "ask the user to search their Palace log for it."]
    if len(runs) > MAX_RUNS:
        notes.insert(0, f"{len(runs)} runs found, only the first {MAX_RUNS} are summarized.")
    return {"search_root": root, "runs": results, "notes": notes}, images
