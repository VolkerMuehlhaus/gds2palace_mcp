import struct

import pytest

import layouts
from gds2palace_mcp import circuit, viewers

PNG = b"\x89PNG\r\n\x1a\n"


def png_size(data):
    assert data[:8] == PNG
    return struct.unpack(">II", data[16:24])


def test_preview_has_no_empty_bands_and_labels_the_ports(line_gds):
    png, summary = viewers.preview_layout(line_gds, ports={1: 201, 2: 202}, width_px=1000)
    width, height = png_size(png)
    assert width == 1000 and height == pytest.approx(1000 * 220 / 960, abs=3)     # the layout's own aspect ratio
    assert [l["text"] for l in summary["labels"]] == ["P1", "P2"]
    assert summary["style"] == "sg13g2" and set(summary["outline_layers"]) == {201, 202}
    assert any("assume IHP SG13G2" in n for n in summary["notes"])               # no stackup: colors are assumed


def test_ihp_stackup_keeps_the_sg13g2_look(line_gds, stackup_name):
    _, summary = viewers.preview_layout(line_gds, stackup_file=stackup_name)
    assert summary["style"] == "sg13g2" and "legend" not in summary


def test_other_technology_gets_stackup_colors_and_a_legend(tmp_path, pcb_stackup):
    path = layouts.pcb_lines(tmp_path / "pcb.gds")
    png, summary = viewers.preview_layout(path, stackup_file=pcb_stackup)
    assert summary["style"] == "stackup"
    legend = {l["name"]: l for l in summary["legend"]}
    assert set(legend) == {"Top"} or set(legend) == {"Top", "Bottom"}
    assert png[:8] == PNG


def test_layers_with_the_same_material_color_stay_distinguishable(tmp_path, pcb_stackup):
    import gdspy
    cell = gdspy.Cell("TWO", exclude_from_current=True)
    cell.add(gdspy.Rectangle((0, 0), (1000, 500), layer=1))
    cell.add(gdspy.Rectangle((200, 100), (800, 300), layer=10))
    library = gdspy.GdsLibrary()
    library.add(cell)
    path = tmp_path / "two.gds"
    library.write_gds(str(path))
    _, summary = viewers.preview_layout(str(path), stackup_file=pcb_stackup)
    colors = [l["color"] for l in summary["legend"]]
    assert len(colors) == 2 and colors[0] != colors[1]          # both are "Copper" (00ff00)


def test_preview_input_errors(line_gds):
    with pytest.raises(ValueError, match="needs stackup_file"):
        viewers.preview_layout(line_gds, style="stackup")
    with pytest.raises(ValueError, match="style must be"):
        viewers.preview_layout(line_gds, style="rainbow")


def test_preview_save_png(line_gds, tmp_path):
    target = tmp_path / "preview.png"
    png, summary = viewers.preview_layout(line_gds, save_png=str(target))
    assert target.read_bytes() == png and summary["saved_png"] == str(target)


# ------------------------------------------------------------------------------ snp2le (optional)

def snp2le_installed():
    try:
        circuit._snp2le()
        return True
    except FileNotFoundError:
        return False


needs_snp2le = pytest.mark.skipif(not snp2le_installed(), reason="snp2le is not installed")


@needs_snp2le
def test_snp2le_structures_and_fit(tmp_path):
    keys = [s["key"] for s in circuit.list_structures()]
    assert "inductor-pi" in keys and "mim-cap" in keys
    path = layouts.series_network(tmp_path / "rl.s2p", "rl", resistance=0.5, inductance=1e-9)
    r = circuit.fit_equivalent_circuit(path, structure="inductor-pi", fext_ghz=10)
    assert r["returncode"] == 0 and r["netlist_files"]
    assert ".SUBCKT" in r["netlist"].upper() and "L_s" in r["element_values"]
    assert r["netlist_files"][0].endswith("rl_inductor_pi.cir")                 # no hyphen in the subcircuit name


@needs_snp2le
def test_snp2le_rejects_bad_requests(tmp_path):
    path = layouts.series_network(tmp_path / "rl.s2p", "rl")
    with pytest.raises(ValueError, match="Unknown structure"):
        circuit.fit_equivalent_circuit(path, structure="nonsense")
    with pytest.raises(ValueError, match="either structure"):
        circuit.fit_equivalent_circuit(path)


def test_missing_snp2le_gives_an_install_hint(monkeypatch, tmp_path):
    path = layouts.series_network(tmp_path / "rl.s2p", "rl")
    monkeypatch.setattr(circuit.sys, "executable", str(tmp_path / "python.exe"))    # a folder without snp2le
    monkeypatch.setattr(circuit.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="pip install snp2le"):
        circuit.fit_equivalent_circuit(path, structure="inductor-pi")
