"""transform_sparameters: mixed mode, renormalize, terminate, cascade on networks with known answers."""

import numpy as np
import pytest
import skrf as rf

from gds2palace_mcp import networks

FREQUENCY = rf.Frequency(1, 10, 10, "GHz")


def write(path, s, z0=50.0):
    rf.Network(frequency=FREQUENCY, s=s, z0=z0).write_touchstone(str(path))
    return str(path)


def two_lines(path, loss=0.5):
    """4 ports: line A from port 1 to 2 (lossless), line B from 3 to 4 (|S43| = loss)."""
    s = np.zeros((10, 4, 4), complex)
    s[:, 1, 0] = s[:, 0, 1] = 1
    s[:, 3, 2] = s[:, 2, 3] = loss
    return write(path, s)


def series_resistor(path, resistance, z0=50.0):
    s = np.zeros((10, 2, 2), complex)
    s[:, 0, 0] = s[:, 1, 1] = resistance / (resistance + 2 * z0)
    s[:, 0, 1] = s[:, 1, 0] = 2 * z0 / (resistance + 2 * z0)
    return write(path, s, z0)


def s11(path):
    return rf.Network(path).s[:, 0, 0]


def test_mixed_mode_of_an_unbalanced_line_pair(tmp_path):
    r = networks.transform_sparameters(two_lines(tmp_path / "lines.s4p"), "mixed_mode", pairs=[[1, 3], [2, 4]],
                                       frequencies_ghz=[5])
    assert r["output_file"].endswith("lines_mixedmode.s4p") and r["touchstone_version"] == "2.0"
    assert [(p["port"], p["mode"], p["logical_port"], p["input_ports"], p["z0_ohm"]) for p in r["ports"]] == [
        (1, "differential", 1, [1, 3], 100.0), (2, "differential", 2, [2, 4], 100.0),
        (3, "common", 1, [1, 3], 25.0), (4, "common", 2, [2, 4], 25.0)]
    assert r["names"]["S21"] == "Sdd21" and r["names"]["S43"] == "Scc21" and r["names"]["S41"] == "Scd21"
    out = rf.Network(r["output_file"])
    assert np.allclose(np.real(out.z0[0]), [100, 100, 25, 25])
    # line A passes 1, line B passes 0.5: Sdd21 = Scc21 = (1 + 0.5) / 2, Scd21 = Sdc21 = (1 - 0.5) / 2
    assert np.allclose(out.s[:, 1, 0], 0.75) and np.allclose(out.s[:, 3, 2], 0.75)
    assert np.allclose(out.s[:, 3, 0], 0.25) and np.allclose(out.s[:, 1, 2], 0.25)
    assert r["parameters"]["S21 (Sdd21)"]["dB_max"] == pytest.approx(20 * np.log10(0.75), abs=1e-3)
    cmrr = {(c["pair"], c["from"]): c for c in r["cmrr"]}
    assert cmrr[(2, 1)]["definition"] == "|Sdd21| / |Scd21|"
    assert cmrr[(2, 1)]["CMRR_min_dB"] == pytest.approx(20 * np.log10(3), abs=0.01)
    assert cmrr[(2, 1)]["at"][0]["GHz"] == 5.0


def test_balanced_lines_have_no_mode_conversion(tmp_path):
    r = networks.transform_sparameters(two_lines(tmp_path / "lines.s4p", loss=1), "mixed_mode", pairs=[[1, 3], [2, 4]])
    out = rf.Network(r["output_file"])
    assert np.allclose(out.s[:, 1, 0], 1) and np.allclose(out.s[:, 3, 0], 0)
    assert all(c["CMRR_min_dB"] > 200 for c in r["cmrr"])


def test_ideal_balun(tmp_path):
    """Single-ended port 1 drives ports 2 and 3 with +-1/sqrt(2): all of it arrives as differential mode."""
    s = np.zeros((10, 3, 3), complex)
    s[:, 1, 0] = s[:, 0, 1] = 1 / np.sqrt(2)
    s[:, 2, 0] = s[:, 0, 2] = -1 / np.sqrt(2)
    r = networks.transform_sparameters(write(tmp_path / "balun.s3p", s), "mixed_mode", pairs=[[2, 3]])
    assert [(p["mode"], p["logical_port"]) for p in r["ports"]] == [
        ("single_ended", 1), ("differential", 2), ("common", 2)]
    assert r["names"]["S21"] == "Sds21" and r["names"]["S31"] == "Scs21"
    out = rf.Network(r["output_file"])
    # Sds21 = (S21 - S31) / sqrt(2) = +1: the sign shows that port 2 is the positive one
    assert np.allclose(out.s[:, 1, 0], 1) and np.allclose(out.s[:, 2, 0], 0)
    swapped = networks.transform_sparameters(str(tmp_path / "balun.s3p"), "mixed_mode", pairs=[[3, 2]],
                                             output_file=str(tmp_path / "swapped"))
    assert np.allclose(rf.Network(swapped["output_file"]).s[:, 1, 0], -1)
    assert r["cmrr"][0]["definition"] == "|Sds21| / |Scs21|" and r["cmrr"][0]["CMRR_min_dB"] > 200


def test_mixed_mode_keeps_a_lossless_network_lossless(tmp_path):
    rng = np.random.default_rng(1)
    u, _ = np.linalg.qr(rng.normal(size=(4, 4)) + 1j * rng.normal(size=(4, 4)))
    s = np.repeat((u @ u.T)[None], 10, axis=0)         # symmetric and unitary: reciprocal, lossless
    r = networks.transform_sparameters(write(tmp_path / "lossless.s4p", s), "mixed_mode", pairs=[[1, 2], [3, 4]])
    out = rf.Network(r["output_file"]).s
    assert np.allclose(np.conj(out.transpose(0, 2, 1)) @ out, np.eye(4), atol=1e-9)
    assert np.allclose(out, out.transpose(0, 2, 1), atol=1e-9)
    assert r["passivity"]["max_singular_value"] == pytest.approx(1, abs=1e-5)


def test_renormalize(tmp_path):
    # a series 50 ohm resistor: S11 = R / (R + 2 z0) is 1/3 at 50 ohm and 1/2 at 25 ohm
    r = networks.transform_sparameters(series_resistor(tmp_path / "r.s2p", 50), "renormalize", z0_ohm=25)
    assert r["output_file"].endswith("r_renormalized.s2p") and r["touchstone_version"] == "1.0"
    assert np.allclose(s11(r["output_file"]), 0.5)
    per_port = networks.transform_sparameters(str(tmp_path / "r.s2p"), "renormalize", z0_ohm=[50, 25],
                                              output_file=str(tmp_path / "mixed_z0"))
    assert per_port["output_file"].endswith("mixed_z0.s2p") and per_port["touchstone_version"] == "2.0"
    assert np.allclose(np.real(rf.Network(per_port["output_file"]).z0[0]), [50, 25])


@pytest.mark.parametrize("load, expected", [(50, 1 / 3), (0, 0.0), ("short", 0.0), ("open", 1.0)])
def test_terminate_a_series_resistor(tmp_path, load, expected):
    # 50 ohm in series with the load at port 2: Z = 50 + load, S11 = (Z - 50) / (Z + 50)
    r = networks.transform_sparameters(series_resistor(tmp_path / "r.s2p", 50), "terminate",
                                       terminations={"2": load})
    assert r["output_file"].endswith("r_terminated.s1p") and r["ports"] == [{"port": 1, "input_port": 1, "z0_ohm": 50.0}]
    assert np.allclose(s11(r["output_file"]), expected)


def test_terminate_renumbers_the_remaining_ports(tmp_path):
    s = np.zeros((10, 3, 3), complex)
    s[:, 2, 0] = s[:, 0, 2] = 1                        # port 1 to port 3 through; port 2 isolated
    r = networks.transform_sparameters(write(tmp_path / "three.s3p", s), "terminate", terminations={"2": "open"})
    assert [p["input_port"] for p in r["ports"]] == [1, 3]
    assert np.allclose(rf.Network(r["output_file"]).s[:, 1, 0], 1)


def test_cascade(tmp_path):
    a = series_resistor(tmp_path / "a.s2p", 50)
    r = networks.transform_sparameters(a, "cascade", second_file=a)
    assert np.allclose(s11(r["output_file"]), 0.5)        # 100 ohm in series: 100 / (100 + 100)
    # the second file at 25 ohm reference: the physical series impedance is still 100 ohm
    b = series_resistor(tmp_path / "b.s2p", 50, z0=25)
    out = rf.Network(networks.transform_sparameters(a, "cascade", second_file=b,
                                                    output_file=str(tmp_path / "ab"))["output_file"])
    z = out.z
    assert np.allclose(z[:, 0, 0] - z[:, 0, 1] - z[:, 1, 0] + z[:, 1, 1], 100)
    assert np.allclose(np.real(out.z0[0]), [50, 25])


def test_cascade_connects_port_2_to_port_1(tmp_path):
    """An L-section (series 50 ohm at port 1, shunt 50 ohm at port 2) looks different from both sides: cascaded
    with an ideal through, it must come out unchanged, not mirrored."""
    z = np.array([[100, 50], [50, 50]], dtype=complex)          # Z matrix: series 50 ohm, then shunt 50 ohm
    s = rf.network.z2s(np.repeat(z[None], 10, axis=0), z0=50)
    l_section = write(tmp_path / "l.s2p", s)
    thru = np.zeros((10, 2, 2), complex)
    thru[:, 0, 1] = thru[:, 1, 0] = 1
    r = networks.transform_sparameters(l_section, "cascade", second_file=write(tmp_path / "thru.s2p", thru))
    out = rf.Network(r["output_file"]).s
    assert not np.allclose(s[:, 0, 0], s[:, 1, 1]) and np.allclose(out, s)


def test_terminate_uses_the_port_reference_impedance(tmp_path):
    # series 50 ohm on a 25 ohm reference, 50 ohm load at port 2: Z = 100, S11 = (100 - 25) / (100 + 25)
    r = networks.transform_sparameters(series_resistor(tmp_path / "r25.s2p", 50, z0=25), "terminate",
                                       terminations={"2": 50})
    assert np.allclose(s11(r["output_file"]), 0.6) and r["ports"][0]["z0_ohm"] == 25.0


def test_cascade_interpolates_a_finer_second_file(tmp_path):
    fine = rf.Frequency(0.5, 20, 40, "GHz")
    s = np.zeros((40, 2, 2), complex)
    s[:, 0, 1] = s[:, 1, 0] = 1
    second = str(tmp_path / "thru.s2p")
    rf.Network(frequency=fine, s=s, z0=50).write_touchstone(second)
    r = networks.transform_sparameters(series_resistor(tmp_path / "a.s2p", 50), "cascade", second_file=second)
    assert "interpolated" in r["notes"][0] and np.allclose(s11(r["output_file"]), 1 / 3)
    narrow = rf.Frequency(2, 5, 4, "GHz")
    rf.Network(frequency=narrow, s=s[:4], z0=50).write_touchstone(str(tmp_path / "narrow.s2p"))
    with pytest.raises(ValueError, match="must cover the first file's range"):
        networks.transform_sparameters(str(tmp_path / "a.s2p"), "cascade", second_file=str(tmp_path / "narrow.s2p"))


@pytest.mark.parametrize("operation, options, message", [
    ("mixed_mode", {}, "needs pairs"),
    ("mixed_mode", {"pairs": [[1, 1]]}, "used twice"),
    ("mixed_mode", {"pairs": [[1, 5]]}, "port 5 does not exist in a 4-port file"),
    ("mixed_mode", {"pairs": [[1, 2, 3]]}, "positive port, negative port"),
    ("renormalize", {}, "needs z0_ohm"),
    ("renormalize", {"z0_ohm": [50, 50]}, "one per port"),
    ("renormalize", {"z0_ohm": -5}, "positive real"),
    ("terminate", {}, "needs terminations"),
    ("terminate", {"terminations": {"1": 50, "2": 50, "3": 50, "4": 50}}, "must stay open"),
    ("terminate", {"terminations": {"2": "matched"}}, "not 'open', 'short'"),
    ("terminate", {"terminations": {"x": 50}}, "not a port number"),
    ("cascade", {"second_file": "lines.s4p"}, "connects 2-ports"),
    ("rotate", {}, "operation must be one of"),
])
def test_bad_input_is_explained(tmp_path, monkeypatch, operation, options, message):
    monkeypatch.chdir(tmp_path)
    file = two_lines(tmp_path / "lines.s4p")
    with pytest.raises(ValueError, match=message):
        networks.transform_sparameters(file, operation, **options)


def test_the_input_file_is_never_overwritten(tmp_path):
    file = series_resistor(tmp_path / "r.s2p", 50)
    with pytest.raises(ValueError, match="must not be the input file"):
        networks.transform_sparameters(file, "renormalize", z0_ohm=25, output_file=file)


def test_complex_reference_impedances_are_refused(tmp_path):
    s = np.zeros((10, 2, 2), complex)
    rf.Network(frequency=FREQUENCY, s=s, z0=50 + 5j).write_touchstone(str(tmp_path / "c.s2p"), write_z0=True)
    with pytest.raises(ValueError, match="complex reference impedances"):
        networks.transform_sparameters(str(tmp_path / "c.s2p"), "renormalize", z0_ohm=50)
