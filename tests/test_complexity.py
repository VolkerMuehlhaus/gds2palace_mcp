import hashlib

import gdspy
import pytest

import layouts
from gds2palace_mcp import complexity


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def flat_counts(path):
    cell = gdspy.GdsLibrary(infile=path).top_level()[0]
    counts = {}
    for (layer, datatype), polys in cell.get_polygons(by_spec=True).items():
        if datatype == 0:
            counts[int(layer)] = counts.get(int(layer), 0) + len(polys)
    return counts


def test_complexity_counts_equal_flattened_counts(via_array_gds, stackup_name):
    r = complexity.check_layout_complexity(via_array_gds, stackup_name)
    assert {l["layer"]: l["polygons"] for l in r["layers"]} == flat_counts(via_array_gds)
    assert r["polygons_read_by_gds2palace"] == 102
    top = r["layers"][0]                              # the via layer dominates, and the hint names it
    assert top["layer"] == 133 and top["share"] > 0.9
    assert any("via arrays" in h and "TopVia2" in h for h in r["hints"])
    assert "no measured polygon limit" in " ".join(r["notes"])


def test_complexity_ports_are_not_counted_as_read(line_gds, stackup_name):
    r = complexity.check_layout_complexity(line_gds, stackup_name)
    assert r["polygons_read_by_gds2palace"] == 2
    assert r["port_layer_polygons"] == {201: 1, 202: 1}
    assert r["hints"] == []                           # too few polygons for any simplification hint
    assert r["size"]["width"] == pytest.approx(960.0)


def test_simplify_merges_vias_and_keeps_one_top_cell(copy_to, via_array_gds, stackup_name):
    source = copy_to(via_array_gds, "vias.gds")
    before_hash = digest(source)
    r = complexity.simplify_layout(source, stackup_name, merge_vias=True, via_merge_size=1.0)
    assert r["output_file"].endswith("vias_simplified.gds")
    via = next(l for l in r["changed_layers"] if l["layer"] == 133)
    assert via["before"] == 100 and via["after"] == 1
    # flattening must not leave the former sub cell behind as a second top-level cell
    assert len(r["top_level_cells"]) == 1
    assert digest(source) == before_hash              # the original is never changed


def test_simplify_flat_via_array_gives_the_same_result(tmp_path, stackup_name):
    path = layouts.via_array(tmp_path / "flat.gds", hierarchical=False)
    r = complexity.simplify_layout(path, stackup_name, merge_vias=True, via_merge_size=1.0)
    assert next(l for l in r["changed_layers"] if l["layer"] == 133)["after"] == 1


def test_simplify_refuses_unsafe_requests(copy_to, via_array_gds, stackup_name):
    source = copy_to(via_array_gds, "vias.gds")
    with pytest.raises(ValueError, match="at least one operation"):
        complexity.simplify_layout(source, stackup_name)
    with pytest.raises(ValueError, match="never overwritten"):
        complexity.simplify_layout(source, stackup_name, output_file=source, merge_vias=True)
    with pytest.raises(ValueError, match="explicit max_hole_area"):
        complexity.simplify_layout(source, stackup_name, fill_cutouts=True)


def test_simplify_missing_file(stackup_name, tmp_path):
    with pytest.raises(FileNotFoundError):
        complexity.simplify_layout(str(tmp_path / "missing.gds"), stackup_name, merge_vias=True)
