"""fit_equivalent_circuit tool: lumped-element model from S-parameters with snp2le (command-line mode)."""

import os
import re
import shutil
import subprocess
import sys
import tempfile

from .common import open_window

SNP2LE_HINT = ("snp2le is not installed in this Python environment: pip install snp2le "
               "(https://github.com/iic-jku/snp2le).")
VALUE_RE = re.compile(r"^\s+(\S+)\s+=\s+(.+?)\s*$")


def _snp2le():
    """snp2le command next to this Python (venv Scripts/bin), else on PATH."""
    folder = os.path.dirname(sys.executable)
    for name in ("snp2le.exe", "snp2le"):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    found = shutil.which("snp2le")
    if found is None:
        raise FileNotFoundError(SNP2LE_HINT)
    return found


def _run(args, timeout=600):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([_snp2le(), "-b", *args], stdin=subprocess.DEVNULL, capture_output=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def list_structures():
    code, out, err = _run(["list-structures"], timeout=60)
    structures = []
    for line in out.splitlines():
        parts = line.split(None, 1)
        if parts:
            structures.append({"key": parts[0], "description": parts[1].strip() if len(parts) > 1 else ""})
    return structures


def fit_equivalent_circuit(file, structure="", universal=False, fext_ghz=None, fmin_ghz=None, fmax_ghz=None,
                           order=None, output_file="", netlist_format="ngspice", show_plot=False):
    file = os.path.abspath(file)
    if not os.path.isfile(file):
        raise FileNotFoundError(file)
    if universal == bool(structure):
        raise ValueError("Give either structure (see the structures list) or universal=True.")
    if netlist_format not in ("ngspice", "vacask", "both"):
        raise ValueError("netlist_format must be 'ngspice', 'vacask' or 'both'.")
    if structure:
        keys = [s["key"] for s in list_structures()]
        if structure not in keys:
            raise ValueError(f"Unknown structure '{structure}'. snp2le structures: {keys}")
    if not output_file:
        base = os.path.splitext(file)[0]
        # the file name is the subcircuit name: no hyphens (not valid in Ngspice/VACASK names)
        name = re.sub(r"\W", "_", f"{os.path.basename(base)}_{structure or 'universal'}")
        output_file = os.path.join(os.path.dirname(base), f"{name}.{'inc' if netlist_format == 'vacask' else 'cir'}")
    output_file = os.path.abspath(output_file)

    args = ["convert", file, "--mode", "universal" if universal else "structure", "--values", "--tolerances",
            "--no-progress", "--format", netlist_format, "-o", output_file]
    if structure:
        args += ["--structure", structure]
    if fext_ghz is not None:
        args += ["--fext", f"{fext_ghz}GHz"]
    if fmin_ghz is not None:
        args += ["--fmin", f"{fmin_ghz}GHz"]
    if fmax_ghz is not None:
        args += ["--fmax", f"{fmax_ghz}GHz"]
    if order is not None:
        args += ["--order", str(int(order))]

    code, out, err = _run(args)
    lines = [l for l in (out + err).splitlines() if l.strip()]
    values, section = {}, None
    for line in lines:
        if line.strip().endswith("element values:"):
            section = "values"
            continue
        if line.strip().endswith(":") and section == "values":
            section = None
        match = VALUE_RE.match(line)
        if section == "values" and match:
            values[match.group(1)] = match.group(2)
    status = [l.strip() for l in lines if l.lstrip().startswith(("[ OK ]", "[FAIL]", "[WARN]", "note:"))]
    netlists = [p for p in (output_file, os.path.splitext(output_file)[0] + ".inc",
                            os.path.splitext(output_file)[0] + ".cir") if os.path.isfile(p)]

    result = {"file": file, "structure": structure or "universal", "returncode": code, "status": status,
              "element_values": values, "netlist_files": sorted(set(netlists)), "snp2le_output": lines[-40:]}
    if netlists:
        with open(netlists[0], encoding="utf-8", errors="replace") as f:
            text = f.read()
        result["netlist"] = text if len(text) < 6000 else text[:6000] + "\n... (truncated)"
    if code != 0 or not netlists:
        result["note"] = "snp2le did not write a netlist; see status and snp2le_output."

    if show_plot and netlists:
        plot_args = [a for a in args if a not in ("--values", "--tolerances")]
        out_index = plot_args.index("-o")
        plot_args[out_index + 1] = os.path.join(tempfile.gettempdir(), "snp2le_plot_" + os.path.basename(output_file))
        open_window([_snp2le(), "-b", *plot_args, "--plot"])
        result["plot"] = "snp2le opens its data-vs-model plot window on the user's screen."
    return result
