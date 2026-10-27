"""figures_of_merit tool: transformer, balun, coupler, power divider and transmission line figures of merit from
S-parameters. Differential transformers and lines (4-port files) are evaluated on their differential 2-port."""

import numpy as np

from .networks import _mixed_mode, _network
from .sparams import _figures_to_png, _zero_crossing

C0 = 299792458.0
DEVICES = ("transformer", "balun", "coupler", "power_divider", "line")
# port roles per device and their defaults (None: the user must say which port is which)
ROLES = {
    "transformer": {"primary": 1, "secondary": 2},
    "balun": {"input": 1, "outputs": [2, 3]},
    "coupler": {"input": None, "through": None, "coupled": None, "isolated": None},
    "power_divider": {"input": 1, "outputs": [2, 3]},
    "line": {"input": 1, "output": 2},
}
NPORTS = {"transformer": 2, "balun": 3, "coupler": 4, "power_divider": 3, "line": 2}


def _db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-15))


def _wrap_deg(x):
    """Angle in degrees, wrapped to (-180, 180]."""
    return -((-np.asarray(x) + 180) % 360 - 180)


def _roles(device, ports, nports):
    roles = dict(ROLES[device])
    for key, value in (ports or {}).items():
        if key not in roles:
            raise ValueError(f"{device}: unknown port role '{key}' (roles: {', '.join(roles)}).")
        roles[key] = value
    missing = [k for k, v in roles.items() if v is None]
    if missing:
        raise ValueError(f"{device}: give the ports for {', '.join(missing)}, e.g. ports={{"
                         + ", ".join(f'"{k}": {i + 1}' for i, k in enumerate(roles)) + "}. Ask the user which port "
                         "is which if the layout doesn't show it.")
    flat = [p for v in roles.values() for p in (v if isinstance(v, list) else [v])]
    for p in flat:
        if not isinstance(p, int) or isinstance(p, bool) or not 1 <= p <= nports:
            raise ValueError(f"{device}: port {p!r} does not exist in a {nports}-port file.")
    if len(set(flat)) != len(flat):
        raise ValueError(f"{device}: a port has two roles in {roles}.")
    return roles


def _differential_two_port(network, pairs):
    """The differential 2-port (Sdd, 2 x z0 reference) of a 4-port with two pairs."""
    import skrf as rf
    if not pairs or len(pairs) != 2:
        raise ValueError("pairs needs two pairs here, e.g. [[1, 3], [2, 4]]: primary (or line input) first, "
                         "positive port first in each pair.")
    mm, port_map, _ = _mixed_mode(network, pairs)
    if any(p["mode"] == "single_ended" for p in port_map):
        raise ValueError("With pairs, all ports must be in a pair (4-port file, two pairs).")
    first = min(pairs[0])
    order = sorted((p for p in port_map if p["mode"] == "differential"),
                   key=lambda p: 0 if min(p["input_ports"]) == first else 1)
    index = [p["port"] - 1 for p in order]
    two_port = rf.Network(frequency=mm.frequency, s=mm.s[:, index][:, :, index], z0=np.real(mm.z0[:, index]))
    two_port.path = network.path
    return two_port


def _band(f, fmin_ghz, fmax_ghz):
    lo = f[0] if fmin_ghz is None else fmin_ghz * 1e9
    hi = f[-1] if fmax_ghz is None else fmax_ghz * 1e9
    band = (f >= lo * (1 - 1e-12)) & (f <= hi * (1 + 1e-12))
    if not band.any():
        raise ValueError(f"No frequency point between {lo / 1e9:g} and {hi / 1e9:g} GHz in the file.")
    return band


def _summarize(f, curves, band, frequencies_ghz, worst):
    """Per curve: the value at each frequency, and min/max (with frequency) inside the band. worst says which
    end is the worst case ('min' or 'max'), so that the result can name it."""
    result = {}
    for name, values in curves.items():
        values = np.asarray(values, dtype=float)
        valid = band & np.isfinite(values)
        entry = {}
        if valid.any():
            idx = np.where(valid)[0]
            i_min, i_max = idx[np.argmin(values[idx])], idx[np.argmax(values[idx])]
            entry = {"min": round(float(values[i_min]), 4), "min_at_GHz": round(float(f[i_min] / 1e9), 6),
                     "max": round(float(values[i_max]), 4), "max_at_GHz": round(float(f[i_max] / 1e9), 6)}
            if name in worst:
                entry["worst"] = entry[worst[name]]
        for f_ghz in frequencies_ghz or []:
            i = int(np.argmin(np.abs(f - f_ghz * 1e9)))
            value = values[i]
            entry.setdefault("at", []).append({"GHz": round(float(f[i] / 1e9), 6),
                                               "value": None if not np.isfinite(value) else round(float(value), 4)})
        result[name] = entry
    return result


def _conjugate_match(s, z0):
    """Source and load impedance for a simultaneous conjugate match (where the network is unconditionally
    stable), Gonzalez eq. 3.6.11-3.6.12."""
    s11, s12, s21, s22 = s[:, 0, 0], s[:, 0, 1], s[:, 1, 0], s[:, 1, 1]
    delta = s11 * s22 - s12 * s21
    def gamma(b, c):
        root = np.sqrt(np.maximum(b ** 2 - 4 * np.abs(c) ** 2, 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            g = (b - np.sign(b) * root) / (2 * c)
        return np.where(np.abs(c) < 1e-12, 0, g)
    gs = gamma(1 + np.abs(s11) ** 2 - np.abs(s22) ** 2 - np.abs(delta) ** 2, s11 - delta * np.conj(s22))
    gl = gamma(1 + np.abs(s22) ** 2 - np.abs(s11) ** 2 - np.abs(delta) ** 2, s22 - delta * np.conj(s11))
    return z0[:, 0] * (1 + gs) / (1 - gs), z0[:, 1] * (1 + gl) / (1 - gl)


def _transformer(network, roles):
    p, s = roles["primary"] - 1, roles["secondary"] - 1
    f = network.frequency.f
    omega = 2 * np.pi * f
    z = network.z
    z11, z22, z21 = z[:, p, p], z[:, s, s], (z[:, s, p] + z[:, p, s]) / 2
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.imag(z21) / np.sqrt(np.imag(z11) * np.imag(z22))
    two = network.s[:, [p, s]][:, :, [p, s]]
    z0 = np.real(network.z0[:, [p, s]])
    import skrf as rf
    ordered = rf.Network(frequency=network.frequency, s=two, z0=z0)
    gmax = ordered.max_gain
    stability = ordered.stability
    zs, zl = _conjugate_match(two, z0)
    unstable = stability <= 1
    zs, zl = np.where(unstable, np.nan, zs), np.where(unstable, np.nan, zl)
    with np.errstate(divide="ignore", invalid="ignore"):          # lossless windings: Q is infinite (None)
        q1, q2 = np.imag(z11) / np.real(z11), np.imag(z22) / np.real(z22)
    curves = {"L1_nH": np.imag(z11) / omega * 1e9, "L2_nH": np.imag(z22) / omega * 1e9, "k": k, "Q1": q1, "Q2": q2,
              "Gmax_dB": 10 * np.log10(gmax), "min_insertion_loss_dB": -10 * np.log10(gmax),
              "Zsource_opt_real_ohm": np.real(zs), "Zsource_opt_imag_ohm": np.imag(zs),
              "Zload_opt_real_ohm": np.real(zl), "Zload_opt_imag_ohm": np.imag(zl)}
    extra = {"L1_low_frequency_nH": round(float(curves["L1_nH"][0]), 4),
             "L2_low_frequency_nH": round(float(curves["L2_nH"][0]), 4),
             "k_low_frequency": round(float(k[0]), 4), "low_frequency_GHz": round(float(f[0] / 1e9), 6),
             "SRF_primary_GHz": _srf(f, np.imag(z11)), "SRF_secondary_GHz": _srf(f, np.imag(z22))}
    worst = {"min_insertion_loss_dB": "max", "Q1": "min", "Q2": "min"}
    notes = ["L = Im(Z)/omega and Q = Im(Z)/Re(Z) of each winding with the other open (Z11, Z22); "
             "k = Im(Z21) / sqrt(Im(Z11) Im(Z22)).",
             "Gmax is the maximum available gain (both sides conjugately matched), min_insertion_loss_dB = -Gmax: "
             "the loss of the transformer itself, without mismatch. Zsource_opt/Zload_opt give that match "
             "(on primary and secondary).",
             "L1, L2 and k lose their meaning near and above the self-resonance (SRF)."]
    return curves, extra, worst, notes, {"plot": [("L1_nH", "L2_nH"), ("k",), ("Q1", "Q2"),
                                                  ("min_insertion_loss_dB",)]}


def _srf(f, reactance):
    srf = _zero_crossing(f, reactance, rising=False)
    return None if srf is None else round(srf / 1e9, 4)


def _balun(network, roles):
    i, (a, b) = roles["input"] - 1, [o - 1 for o in roles["outputs"]]
    s = network.s
    sa, sb = s[:, a, i], s[:, b, i]
    order = [roles["input"]] + roles["outputs"]
    mm, port_map, _ = _mixed_mode(network, [roles["outputs"]])
    j = next(p["port"] - 1 for p in port_map if p["mode"] == "single_ended" and p["input_ports"] == [roles["input"]])
    d, c = (next(p["port"] - 1 for p in port_map if p["mode"] == mode) for mode in ("differential", "common"))
    curves = {"insertion_loss_dB": -10 * np.log10(np.abs(sa) ** 2 + np.abs(sb) ** 2),
              "differential_insertion_loss_dB": -_db(mm.s[:, d, j]),
              "amplitude_imbalance_dB": _db(sa) - _db(sb),
              "phase_imbalance_deg": _wrap_deg(np.angle(sa / sb, deg=True) - 180),
              "CMRR_dB": _db(mm.s[:, d, j]) - _db(mm.s[:, c, j]),
              "input_return_loss_dB": -_db(s[:, i, i]),
              "output_return_loss_differential_dB": -_db(mm.s[:, d, d])}
    worst = {"insertion_loss_dB": "max", "differential_insertion_loss_dB": "max", "CMRR_dB": "min",
             "input_return_loss_dB": "min", "output_return_loss_differential_dB": "min"}
    notes = [f"Input port {order[0]}, outputs {order[1]} (positive) and {order[2]}.",
             "amplitude_imbalance = |S_out1,in| / |S_out2,in| in dB, phase_imbalance = angle(S_out1,in / "
             "S_out2,in) - 180 deg (ideal: 0 and 0).",
             "insertion_loss = -10 log(|S_out1,in|^2 + |S_out2,in|^2): the power that reaches neither output. "
             "differential_insertion_loss = -|Sds21| in dB (differential output referenced to 2 x z0, 100 ohm for "
             "50 ohm ports): it also counts the common-mode part as lost. CMRR = |Sds21| / |Scs21|; "
             "output_return_loss_differential = -|Sdd22| in dB."]
    return curves, {}, worst, notes, {"plot": [("insertion_loss_dB", "differential_insertion_loss_dB"),
                                               ("amplitude_imbalance_dB",),
                                               ("phase_imbalance_deg",), ("CMRR_dB",)]}


def _coupler(network, roles):
    s = network.s
    i, t, c, iso = (roles[k] - 1 for k in ("input", "through", "coupled", "isolated"))
    coupling, isolation = -_db(s[:, c, i]), -_db(s[:, iso, i])
    curves = {"coupling_dB": coupling, "isolation_dB": isolation, "directivity_dB": isolation - coupling,
              "through_loss_dB": -_db(s[:, t, i]),
              "amplitude_balance_dB": _db(s[:, t, i]) - _db(s[:, c, i]),
              "phase_difference_deg": _wrap_deg(np.angle(s[:, c, i] / s[:, t, i], deg=True)),
              "input_return_loss_dB": -_db(s[:, i, i])}
    worst = {"isolation_dB": "min", "directivity_dB": "min", "input_return_loss_dB": "min"}
    notes = ["coupling = -|S_coupled,input|, isolation = -|S_isolated,input|, directivity = isolation - coupling, "
             "through_loss = -|S_through,input| (all dB). amplitude_balance = |S_through| / |S_coupled| in dB; "
             "phase_difference = angle(S_coupled / S_through): -90 deg for an ideal branch-line hybrid, 0 or 180 "
             "for a rat-race."]
    return curves, {}, worst, notes, {"plot": [("coupling_dB", "through_loss_dB"), ("isolation_dB", "directivity_dB"),
                                               ("phase_difference_deg",), ("amplitude_balance_dB",)]}


def _power_divider(network, roles):
    s = network.s
    i, (a, b) = roles["input"] - 1, [o - 1 for o in roles["outputs"]]
    curves = {"split_out1_dB": -_db(s[:, a, i]), "split_out2_dB": -_db(s[:, b, i]),
              "amplitude_balance_dB": _db(s[:, a, i]) - _db(s[:, b, i]),
              "phase_balance_deg": _wrap_deg(np.angle(s[:, a, i] / s[:, b, i], deg=True)),
              "isolation_dB": -_db(s[:, b, a]),
              "input_return_loss_dB": -_db(s[:, i, i]),
              "output1_return_loss_dB": -_db(s[:, a, a]), "output2_return_loss_dB": -_db(s[:, b, b])}
    worst = {"isolation_dB": "min", "input_return_loss_dB": "min", "output1_return_loss_dB": "min",
             "output2_return_loss_dB": "min"}
    notes = ["split = -|S_out,in| in dB (3.01 dB each for an ideal lossless equal split); the excess over 3.01 dB "
             "is the loss. isolation = -|S_out2,out1|; balances: out1 / out2 (ideal 0 dB, 0 deg)."]
    return curves, {}, worst, notes, {"plot": [("split_out1_dB", "split_out2_dB"), ("isolation_dB",),
                                               ("amplitude_balance_dB",), ("phase_balance_deg",)]}


def _line(network, roles, length_um):
    if not length_um or length_um <= 0:
        raise ValueError("line needs length_um: the length of the line between the ports, in um.")
    i, o = roles["input"] - 1, roles["output"] - 1
    import skrf as rf
    two = rf.Network(frequency=network.frequency, s=network.s[:, [i, o]][:, :, [i, o]],
                     z0=np.real(network.z0[:, [i, o]]))
    abcd = two.a
    A, B, C, D = abcd[:, 0, 0], abcd[:, 0, 1], abcd[:, 1, 0], abcd[:, 1, 1]
    half = (A + D) / 2
    root = np.sqrt(half ** 2 - 1)
    wave = half + root                                  # exp(+gamma l) or exp(-gamma l): pick |.| >= 1
    wave = np.where(np.abs(wave) >= np.abs(half - root), wave, half - root)
    alpha_l = np.log(np.abs(wave))
    beta_l = np.unwrap(np.angle(wave))                  # starts near 0 at low frequency
    if beta_l[0] < -np.pi / 2:
        beta_l += 2 * np.pi
    beta_l = np.abs(beta_l)
    length = length_um * 1e-6
    f = network.frequency.f
    omega = 2 * np.pi * f
    zc = np.sqrt(B / C)                                 # principal root: Re(Z0) >= 0
    curves = {"Z0_real_ohm": np.real(zc), "Z0_imag_ohm": np.imag(zc),
              "eps_eff": (beta_l / length * C0 / omega) ** 2,
              "loss_dB_per_mm": 20 / np.log(10) * alpha_l / (length * 1e3),
              "phase_deg": np.degrees(beta_l)}
    notes = [f"From the ABCD matrix of the 2-port with length {length_um:g} um: Z0 = sqrt(B/C), gamma l = "
             "acosh((A+D)/2), eps_eff = (beta c / omega)^2, loss = 8.686 alpha.",
             "The ports and their feed (pads, vias, port de-embedding) are counted as part of the line. For "
             "values per length without them, compare two lines of different length.",
             "The phase is unwrapped from the lowest frequency: the file needs enough points that the phase "
             "changes less than 180 deg between two of them, starting below half a wavelength."]
    step = np.max(np.abs(np.diff(np.degrees(np.unwrap(np.angle(wave)))))) if len(f) > 1 else 0
    if step > 90:
        notes.append(f"Warning: the phase changes by up to {step:.0f} deg between frequency points: eps_eff may "
                     "be off by a whole wavelength. Use a file with more points.")
    return curves, {}, {"loss_dB_per_mm": "max"}, notes, {"plot": [("Z0_real_ohm", "Z0_imag_ohm"), ("eps_eff",),
                                                                   ("loss_dB_per_mm",), ("phase_deg",)]}


def figures_of_merit(file, device, ports=None, pairs=None, length_um=None, frequencies_ghz=None, fmin_ghz=None,
                     fmax_ghz=None, plot=False, width_px=1000):
    if device not in DEVICES:
        raise ValueError(f"device must be one of {list(DEVICES)}.")
    network = _network(file)
    differential = False
    if pairs:
        if device not in ("transformer", "line"):
            raise ValueError(f"pairs are for differential transformers and lines; a {device} takes ports.")
        network = _differential_two_port(network, pairs)
        differential = True
    nports = network.nports
    if nports < NPORTS[device]:
        raise ValueError(f"A {device} needs a {NPORTS[device]}-port file (or pairs for a differential 4-port); "
                         f"{network.path} has {nports} ports.")
    roles = _roles(device, None if differential else ports, nports)
    if device == "transformer":
        curves, extra, worst, notes, layout = _transformer(network, roles)
    elif device == "balun":
        curves, extra, worst, notes, layout = _balun(network, roles)
    elif device == "coupler":
        curves, extra, worst, notes, layout = _coupler(network, roles)
    elif device == "power_divider":
        curves, extra, worst, notes, layout = _power_divider(network, roles)
    else:
        curves, extra, worst, notes, layout = _line(network, roles, length_um)

    f = network.frequency.f
    band = _band(f, fmin_ghz, fmax_ghz)
    result = {"file": network.path, "device": device,
              "ports": {"pairs": pairs} if differential else roles,
              "band_GHz": [round(float(f[band][0] / 1e9), 6), round(float(f[band][-1] / 1e9), 6)]}
    result.update(extra)
    result["values"] = _summarize(f, curves, band, frequencies_ghz, worst)
    if differential:
        notes.append("Differential: evaluated on the differential 2-port (Sdd, reference 2 x z0) of the pairs, "
                     "first pair as primary (input).")
    if nports > NPORTS[device] and not differential:
        notes.append(f"The file has {nports} ports; the ports without a role are left as they are (terminated in "
                     "their reference impedance).")
    result["notes"] = notes + ["min/max are inside band_GHz (fmin_ghz/fmax_ghz); 'worst' is the worse end where "
                               "one is worse. Values 'at' a frequency use the nearest frequency point."]

    images = []
    if plot:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(2, 2, figsize=(11, 7))
        fig.suptitle(f"{network.name}: {device}")
        for ax, names in zip(axes.ravel(), layout["plot"]):
            for name in names:
                ax.plot(f[band] / 1e9, np.asarray(curves[name], dtype=float)[band], label=name)
            ax.set_xlabel("Frequency (GHz)")
            ax.set_xmargin(0)
            ax.grid()
            ax.legend()
        fig.tight_layout()
        images = _figures_to_png([fig], width_px)
    return result, images
