import pytest

import layouts
from gds2palace_mcp import layout, stackup


def test_bundled_stackups_are_listed():
    result = stackup.list_stackups()
    names = [s["name"] for s in result["stackups"]]
    assert {"SG13G2_FEM_200um.xml", "SG13G2_FEM_200um_passi3D.xml", "SG13CMOS5L_200um.xml"} <= set(names)
    assert all(s["description"] for s in result["stackups"])


def test_inspect_stackup_layers_and_variables(stackup_name):
    r = stackup.inspect_stackup(stackup_name)
    by_name = {l["name"]: l for l in r["layers_bottom_to_top"]}
    assert by_name["TopMetal2"]["gds_layer"] == 134 and by_name["TopMetal2"]["type"] == "conductor"
    assert by_name["TopVia2"]["type"] == "via"
    assert by_name["SUBGND"]["type"] == "sheet"
    assert r["variables"]["total_thickness"] == 200.0
    assert r["free_port_layers"][0] == 201
    zs = [l["zmin"] for l in r["layers_bottom_to_top"]]
    assert zs == sorted(zs)                       # bottom to top


def test_variable_override_changes_substrate(stackup_name):
    r = stackup.inspect_stackup(stackup_name, {"total_thickness": 100})
    assert r["variables"]["total_thickness"] == 100.0
    assert r["variables"]["bulk_thickness"] == pytest.approx(80.12)


def test_unknown_stackup_lists_available_files():
    with pytest.raises(FileNotFoundError) as e:
        stackup.inspect_stackup("nonexistent.xml")
    assert "SG13G2_FEM_200um.xml" in str(e.value)


def test_inspect_gds_layers_and_roles(line_gds, stackup_name):
    r = layout.inspect_gds(line_gds, stackup_file=stackup_name)
    layers = {l["layer"]: l for l in r["layers"]}
    assert set(layers) == {8, 134, 201, 202}
    assert layers[134]["shapes"] == 1 and layers[134]["smallest_shape_side"] == pytest.approx(15.0)
    assert layers[134]["stackup_role"] == ["conductor TopMetal2"]
    assert "not in stackup" in layers[201]["stackup_role"][0]
    assert r["bbox"] == pytest.approx([-80, -110, 880, 110])
    assert r["unit_m"] == pytest.approx(1e-6)
    assert 126 in r["stackup_layers_without_shapes"]


def test_inspect_gds_smallest_gap(tmp_path):
    path = layouts.coupled_lines(tmp_path / "c.gds", gap=3.0, width=8.0)
    r = layout.inspect_gds(path)
    top = next(l for l in r["layers"] if l["layer"] == 134)
    assert top["shapes"] == 2 and top["smallest_gap"] == pytest.approx(3.0)


def test_inspect_gds_unknown_cell_falls_back_to_first_top_cell(line_gds):
    r = layout.inspect_gds(line_gds, cellname="DOES_NOT_EXIST")
    assert r["cell"] == "LINE" and "not found" in r["notes"][0]
