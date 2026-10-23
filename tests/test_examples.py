"""Regression tests on the real example layouts of the gds2palace, gds_prepare_for_EM and lumpedmodel repositories,
with the values found while developing the tools. Skipped when those repositories are not next to this one."""

import glob
import json
import os

import gdspy
import pytest

from gds2palace_mcp import complexity, holes, meshsize, model_run, ports, settings_check, sparams

pytestmark = pytest.mark.examples
STACKUP = "SG13G2_FEM_200um.xml"


def study(examples, *parts):
    path = os.path.join(str(examples), "more_examples", "mesh_convergence", *parts)
    if not os.path.exists(path):
        pytest.skip(f"{path} not found")
    return path


def workflow(examples, name):
    return os.path.join(str(examples), "workflow", name)


# ------------------------------------------------------------------------------ complexity and holes

def test_t582_complexity_matches_the_flattened_count(t582_layouts):
    path = str(t582_layouts / "T582_BEOL_I1_2x300u.gds")
    r = complexity.check_layout_complexity(path, STACKUP)
    assert r["polygons_read_by_gds2palace"] == 53170
    cell = gdspy.GdsLibrary(infile=path).top_level()[0]
    flat = sum(len(p) for (l, d), p in cell.get_polygons(by_spec=True).items()
               if d == 0 and l in {x["layer"] for x in r["layers"]})
    assert flat == 53170
    assert r["layers"][0]["layer"] == 66 and any("Via4" in h for h in r["hints"])      # the via arrays dominate


def test_t582_ground_plane_openings(t582_layouts):
    r = holes.measure_holes(str(t582_layouts / "T582_BEOL_I1_2x300u.gds"), STACKUP)
    assert r["openings_to_fill"] == 3490
    metal4 = next(l for l in r["layers"] if l["name"] == "Metal4")
    assert {(tuple(o["size_um"]), o["count"]) for o in metal4["repeated_openings"]} == \
        {((14.0, 14.0), 264), ((0.5, 0.5), 210), ((20.0, 20.0), 64)}
    # the frame's core opening and the pad openings are not repeated: they stay
    assert any(o["size_um"] == [1593.0, 293.0] for l in r["layers"] for o in l["other_openings"])


def test_butler_matrix_has_nothing_to_fill(gds2palace_examples):
    r = holes.measure_holes(workflow(gds2palace_examples, "BM_Ardavan_Rahimian_with_ports.gds"), STACKUP)
    assert r["openings_to_fill"] == 0


def test_t582_via_merge_keeps_one_top_cell(t582_layouts, tmp_path):
    r = complexity.simplify_layout(str(t582_layouts / "T582_BEOL_D1_300u.gds"), STACKUP,
                                   output_file=str(tmp_path / "d1.gds"), merge_vias=True)
    assert (r["polygons_before"], r["polygons_after"]) == (24179, 5350)
    assert len(r["top_level_cells"]) == 1 and r["cell"] == "T582_BEOL_D1_300u"


# ------------------------------------------------------------------------------ mesh size

@pytest.mark.parametrize("parts, stackup, accurate, quick", [
    (("mesh_convergence_inductor", "ind_frame_with_ports.gds"), STACKUP, 2.0, 5.0),                    # 5 um trace/gap
    (("mesh_convergence_transformer", "Transformer_IMN_ports.gds"), STACKUP, 2.0, 2.0),               # 2 um gap
    (("mesh_convergence_D-band_balun", "Balun_140-170G_RupokDas_with_ports.gds"), STACKUP, 2.0, 2.0),  # 2 um gap
    (("mesh_convergence_balun2x1", "balun2x1_edgecoupled_do200_w8_s2.gds"), STACKUP, 2.0, 2.0),       # 2 um gap
    (("mesh_convergence_mom_cmos5l", "c4_frame_ports.gds"), "SG13CMOS5L_200um.xml", 0.2, 0.2),        # 0.2 um fingers
])
def test_documented_finest_features_of_the_mesh_convergence_studies(gds2palace_examples, parts, stackup, accurate,
                                                                    quick):
    r = meshsize.suggest_mesh_size(study(gds2palace_examples, *parts), stackup)
    assert r["suggested_refined_cellsize_um"] == {"accurate": accurate, "quick": quick}


def test_study_scripts_with_coarse_cells_get_the_warning(gds2palace_examples):
    for parts in [("mesh_convergence_balun2x1", "balun2x1_edgecoupled_do200_w8_s2.py"),            # 5 um on 2 um gaps
                  ("mesh_convergence_D-band_balun", "palace_balun_mesh5.py")]:
        r = settings_check.check_model_script(study(gds2palace_examples, *parts))
        assert any("refined_cellsize" in w and "larger than the smallest" in w for w in r["warnings"]), parts
    r = settings_check.check_model_script(study(gds2palace_examples, "mesh_convergence_balun2x1",
                                                "balun2x1_edgecoupled_do200_w8_s2_mesh2.py"))
    assert not [w for w in r["warnings"] if "refined_cellsize" in w]


# ------------------------------------------------------------------------------ ports

@pytest.mark.parametrize("script", ["palace_line_viaport.py", "palace_L2n0.py", "palace_core.py",
                                    "palace_ind_frame.py", "palace_butlermatrix.py"])
def test_example_scripts_pass_the_port_check(gds2palace_examples, script):
    tree, defs = ports.ports_from_script(workflow(gds2palace_examples, script))
    assert defs
    r = settings_check.check_model_script(workflow(gds2palace_examples, script))
    assert not [e for e in r["errors"] if "port" in e], r["errors"]


def test_butler_matrix_ports_are_reproduced_from_its_labels(gds2palace_examples, tmp_path):
    source = workflow(gds2palace_examples, "BM_Ardavan_Rahimian_with_ports.gds")
    library = gdspy.GdsLibrary(infile=source)
    cell = library.top_level()[0]
    original = {}
    for p in cell.polygons:
        layer = int(p.layers[0])
        if 201 <= layer <= 208:
            (x0, y0), (x1, y1) = p.polygons[0].min(axis=0), p.polygons[0].max(axis=0)
            original[round(float(x0), 2), round(float(y0), 2), round(float(x1), 2), round(float(y1), 2)] = layer
    cell.remove_polygons(lambda pts, layer, datatype: 201 <= layer <= 208)
    stripped = str(tmp_path / "bm_noports.gds")
    library.write_gds(stripped)
    r = ports.propose_ports(stripped, STACKUP)
    proposed = {(round(min(p["line"][0], p["line"][2]), 2), round(min(p["line"][1], p["line"][3]), 2),
                 round(max(p["line"][0], p["line"][2]), 2), round(max(p["line"][1], p["line"][3]), 2))
                for p in r["proposals"]}
    assert proposed == set(original) and len(r["proposals"]) == 8
    assert [p["portnumber"] for p in r["proposals"]] == list(range(1, 9))          # from the layout's labels
    assert {p["reference_layer"] for p in r["proposals"]} == {"Metal3"}


# ------------------------------------------------------------------------------ S-parameters

def test_inductor_values_agree_with_lumpedmodel(lumped_examples):
    path = str(lumped_examples / "pi_from_s2p" / "inductor_500p.s2p")
    r, _ = sparams.passive_values(path, "inductor", "differential")
    assert r["L_low_frequency_nH"] == pytest.approx(0.4814, abs=0.001)
    assert r["Q_peak"] == pytest.approx(19.295, abs=0.01) and r["Q_peak_at_GHz"] == pytest.approx(42.0)
    assert r["SRF_GHz"] == pytest.approx(87.54, abs=0.05)                         # lumpedmodel: 19.29 at 42 GHz


def test_mim_capacitance_agrees_with_lumpedmodel(lumped_examples):
    path = str(lumped_examples / "mim_from_s2p" / "mim_170fF.s2p")
    r, _ = sparams.passive_values(path, "capacitor", "series")
    assert r["C_low_frequency_fF"] == pytest.approx(171.3, abs=0.5)                # lumpedmodel: 171.29 fF


# ------------------------------------------------------------------------------ resource estimate

def test_estimates_cover_the_palace_runs_they_were_calibrated_on(gds2palace_examples):
    runs = []
    for palace_json in glob.glob(os.path.join(str(gds2palace_examples), "**", "output", "**", "palace.json"),
                                 recursive=True):
        run = os.path.dirname(palace_json)
        while run and not os.path.isfile(os.path.join(run, "config.json")):
            parent = os.path.dirname(run)
            run = parent if parent != run else ""
        if not run:
            continue
        with open(os.path.join(run, "config.json"), encoding="utf-8") as f:
            config = json.load(f)
        if config.get("Model", {}).get("Refinement", {}).get("MaxIts", 0) or "iteration" in palace_json:
            continue
        with open(palace_json, encoding="utf-8") as f:
            data = json.load(f)
        runs.append((os.path.join(run, "config.json"), data))
        if len(runs) == 60:
            break
    if len(runs) < 10:
        pytest.skip("too few Palace runs with results in the gds2palace repository")
    for config_path, data in runs:
        e = model_run.estimate_resources(data["Problem"]["MeshElements"], config_path)
        assert e["unknowns"] == pytest.approx(data["Problem"]["DegreesOfFreedom"], rel=0.08)
        total = data.get("PeakMemoryMegabytes", {}).get("Total")
        if total:
            assert total / 1024 <= e["peak_ram_gb"]["worst_case"], config_path       # never above the worst case
            assert total / 1024 >= 0.5 * e["peak_ram_gb"]["typical"], config_path
