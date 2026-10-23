"""S-parameter tools: numbers, plots (via plot_snp), plot window, L/Q/C of passives."""

import io
import os
import subprocess
import sys

import numpy as np

from .common import open_window

import matplotlib
matplotlib.use("Agg")   # images only; the window for the user runs in its own process

PASSIVE_DEFINITIONS = {
    "differential": "Z = Z11 - Z12 - Z21 + Z22 (port 1 to port 2, floating), as lumpedmodel pi_from_s2p",
    "single_ended": "Z = 1/Y11 (port 2 shorted to ground)",
    "series": "Z = -2/(Y12 + Y21) (series branch of the pi model), as lumpedmodel mim_from_s2p",
    "one_port": "Z = Z11",
}


def _read(files):
    """Networks read like plot_snp does (shortened legend names), with their full paths."""
    import plot_snp
    networks = []
    for filename in files:
        if not os.path.isfile(filename):
            raise FileNotFoundError(filename)
        network = plot_snp.read_network(os.path.abspath(filename))
        network.path = os.path.abspath(filename)
        networks.append(network)
    return networks


def _default_parameters(nports):
    if nports <= 4:
        return [(m, n) for m in range(1, nports + 1) for n in range(1, m + 1)]
    return [(m, m) for m in range(1, nports + 1)] + [(m, 1) for m in range(2, nports + 1)]


def _parse(parameters, nports):
    import plot_snp
    if not parameters:
        return _default_parameters(nports)
    parsed = [plot_snp.parse_parameter(p.upper()) for p in parameters]
    for m, n in parsed:
        if not (1 <= m <= nports and 1 <= n <= nports):
            raise ValueError(f"S{m}{n} does not exist in a {nports}-port file.")
    return parsed


def _nearest(f, f_ghz):
    i = int(np.argmin(np.abs(f - f_ghz * 1e9)))
    return i, round(float(f[i] / 1e9), 6)


def sparameter_summary(files, parameters=None, frequencies_ghz=None):
    results = []
    for network in _read(files):
        f = network.frequency.f
        s = network.s
        nports = s.shape[1]
        sigma_max = np.linalg.svd(s, compute_uv=False)[:, 0]
        worst = int(np.argmax(sigma_max))
        entry = {
            "file": network.path, "ports": nports, "points": len(f),
            "f_start_GHz": round(float(f[0] / 1e9), 6), "f_stop_GHz": round(float(f[-1] / 1e9), 6),
            "z0_ohm": sorted({round(float(z), 3) for z in np.real(network.z0).ravel()}),
            "passivity": {
                "max_singular_value": round(float(sigma_max[worst]), 5),
                "at_GHz": round(float(f[worst] / 1e9), 6),
                "passive": bool(sigma_max[worst] <= 1 + 1e-6),
            },
            "reciprocity_max_abs_Sij_minus_Sji": round(float(np.max(np.abs(s - s.transpose(0, 2, 1)))), 6),
            "parameters": {},
        }
        for m, n in _parse(parameters, nports):
            data = s[:, m - 1, n - 1]
            db = 20 * np.log10(np.maximum(np.abs(data), 1e-300))
            i_min, i_max = int(np.argmin(db)), int(np.argmax(db))
            p = {
                "dB_min": round(float(db[i_min]), 3), "dB_min_at_GHz": round(float(f[i_min] / 1e9), 6),
                "dB_max": round(float(db[i_max]), 3), "dB_max_at_GHz": round(float(f[i_max] / 1e9), 6),
            }
            for f_ghz in frequencies_ghz or []:
                i, actual = _nearest(f, f_ghz)
                p.setdefault("at", []).append({"GHz": actual, "dB": round(float(db[i]), 3),
                                               "phase_deg": round(float(np.angle(data[i], deg=True)), 2)})
            entry["parameters"][f"S{m}{n}"] = p
        results.append(entry)
    return {
        "files": results,
        "notes": ["Values 'at' a frequency are taken at the nearest frequency point in the file, not interpolated.",
                  "passive is False if the largest singular value of S exceeds 1 at any frequency (the network "
                  "generates energy there): check the simulation or measurement at that frequency."],
    }


def _figures_to_png(figures, width_px):
    import matplotlib.pyplot as plt
    images = []
    for fig in figures:
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=width_px / fig.get_size_inches()[0])
        plt.close(fig)
        images.append(buffer.getvalue())
    return images


def plot_sparameters(files, parameters=None, smith=False, zoom=False, width_px=1000, save_png=""):
    import plot_snp
    networks = _read(files)
    nports = min(n.s.shape[1] for n in networks)
    parsed = _parse(parameters or ["S11"] + (["S21"] if nports > 1 else []), nports)
    figures = plot_snp.plot_all(networks, [list(p) for p in parsed], smith=smith, zoom=zoom)
    images = _figures_to_png(figures, width_px)
    saved = []
    if save_png:
        root, ext = os.path.splitext(os.path.abspath(save_png))
        for i, png in enumerate(images):
            path = f"{root}{'' if i == 0 else '_' + str(i)}{ext or '.png'}"
            with open(path, "wb") as f:
                f.write(png)
            saved.append(path)
    summary = {"files": [n.path for n in networks], "parameters": [f"S{m}{n}" for m, n in parsed],
               "figures": ["dB and phase"] + (["Smith chart"] if smith else []) + (["Smith chart, zoomed"] if zoom else []),
               "saved_png": saved}
    return images, summary


def show_sparameters(files, parameters=None, smith=False, zoom=False):
    files = [os.path.abspath(f) for f in files]
    for filename in files:
        if not os.path.isfile(filename):
            raise FileNotFoundError(filename)
    args = files + [p.upper() for p in (parameters or [])] + (["-smith"] if smith else []) + (["-zoom"] if zoom else [])
    env = {k: v for k, v in os.environ.items() if k != "MPLBACKEND"}     # plot_snp needs an interactive backend
    process = open_window([sys.executable, "-m", "plot_snp"] + args, env=env)
    return {"opened": files, "arguments": args, "pid": process.pid,
            "note": "plot_snp opens its plot window(s) on the user's screen. This tool does not see them; "
                    "ask the user what they see."}


def _impedance(network, connection):
    nports = network.s.shape[1]
    if nports == 1:
        return network.z[:, 0, 0], "one_port"
    if nports != 2:
        raise ValueError(f"L/Q/C extraction needs a 1-port or 2-port file, this one has {nports} ports.")
    z, y = network.z, network.y
    if connection == "differential":
        return z[:, 0, 0] - z[:, 0, 1] - z[:, 1, 0] + z[:, 1, 1], connection
    if connection == "single_ended":
        return 1 / y[:, 0, 0], connection
    if connection == "series":
        return -2 / (y[:, 0, 1] + y[:, 1, 0]), connection
    raise ValueError("connection must be 'differential', 'single_ended' or 'series'.")


def _zero_crossing(f, values, rising):
    """First frequency where values change sign (rising: - to +), linearly interpolated."""
    for i in range(len(f) - 1):
        a, b = values[i], values[i + 1]
        if (a < 0 <= b) if rising else (a > 0 >= b):
            return float(f[i] + (f[i + 1] - f[i]) * (-a) / (b - a))
    return None


def passive_values(file, device="inductor", connection="differential", frequencies_ghz=None, plot=False,
                   width_px=900):
    if device not in ("inductor", "capacitor"):
        raise ValueError("device must be 'inductor' or 'capacitor'.")
    network = _read([file])[0]
    keep = network.frequency.f > 0
    f = network.frequency.f[keep]
    z, used = _impedance(network, connection)
    z = z[keep]
    omega = 2 * np.pi * f
    x, r = z.imag, z.real

    if device == "inductor":
        value, unit, scale = x / omega, "nH", 1e9
        q = x / r
        srf = _zero_crossing(f, x, rising=False)
    else:
        value, unit, scale = -1 / (omega * x), "fF", 1e15
        q = -x / r
        srf = _zero_crossing(f, x, rising=True)

    below = f < srf if srf else np.ones_like(f, dtype=bool)
    i_q = int(np.argmax(np.where(below, q, -np.inf)))
    name = "L" if device == "inductor" else "C"
    result = {
        "file": network.path, "device": device, "connection": used, "definition": PASSIVE_DEFINITIONS[used],
        f"{name}_low_frequency_{unit}": round(float(value[0] * scale), 4),
        "low_frequency_GHz": round(float(f[0] / 1e9), 6),
        "R_low_frequency_ohm": round(float(r[0]), 4),
        "Q_peak": round(float(q[i_q]), 3), "Q_peak_at_GHz": round(float(f[i_q] / 1e9), 6),
        "SRF_GHz": None if srf is None else round(srf / 1e9, 4),
        "notes": [f"{name} = " + ("Im(Z)/omega" if device == "inductor" else "-1/(omega Im(Z))")
                  + ", R = Re(Z), Q = " + ("Im(Z)/Re(Z)" if device == "inductor" else "-Im(Z)/Re(Z)") + ".",
                  "SRF is where Im(Z) changes sign; None means no resonance inside the file's frequency range.",
                  "Q_peak is searched below the SRF. Values 'at' a frequency use the nearest frequency point."],
    }
    for f_ghz in frequencies_ghz or []:
        i, actual = _nearest(f, f_ghz)
        result.setdefault("at", []).append({"GHz": actual, f"{name}_{unit}": round(float(value[i] * scale), 4),
                                            "R_ohm": round(float(r[i]), 4), "Q": round(float(q[i]), 3)})

    images = []
    if plot:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(10, 4))
        fig.suptitle(f"{network.name}: {device}, {used}")
        axes[0].plot(f[below] / 1e9, value[below] * scale, "b")
        axes[0].set_ylabel(f"{name} ({unit})")
        axes[1].plot(f[below] / 1e9, q[below], "r")
        axes[1].set_ylabel("Q")
        for ax in axes:
            ax.set_xlabel("Frequency (GHz)")
            ax.set_xmargin(0)
            ax.grid()
        fig.tight_layout()
        images = _figures_to_png([fig], width_px)
        result["plot_note"] = "Plotted up to the SRF." if srf else "Plotted over the full frequency range."
    return result, images
