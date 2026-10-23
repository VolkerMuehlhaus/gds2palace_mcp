import numpy as np
import pytest

import layouts
from gds2palace_mcp import sparams

R, L, C = 0.5, 1e-9, 100e-15


@pytest.fixture(scope="module")
def rl_file(tmp_path_factory):
    return layouts.series_network(tmp_path_factory.mktemp("s") / "rl.s2p", "rl", resistance=R, inductance=L)


@pytest.fixture(scope="module")
def rlc_file(tmp_path_factory):
    return layouts.series_network(tmp_path_factory.mktemp("s") / "rlc.s2p", "rlc", resistance=R, inductance=L,
                                  capacitance=C)


def s_of_series(z, z0=50.0):
    return z / (z + 2 * z0), 2 * z0 / (z + 2 * z0)


def test_summary_matches_the_analytic_values(rl_file):
    r = sparams.sparameter_summary([rl_file], ["S11", "S21"], [10.0])["files"][0]
    assert r["ports"] == 2 and r["points"] == 1001
    assert r["f_start_GHz"] == pytest.approx(0.1) and r["f_stop_GHz"] == pytest.approx(100.0)
    assert r["z0_ohm"] == [50.0]
    assert r["passivity"]["passive"] is True and r["reciprocity_max_abs_Sij_minus_Sji"] < 1e-9
    z = R + 1j * 2 * np.pi * 10e9 * L
    s11, s21 = s_of_series(z)
    at = r["parameters"]["S21"]["at"][0]
    assert at["dB"] == pytest.approx(20 * np.log10(abs(s21)), abs=0.01)
    assert at["phase_deg"] == pytest.approx(np.degrees(np.angle(s21)), abs=0.1)
    assert r["parameters"]["S11"]["at"][0]["dB"] == pytest.approx(20 * np.log10(abs(s11)), abs=0.01)


def test_a_gain_network_is_not_passive(tmp_path):
    path = layouts.gain_network(tmp_path / "gain.s2p", gain=1.1)
    r = sparams.sparameter_summary([path])["files"][0]
    assert r["passivity"]["passive"] is False
    assert r["passivity"]["max_singular_value"] == pytest.approx(1.1, abs=1e-3)
    assert r["reciprocity_max_abs_Sij_minus_Sji"] == pytest.approx(1.1)        # S21 = 1.1, S12 = 0


def test_summary_input_errors(rl_file, tmp_path):
    with pytest.raises(ValueError, match="S31 does not exist in a 2-port file"):
        sparams.sparameter_summary([rl_file], ["S31"])
    with pytest.raises(FileNotFoundError):
        sparams.sparameter_summary([str(tmp_path / "missing.s2p")])


@pytest.mark.parametrize("connection", ["series", "single_ended"])
def test_inductor_values_from_a_series_rl(rl_file, connection):
    r, images = sparams.passive_values(rl_file, "inductor", connection, [10.0])
    assert r["L_low_frequency_nH"] == pytest.approx(1.0, rel=1e-3)
    assert r["R_low_frequency_ohm"] == pytest.approx(R, rel=1e-3)
    at = r["at"][0]
    assert at["Q"] == pytest.approx(2 * np.pi * 10e9 * L / R, rel=0.01)
    assert r["SRF_GHz"] is None and images == []


def test_capacitor_and_self_resonance_from_a_series_rlc(rlc_file):
    r, _ = sparams.passive_values(rlc_file, "capacitor", "series")
    assert r["C_low_frequency_fF"] == pytest.approx(100.0, rel=1e-3)
    assert r["SRF_GHz"] == pytest.approx(1 / (2 * np.pi * np.sqrt(L * C)) / 1e9, rel=0.01)


def test_passive_values_plot_and_errors(rl_file):
    result, images = sparams.passive_values(rl_file, "inductor", "series", plot=True)
    assert len(images) == 1 and images[0][:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(ValueError, match="device"):
        sparams.passive_values(rl_file, device="resistor")
    with pytest.raises(ValueError, match="connection"):
        sparams.passive_values(rl_file, connection="parallel")


def test_plot_sparameters_returns_png_figures(rl_file, tmp_path):
    images, summary = sparams.plot_sparameters([rl_file, rl_file], ["S11", "S21"], smith=True, zoom=True,
                                               save_png=str(tmp_path / "plot.png"))
    assert len(images) == 3 and all(png[:8] == b"\x89PNG\r\n\x1a\n" for png in images)
    assert summary["figures"] == ["dB and phase", "Smith chart", "Smith chart, zoomed"]
    assert len(summary["saved_png"]) == 3 and all(p.endswith(".png") for p in summary["saved_png"])
    assert (tmp_path / "plot.png").exists() and (tmp_path / "plot_1.png").exists()
