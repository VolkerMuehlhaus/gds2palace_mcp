import pytest

import layouts
from gds2palace_mcp import meshsize


@pytest.mark.parametrize("value, expected", [(2.998, 2.5), (2.0, 2.0), (1.2, 1.0), (0.7, 0.5), (0.2, 0.2), (0.16, 0.1)])
def test_round_down_cellsize_never_exceeds_the_feature(value, expected):
    assert meshsize.round_down_cellsize(value) == expected
    assert expected <= value


@pytest.mark.parametrize("value, expected", [(500, 500), (300, 200), (2.998, 2), (0.16, 0.1), (7, 5), (1500, 1000)])
def test_round_down_125(value, expected):
    assert meshsize.round_down_125(value) == expected
    assert expected <= value


@pytest.mark.parametrize("gap, width, accurate, quick", [
    (2.0, 8.0, 2.0, 2.0),      # gap equal to the accurate default: both 2
    (3.0, 8.0, 2.0, 3.0),      # quick may be as large as the gap, not larger
    (2.998, 8.0, 2.0, 2.5),    # just below 3 um: rounded down to 2.5 (the L2n0 example inductor)
    (1.2, 8.0, 1.0, 1.0),
    (10.0, 8.0, 2.0, 5.0),     # gap and width above the defaults: defaults stay
    (40.0, 8.0, 2.0, 5.0),     # gap > 2x width: not coupled
])
def test_coupled_lines_set_the_cell_size(tmp_path, stackup_name, gap, width, accurate, quick):
    path = layouts.coupled_lines(tmp_path / "c.gds", gap=gap, width=width)
    r = meshsize.suggest_mesh_size(path, stackup_name)
    assert r["suggested_refined_cellsize_um"] == {"accurate": accurate, "quick": quick}
    coupled = r["coupled_lines"]
    if gap <= 2 * width:
        assert coupled and coupled[0]["gap_um"] == pytest.approx(gap)
        assert coupled[0]["parallel_length_um"] == pytest.approx(400.0)
    else:
        assert coupled == []


def test_narrow_trace_alone_sets_the_cell_size(tmp_path, stackup_name):
    path = layouts.coupled_lines(tmp_path / "c.gds", gap=50.0, width=1.2)
    r = meshsize.suggest_mesh_size(path, stackup_name)
    assert r["suggested_refined_cellsize_um"] == {"accurate": 1.0, "quick": 1.0}
    assert r["traces"][0]["width_um"] == pytest.approx(1.2)


def test_short_feature_is_not_a_trace(tmp_path, stackup_name):
    path = layouts.small_patch(tmp_path / "p.gds", size=4.0, layer=layouts.TOPMETAL2)
    r = meshsize.suggest_mesh_size(path, stackup_name)
    assert r["traces"] == [] and r["coupled_lines"] == []
    assert r["suggested_refined_cellsize_um"] == {"accurate": 2.0, "quick": 5.0}


def test_per_layer_alternative_names_the_layer(tmp_path, stackup_name):
    path = layouts.coupled_lines(tmp_path / "c.gds", gap=2.998)
    r = meshsize.suggest_mesh_size(path, stackup_name)
    assert r["per_layer_alternative"]["quick"]["refined_cellsize_override"] == [["TopMetal2", 2.5]]


def test_only_layers_with_shapes_count_as_top_layers(tmp_path, stackup_name):
    # the layout has only Metal1 shapes: TopMetal2/TopMetal1 are empty and must not be "the top 2"
    path = layouts.small_patch(tmp_path / "m1.gds", size=100.0, layer=layouts.METAL1)
    r = meshsize.suggest_mesh_size(path, stackup_name)
    assert [l["name"] for l in r["layers_checked"]] == ["Metal1"]


def test_other_technology_uses_the_feature_itself(tmp_path, pcb_stackup):
    path = layouts.pcb_lines(tmp_path / "pcb.gds", width=500.0, gap=300.0)
    r = meshsize.suggest_mesh_size(path, pcb_stackup)
    # no chip defaults: smallest feature (gap 300 um) rounded down to a 1-2-5 step
    assert r["suggested_refined_cellsize_um"] == {"accurate": 200.0, "quick": 200.0}
    assert "chip defaults don't apply" in " ".join(r["notes"])


def test_other_technology_without_features_has_no_suggestion(tmp_path, pcb_stackup):
    path = layouts.small_patch(tmp_path / "patch.gds")
    r = meshsize.suggest_mesh_size(path, pcb_stackup)
    assert r["suggested_refined_cellsize_um"] == {"accurate": None, "quick": None}
    assert "ask the user" in " ".join(r["notes"])
