"""transform_sparameters tool: mixed-mode conversion, renormalization, terminating ports, cascading.

Each operation writes a new Touchstone file (the input is never changed) and returns how its ports relate to the
input's ports, so that the other S-parameter tools (summary, plots, L/Q/C, equivalent circuits) work on the result.
"""

import os
import re

import numpy as np

from .sparams import _read, sparameter_summary

OPERATIONS = ("mixed_mode", "renormalize", "terminate", "cascade")
SUFFIXES = {"mixed_mode": "mixedmode", "renormalize": "renormalized", "terminate": "terminated",
            "cascade": "cascaded"}
LOADS = {"open": 1.0, "short": -1.0}       # reflection coefficient of an ideal open and short


def _network(file):
    import skrf as rf
    network = _read([file])[0]
    if np.any(np.abs(np.imag(network.z0)) > 1e-9):
        raise ValueError(f"{file} has complex reference impedances: only real ones are supported (the S-parameter "
                         "definitions differ for complex ones).")
    plain = rf.Network(frequency=network.frequency, s=network.s, z0=np.real(network.z0), name=network.name)
    plain.path = network.path
    return plain


def _output_path(file, output_file, operation, nports):
    """output_file, or <name>_<operation>.sNp next to the input; never the input itself."""
    if output_file:
        root, ext = os.path.splitext(os.path.abspath(output_file))
        if not re.fullmatch(r"\.s\d+p", ext, re.IGNORECASE):     # keep other dots in the name, e.g. line_v1.2
            root += ext
    else:
        root = os.path.splitext(os.path.abspath(file))[0] + "_" + SUFFIXES[operation]
    path = f"{root}.s{nports}p"
    if os.path.normcase(path) == os.path.normcase(os.path.abspath(file)):
        raise ValueError("output_file must not be the input file: the input is never changed.")
    return path


def _write(network, path, comments):
    """Touchstone 1.0 if all ports have the same reference impedance, else 2.0 ([Reference] per port)."""
    import skrf as rf
    z0 = np.real(network.z0)
    out = rf.Network(frequency=network.frequency, s=network.s, z0=z0)
    out.comments = "\n".join(comments)
    uniform = np.allclose(z0, z0.flat[0])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    out.write_touchstone(path, version="1.0" if uniform else "2.0")
    if not os.path.isfile(path):
        raise OSError(f"{path} was not written.")
    return "1.0" if uniform else "2.0"


def _port(nports, port, what):
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= nports:
        raise ValueError(f"{what}: port {port!r} does not exist in a {nports}-port file (ports 1-{nports}).")
    return port


def _mode_name(mode):
    return {"differential": "d", "common": "c", "single_ended": "s"}[mode]


def _mixed_mode(network, pairs):
    """Mixed-mode network for the given port pairs (positive, negative). Output ports: the differential and
    single-ended ports in the order of their logical port numbers, then the common-mode ports. A logical port is
    a pair or a single-ended port, numbered by its lowest input port."""
    nports = network.nports
    if not pairs:
        raise ValueError("mixed_mode needs pairs, e.g. [[1, 3], [2, 4]] for a differential 2-port on 4 ports "
                         "(positive port first).")
    used = []
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"Each pair is [positive port, negative port], not {pair!r}.")
        used += [_port(nports, p, "pairs") for p in pair]
    if len(set(used)) != len(used):
        raise ValueError(f"A port is used twice in pairs {pairs}.")
    singles = [p for p in range(1, nports + 1) if p not in used]
    groups = sorted([("pair", tuple(pair)) for pair in pairs] + [("single", (p,)) for p in singles],
                    key=lambda g: min(g[1]))
    logical = {g: i + 1 for i, g in enumerate(groups)}

    # scikit-rf pairs neighbouring ports (1st+2nd, 3rd+4th, ...) and returns [d..., c..., single-ended...]
    pair_groups = [g for g in groups if g[0] == "pair"]
    single_groups = [g for g in groups if g[0] == "single"]
    order = [p - 1 for _, pair in pair_groups for p in pair] + [g[1][0] - 1 for g in single_groups]
    import skrf as rf
    mm = rf.Network(frequency=network.frequency, s=network.s[:, order][:, :, order], z0=network.z0[:, order])
    mm.se2gmm(p=len(pair_groups))
    k = len(pair_groups)
    skrf_ports = ([("differential", g) for g in pair_groups] + [("common", g) for g in pair_groups]
                  + [("single_ended", g) for g in single_groups])

    wanted = [("differential" if g[0] == "pair" else "single_ended", g) for g in groups] + \
             [("common", g) for g in pair_groups]
    index = [skrf_ports.index(port) for port in wanted]
    result = rf.Network(frequency=network.frequency, s=mm.s[:, index][:, :, index], z0=np.real(mm.z0[:, index]))
    port_map = [{"port": i + 1, "mode": mode, "logical_port": logical[g],
                 "input_ports": list(g[1]), "z0_ohm": round(float(np.real(result.z0[0, i])), 6)}
                for i, (mode, g) in enumerate(wanted)]
    return result, port_map, k


def _mixed_mode_names(port_map):
    """Name of each S-parameter of the output file in mixed-mode notation, e.g. S21 -> Sdd21."""
    names = {}
    for a in port_map:
        for b in port_map:
            names[f"S{a['port']}{b['port']}"] = (f"S{_mode_name(a['mode'])}{_mode_name(b['mode'])}"
                                                f"{a['logical_port']}{b['logical_port']}")
    return names


def _db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-15))


def _cmrr(network, port_map, frequencies_ghz):
    """Common-mode rejection at each pair: for a signal from another logical port (differential or single-ended
    mode), how much more arrives at the pair as differential than as common mode, in dB."""
    f = network.frequency.f
    rows = []
    by_logical = {}
    for p in port_map:
        by_logical.setdefault(p["logical_port"], {})[p["mode"]] = p["port"] - 1
    for receiver, modes in by_logical.items():
        if "differential" not in modes:
            continue
        for source, source_modes in by_logical.items():
            if source == receiver:
                continue
            j = source_modes.get("differential", source_modes.get("single_ended"))
            source_mode = "d" if "differential" in source_modes else "s"
            cmrr = _db(network.s[:, modes["differential"], j]) - _db(network.s[:, modes["common"], j])
            worst = int(np.argmin(cmrr))
            row = {"pair": receiver, "from": source,
                   "definition": f"|Sd{source_mode}{receiver}{source}| / |Sc{source_mode}{receiver}{source}|",
                   "CMRR_min_dB": round(float(cmrr[worst]), 2), "CMRR_min_at_GHz": round(float(f[worst] / 1e9), 6)}
            for f_ghz in frequencies_ghz or []:
                i = int(np.argmin(np.abs(f - f_ghz * 1e9)))
                row.setdefault("at", []).append({"GHz": round(float(f[i] / 1e9), 6),
                                                 "CMRR_dB": round(float(cmrr[i]), 2)})
            rows.append(row)
    return rows


def _renormalize(network, z0_ohm):
    values = z0_ohm if isinstance(z0_ohm, (list, tuple)) else [z0_ohm] * network.nports
    if len(values) != network.nports:
        raise ValueError(f"z0_ohm needs one value, or one per port ({network.nports}), not {len(values)}.")
    for z in values:
        if isinstance(z, bool) or not isinstance(z, (int, float)) or not z > 0:
            raise ValueError(f"z0_ohm must be positive real values in ohm, not {z!r}.")
    result = network.copy()
    result.renormalize(np.array(values, dtype=float))
    return result, [{"port": i + 1, "input_port": i + 1, "z0_ohm": float(z)} for i, z in enumerate(values)]


def _load(network, port, value):
    """1-port load at the port's reference impedance: ohm (real), or 'open' / 'short'."""
    import skrf as rf
    z0 = np.real(network.z0[:, port - 1])
    if isinstance(value, str):
        if value.lower() not in LOADS:
            raise ValueError(f"Termination of port {port}: {value!r} is not 'open', 'short' or a value in ohm.")
        gamma = np.full(len(z0), LOADS[value.lower()], dtype=complex)
    elif isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        gamma = (value - z0) / (value + z0)
    else:
        raise ValueError(f"Termination of port {port}: {value!r} is not 'open', 'short' or a value in ohm (>= 0).")
    return rf.Network(frequency=network.frequency, s=gamma.reshape(-1, 1, 1), z0=z0.reshape(-1, 1))


def _terminate(network, terminations):
    import skrf as rf
    if not terminations:
        raise ValueError("terminate needs terminations, e.g. {\"3\": 50} or {\"2\": \"short\"}.")
    ports = {}
    for key, value in terminations.items():
        try:
            port = int(key)
        except (TypeError, ValueError):
            raise ValueError(f"Termination key {key!r} is not a port number.") from None
        ports[_port(network.nports, port, "terminations")] = value
    if len(ports) >= network.nports:
        raise ValueError("At least one port must stay open to the outside (not terminated).")
    result = network
    for port in sorted(ports, reverse=True):         # from the last port, so that lower port numbers stay valid
        result = rf.network.connect(result, port - 1, _load(result, port, ports[port]), 0)
    kept = [p for p in range(1, network.nports + 1) if p not in ports]
    port_map = [{"port": i + 1, "input_port": p, "z0_ohm": round(float(np.real(result.z0[0, i])), 6)}
                for i, p in enumerate(kept)]
    return result, port_map, {str(p): ports[p] for p in sorted(ports)}


def _cascade(network, second_file):
    import skrf as rf
    if not second_file:
        raise ValueError("cascade needs second_file: the 2-port connected to port 2 of the first file.")
    second = _network(second_file)
    second_path = second.path
    for n in (network, second):
        if n.nports != 2:
            raise ValueError(f"cascade connects 2-ports (port 2 of the first to port 1 of the second); "
                             f"{n.path} has {n.nports} ports. Use terminate first to reduce a larger network.")
    f1, f2 = network.frequency.f, second.frequency.f
    notes = []
    if len(f1) != len(f2) or not np.allclose(f1, f2, rtol=1e-9, atol=0):
        if f1[0] < f2[0] * (1 - 1e-9) or f1[-1] > f2[-1] * (1 + 1e-9):
            raise ValueError(f"The second file covers {f2[0] / 1e9:g}-{f2[-1] / 1e9:g} GHz, the first "
                             f"{f1[0] / 1e9:g}-{f1[-1] / 1e9:g} GHz: it must cover the first file's range.")
        second = second.interpolate(network.frequency)
        notes.append("The second file was interpolated (linear, real/imaginary) to the first file's frequency points.")
    result = rf.network.connect(network, 1, second, 0)
    port_map = [{"port": 1, "from": "port 1 of the first file", "z0_ohm": round(float(np.real(result.z0[0, 0])), 6)},
                {"port": 2, "from": "port 2 of the second file", "z0_ohm": round(float(np.real(result.z0[0, 1])), 6)}]
    return result, port_map, second_path, notes


def transform_sparameters(file, operation, output_file="", pairs=None, z0_ohm=None, terminations=None,
                          second_file="", frequencies_ghz=None):
    if operation not in OPERATIONS:
        raise ValueError(f"operation must be one of {list(OPERATIONS)}.")
    network = _network(file)
    notes = []
    result = {"input_file": network.path, "operation": operation}

    if operation == "mixed_mode":
        out, port_map, npairs = _mixed_mode(network, pairs)
        names = _mixed_mode_names(port_map)
        result["pairs"] = [list(p) for p in pairs]
        result["names"] = names
        comments = [f"Mixed-mode S-parameters of {os.path.basename(network.path)}, pairs (positive, negative) "
                    f"{result['pairs']}; made by gds2palace_mcp transform_sparameters."]
        notes += ["Reference impedances: differential 2 x z0, common mode z0 / 2 of the pair's ports (100 and 25 "
                  "ohm for 50 ohm ports); single-ended ports keep theirs. Use operation renormalize on the output "
                  "for other values.",
                  "names gives each S-parameter of the output file in mixed-mode notation (S21 -> Sdd21): use the "
                  "file's names (S21) with sparameter_summary, plot_sparameters, passive_values.",
                  "Logical ports: each pair and each single-ended port, numbered by its lowest input port."]
    elif operation == "renormalize":
        if z0_ohm is None:
            raise ValueError("renormalize needs z0_ohm: one value for all ports, or one per port.")
        out, port_map = _renormalize(network, z0_ohm)
        comments = [f"{os.path.basename(network.path)} renormalized to z0 = {z0_ohm} ohm; made by gds2palace_mcp "
                    "transform_sparameters."]
    elif operation == "terminate":
        out, port_map, loads = _terminate(network, terminations)
        result["terminations"] = loads
        comments = [f"{os.path.basename(network.path)} with ports terminated {loads}; made by gds2palace_mcp "
                    "transform_sparameters."]
        notes.append("Remaining ports are renumbered in their input order, see ports.")
    else:
        out, port_map, second_path, cascade_notes = _cascade(network, second_file)
        result["second_file"] = second_path
        comments = [f"{os.path.basename(network.path)} (port 2) cascaded with {os.path.basename(second_path)} "
                    "(port 1); made by gds2palace_mcp transform_sparameters."]
        notes += cascade_notes

    path = _output_path(file, output_file, operation, out.nports)
    result["touchstone_version"] = _write(out, path, comments)
    result["output_file"] = path
    result["ports"] = port_map

    if operation == "mixed_mode":
        transmission = [f"S{a['port']}{b['port']}" for a in port_map for b in port_map
                        if a["logical_port"] != b["logical_port"] and a["mode"] != "common"
                        and b["mode"] != "common"]
        reflection = [f"S{a['port']}{a['port']}" for a in port_map if a["mode"] != "common"]
        conversion = [f"S{c['port']}{b['port']}" for c in port_map if c["mode"] == "common" for b in port_map
                      if b["mode"] != "common"]
        chosen = list(dict.fromkeys(reflection + transmission + conversion))[:24]
        summary = sparameter_summary([path], chosen, frequencies_ghz)["files"][0]
        result["passivity"] = summary["passivity"]
        result["parameters"] = {f"{p} ({names[p]})": v for p, v in summary["parameters"].items()}
        result["cmrr"] = _cmrr(out, port_map, frequencies_ghz)
    else:
        result["passivity"] = sparameter_summary([path], ["S11"])["files"][0]["passivity"]
    if result["touchstone_version"] == "2.0":
        notes.append("Written as Touchstone 2.0, which stores a reference impedance per port ([Reference]). The "
                     "tools here read it; check that the circuit simulator it goes to reads Touchstone 2.0.")
    result["notes"] = notes
    return result
