"""figures_of_merit on networks with known figures: coupled inductors, ideal balun, hybrid, Wilkinson, line."""

import numpy as np
import pytest
import skrf as rf

from gds2palace_mcp import figures

FREQUENCY = rf.Frequency(1, 50, 50, "GHz")
F = FREQUENCY.f
W = 2 * np.pi * F


def write(path, s, z0=50.0, frequency=FREQUENCY):
    rf.Network(frequency=frequency, s=s, z0=z0).write_touchstone(str(path))
    return str(path)


def coupled_inductors(l1=1e-9, l2=2e-9, k=0.7, r1=1.0, r2=2.0, r12=0.5):
    """Z matrix of two coupled windings; r12 is a resistive coupling (e.g. through the substrate), which must
    not count into k."""
    m = k * np.sqrt(l1 * l2)
    z = np.zeros((len(F), 2, 2), complex)
    z[:, 0, 0], z[:, 1, 1] = r1 + 1j * W * l1, r2 + 1j * W * l2
    z[:, 0, 1] = z[:, 1, 0] = r12 + 1j * W * m
    return z


def value_at(result, name, ghz):
    """The value at the frequency point the tool used for ghz (the nearest one in the file)."""
    return min(result["values"][name]["at"], key=lambda a: abs(a["GHz"] - ghz))["value"]


def test_transformer_from_coupled_inductors(tmp_path):
    z = coupled_inductors()
    path = write(tmp_path / "xfmr.s2p", rf.network.z2s(z, z0=50))
    r, images = figures.figures_of_merit(path, "transformer", frequencies_ghz=[10], plot=True)
    assert r["L1_low_frequency_nH"] == pytest.approx(1.0, abs=1e-6) and r["L2_low_frequency_nH"] == pytest.approx(2.0, abs=1e-6)
    assert r["k_low_frequency"] == pytest.approx(0.7, abs=1e-6)
    assert value_at(r, "Q1", 10) == pytest.approx(2 * np.pi * 10e9 * 1e-9 / 1.0, rel=1e-4)
    assert value_at(r, "Q2", 10) == pytest.approx(2 * np.pi * 10e9 * 2e-9 / 2.0, rel=1e-4)
    assert r["SRF_primary_GHz"] is None and len(images) == 1
    # the optimum terminations really give the maximum available gain (transducer gain with conjugate match)
    s = rf.network.z2s(z, z0=50)[9]
    zs = value_at(r, "Zsource_opt_real_ohm", 10) + 1j * value_at(r, "Zsource_opt_imag_ohm", 10)
    zl = value_at(r, "Zload_opt_real_ohm", 10) + 1j * value_at(r, "Zload_opt_imag_ohm", 10)
    gs, gl = (zs - 50) / (zs + 50), (zl - 50) / (zl + 50)
    gin = s[0, 0] + s[0, 1] * s[1, 0] * gl / (1 - s[1, 1] * gl)
    gt = (1 - abs(gs) ** 2) * abs(s[1, 0]) ** 2 * (1 - abs(gl) ** 2) / (abs(1 - gs * gin) ** 2 * abs(1 - s[1, 1] * gl) ** 2)
    assert -10 * np.log10(gt) == pytest.approx(value_at(r, "min_insertion_loss_dB", 10), abs=1e-3)
    assert 0 < value_at(r, "min_insertion_loss_dB", 10) < 3


def test_lossless_transformer_has_no_insertion_loss(tmp_path):
    path = write(tmp_path / "ideal.s2p", rf.network.z2s(coupled_inductors(r1=0, r2=0, r12=0), z0=50))
    r, _ = figures.figures_of_merit(path, "transformer", fmin_ghz=5, fmax_ghz=20)
    assert abs(r["values"]["min_insertion_loss_dB"]["max"]) < 1e-6 and r["band_GHz"] == [5.0, 20.0]


def test_differential_transformer_from_pairs(tmp_path):
    """The differential 2-port of a 4-port built from known coupled inductors (pairs [1,2] and [3,4])."""
    sdd = rf.network.z2s(coupled_inductors(), z0=100)
    mm = np.zeros((len(F), 4, 4), complex)              # ports: d1, d2, c1, c2 (common mode ideally matched)
    mm[:, :2, :2] = sdd
    network = rf.Network(frequency=FREQUENCY, s=mm, z0=[100, 100, 25, 25])
    network.gmm2se(p=2)                                  # -> single-ended ports pair1+, pair1-, pair2+, pair2-
    path = write(tmp_path / "diff.s4p", network.s)
    r, _ = figures.figures_of_merit(path, "transformer", pairs=[[1, 2], [3, 4]])
    assert r["L1_low_frequency_nH"] == pytest.approx(1.0, abs=1e-6) and r["k_low_frequency"] == pytest.approx(0.7, abs=1e-6)
    assert not any("k is negative" in n for n in r["notes"])
    swapped, _ = figures.figures_of_merit(path, "transformer", pairs=[[3, 4], [1, 2]])
    assert swapped["L1_low_frequency_nH"] == pytest.approx(2.0, abs=1e-6)
    # secondary pair the other way round: k changes sign, and the result says why
    reversed_secondary, _ = figures.figures_of_merit(path, "transformer", pairs=[[1, 2], [4, 3]])
    assert reversed_secondary["k_low_frequency"] == pytest.approx(-0.7, abs=1e-6)
    assert any("k is negative" in n and "dot convention" in n for n in reversed_secondary["notes"])
    # the differential 2-port keeps the file's name (plot title)
    network = figures._network(path)
    assert figures._differential_two_port(network, [[1, 2], [3, 4]]).name == network.name


def test_balun_imbalance(tmp_path):
    a, phi = 0.9, 10.0                                   # port 3 has 90 % of port 2's amplitude, 10 deg off
    s = np.zeros((len(F), 3, 3), complex)
    s[:, 1, 0] = s[:, 0, 1] = 1 / np.sqrt(2)
    s[:, 2, 0] = s[:, 0, 2] = -a / np.sqrt(2) * np.exp(1j * np.radians(phi))
    r, _ = figures.figures_of_merit(write(tmp_path / "balun.s3p", s), "balun", frequencies_ghz=[10])
    assert value_at(r, "amplitude_imbalance_dB", 10) == pytest.approx(-20 * np.log10(a), abs=1e-4)
    assert value_at(r, "phase_imbalance_deg", 10) == pytest.approx(-phi, abs=1e-4)
    sds = (s[0, 1, 0] - s[0, 2, 0]) / np.sqrt(2)
    scs = (s[0, 1, 0] + s[0, 2, 0]) / np.sqrt(2)
    assert value_at(r, "differential_insertion_loss_dB", 10) == pytest.approx(-20 * np.log10(abs(sds)), abs=1e-4)
    total = abs(s[0, 1, 0]) ** 2 + abs(s[0, 2, 0]) ** 2
    assert value_at(r, "insertion_loss_dB", 10) == pytest.approx(-10 * np.log10(total), abs=1e-4)
    assert value_at(r, "CMRR_dB", 10) == pytest.approx(20 * np.log10(abs(sds) / abs(scs)), abs=1e-3)


def test_branch_line_hybrid(tmp_path):
    h = -1 / np.sqrt(2) * np.array([[0, 1j, 1, 0], [1j, 0, 0, 1], [1, 0, 0, 1j], [0, 1, 1j, 0]])
    path = write(tmp_path / "hybrid.s4p", np.repeat(h[None], len(F), axis=0))
    with pytest.raises(ValueError, match="give the ports for input, through, coupled, isolated"):
        figures.figures_of_merit(path, "coupler")
    r, _ = figures.figures_of_merit(path, "coupler", ports={"input": 1, "through": 2, "coupled": 3, "isolated": 4},
                                    frequencies_ghz=[10])
    assert value_at(r, "coupling_dB", 10) == pytest.approx(3.0103, abs=1e-4)
    assert value_at(r, "through_loss_dB", 10) == pytest.approx(3.0103, abs=1e-4)
    assert value_at(r, "phase_difference_deg", 10) == pytest.approx(-90, abs=1e-6)
    assert value_at(r, "isolation_dB", 10) > 250 and value_at(r, "amplitude_balance_dB", 10) == pytest.approx(0, abs=1e-6)


def test_wilkinson(tmp_path):
    w = -1j / np.sqrt(2) * np.array([[0, 1, 1], [1, 0, 0], [1, 0, 0]])
    r, _ = figures.figures_of_merit(write(tmp_path / "wilk.s3p", np.repeat(w[None], len(F), axis=0)),
                                    "power_divider", frequencies_ghz=[10])
    assert value_at(r, "split_out1_dB", 10) == pytest.approx(3.0103, abs=1e-4)
    assert value_at(r, "amplitude_balance_dB", 10) == pytest.approx(0, abs=1e-9)
    assert value_at(r, "phase_balance_deg", 10) == pytest.approx(0, abs=1e-9) and value_at(r, "isolation_dB", 10) > 250


def line_s(zc, eps_eff, loss_db_per_mm, length_um, frequency, z0=50.0):
    f = frequency.f
    alpha = loss_db_per_mm * 1e3 / (20 / np.log(10))
    beta = 2 * np.pi * f * np.sqrt(eps_eff) / 299792458.0
    gl = (alpha + 1j * beta) * length_um * 1e-6
    abcd = np.zeros((len(f), 2, 2), complex)
    abcd[:, 0, 0] = abcd[:, 1, 1] = np.cosh(gl)
    abcd[:, 0, 1], abcd[:, 1, 0] = zc * np.sinh(gl), np.sinh(gl) / zc
    return rf.network.a2s(abcd, z0)


def test_line_values_over_several_wavelengths(tmp_path):
    frequency = rf.Frequency(1, 200, 400, "GHz")        # 1.3 wavelengths at 200 GHz: phase unwrapping needed
    s = line_s(40.0, 4.0, 0.5, 1000, frequency)
    r, _ = figures.figures_of_merit(write(tmp_path / "line.s2p", s, frequency=frequency), "line", length_um=1000,
                                    frequencies_ghz=[100, 199])
    for ghz in (100, 199):
        assert value_at(r, "Z0_real_ohm", ghz) == pytest.approx(40, abs=1e-6)
        assert value_at(r, "eps_eff", ghz) == pytest.approx(4.0, abs=1e-6)
        assert value_at(r, "loss_dB_per_mm", ghz) == pytest.approx(0.5, abs=1e-6)
    assert not any("Warning" in n for n in r["notes"])
    with pytest.raises(ValueError, match="needs length_um"):
        figures.figures_of_merit(str(tmp_path / "line.s2p"), "line")


def test_coarse_line_file_is_warned(tmp_path):
    frequency = rf.Frequency(1, 200, 5, "GHz")
    path = write(tmp_path / "coarse.s2p", line_s(40.0, 4.0, 0.5, 1000, frequency), frequency=frequency)
    r, _ = figures.figures_of_merit(path, "line", length_um=1000)
    assert any("Warning" in n and "more points" in n for n in r["notes"])


def test_differential_line_from_pairs(tmp_path):
    frequency = rf.Frequency(1, 100, 200, "GHz")
    mm = np.zeros((200, 4, 4), complex)
    mm[:, :2, :2] = line_s(90.0, 3.5, 0.3, 500, frequency, z0=100)
    mm[:, 2:, 2:] = line_s(30.0, 4.5, 0.3, 500, frequency, z0=25)       # common mode: another line
    network = rf.Network(frequency=frequency, s=mm, z0=[100, 100, 25, 25])
    network.gmm2se(p=2)
    path = write(tmp_path / "dline.s4p", network.s, frequency=frequency)
    r, _ = figures.figures_of_merit(path, "line", pairs=[[1, 2], [3, 4]], length_um=500, frequencies_ghz=[50])
    assert value_at(r, "Z0_real_ohm", 50) == pytest.approx(90, abs=1e-5)
    assert value_at(r, "eps_eff", 50) == pytest.approx(3.5, abs=1e-6)


@pytest.mark.parametrize("device, options, message", [
    ("filter", {}, "device must be one of"),
    ("balun", {"ports": {"input": 1, "outputs": [1, 2]}}, "two roles"),
    ("balun", {"ports": {"input": 4}}, "does not exist"),
    ("balun", {"ports": {"source": 1}}, "unknown port role"),
    ("balun", {"pairs": [[2, 3]]}, "pairs are for differential transformers and lines"),
])
def test_bad_input_is_explained(tmp_path, device, options, message):
    s = np.zeros((len(F), 3, 3), complex)
    s[:, 1, 0] = s[:, 0, 1] = 0.7
    with pytest.raises(ValueError, match=message):
        figures.figures_of_merit(write(tmp_path / "three.s3p", s), device, **options)


def test_too_few_ports(tmp_path):
    with pytest.raises(ValueError, match="needs a 3-port file"):
        figures.figures_of_merit(write(tmp_path / "two.s2p", rf.network.z2s(coupled_inductors(), z0=50)), "balun")
