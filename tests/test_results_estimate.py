import json

import pytest

from gds2palace_mcp import model_run, results


def make_run(folder, basename="model", dof=686496, elements=108249, seconds=652.0, ram_mb=11247.375, log=None,
             touchstone=True):
    """A minimal Palace run folder: config.json, output/<model>/palace.json, a log and a Touchstone file."""
    run = folder / "palace_model" / f"{basename}_data"
    out = run / "output" / basename
    out.mkdir(parents=True)
    (run / "config.json").write_text(json.dumps({"Problem": {"Output": f"output/{basename}"}}), encoding="utf-8")
    (out / "palace.json").write_text(json.dumps({
        "ElapsedTime": {"Durations": {"Total": seconds}},
        "PeakMemoryMegabytes": {"Total": ram_mb},
        "Problem": {"DegreesOfFreedom": dof, "MeshElements": elements},
    }), encoding="utf-8")
    if touchstone:
        (out / f"{basename}.s2p").write_text("# GHz S dB R 50\n1 -20 0 -0.1 0 -0.1 0 -20 0\n", encoding="utf-8")
    if log:
        (run / "palace.log").write_text(log, encoding="utf-8")
    return run


def test_summary_of_a_finished_run(tmp_path):
    run = make_run(tmp_path)
    r, images = results.summarize_results(str(run))
    entry = r["runs"][0]
    assert entry["model"] == "model" and images == []
    assert "686,496" in entry["summary"] and "10m 52s" in entry["summary"] and "10.98 GB" in entry["summary"]
    assert [p.endswith("model.s2p") for p in entry["touchstone_files"]] == [True]
    assert "did_not_converge_lines" not in entry


def test_script_path_finds_the_data_folder(tmp_path):
    make_run(tmp_path, "mymodel")
    r, _ = results.summarize_results(str(tmp_path / "mymodel.py"))
    assert r["runs"][0]["model"] == "mymodel"


def test_not_converged_lines_in_saved_logs_are_counted(tmp_path):
    log = "iteration 1\nlinear solve did NOT converge at f=5 GHz\nlinear solve did NOT converge at f=6 GHz\n"
    run = make_run(tmp_path, log=log)
    entry = results.summarize_results(str(run))[0]["runs"][0]
    assert list(entry["did_not_converge_lines"].values()) == [2]


def test_missing_touchstone_is_pointed_out(tmp_path):
    run = make_run(tmp_path, touchstone=False)
    entry = results.summarize_results(str(run))[0]["runs"][0]
    assert entry["touchstone_files"] == [] and "combine_snp" in entry["note"]


def test_several_runs_in_one_folder(tmp_path):
    make_run(tmp_path / "a", "one")
    make_run(tmp_path / "b", "two")
    r, _ = results.summarize_results(str(tmp_path))
    assert sorted(e["model"] for e in r["runs"]) == ["one", "two"]


def test_summary_errors(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        results.summarize_results(str(tmp_path / "nothing_here.py"))
    with pytest.raises(FileNotFoundError, match="No Palace run directory"):
        results.summarize_results(str(tmp_path))


# ------------------------------------------------------------------------------ resource estimate

def config(tmp_path, order=2, complex_coarse=True, max_its=0, max_size=2e6):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"Solver": {"Order": order, "Linear": {"ComplexCoarseSolve": complex_coarse}},
                                "Model": {"Refinement": {"MaxIts": max_its, "MaxSize": max_size}}}), encoding="utf-8")
    return str(path)


def test_unknowns_follow_the_calibrated_ratio(tmp_path):
    e = model_run.estimate_resources(100000, config(tmp_path))
    assert e["unknowns"] == pytest.approx(100000 * 6.775, rel=0.001)
    low, high = e["unknowns_range"]
    assert low < e["unknowns"] < high
    base = 0.5 + 11.7 * e["unknowns"] / 1e6
    assert e["peak_ram_gb"]["typical"] == pytest.approx(1.13 * base, abs=0.1)
    assert e["peak_ram_gb"]["worst_case"] > e["peak_ram_gb"]["typical"]


def test_order_and_coarse_solve_change_the_ram_not_the_ratio_of_formula_inputs(tmp_path):
    base = model_run.estimate_resources(50000, config(tmp_path, order=2, complex_coarse=False))
    complex_ = model_run.estimate_resources(50000, config(tmp_path, order=2, complex_coarse=True))
    first = model_run.estimate_resources(50000, config(tmp_path, order=1, complex_coarse=False))
    assert complex_["peak_ram_gb"]["typical"] > base["peak_ram_gb"]["typical"]
    # order 1 has fewer unknowns per tetrahedron but needs about twice the memory per unknown
    assert first["unknowns"] < base["unknowns"]
    assert first["peak_ram_gb"]["typical"] / first["unknowns"] > 1.8 * base["peak_ram_gb"]["typical"] / base["unknowns"]


def test_amr_reports_the_ram_at_the_unknown_limit(tmp_path):
    e = model_run.estimate_resources(50000, config(tmp_path, max_its=4, max_size=2e6))
    assert e["amr"]["iterations"] == 4 and e["amr"]["max_unknowns"] == 2000000
    assert e["amr"]["peak_ram_gb_at_max_unknowns"]["typical"] > e["peak_ram_gb"]["worst_case"]


def test_msh_counts_tetrahedra_only(tmp_path):
    msh = tmp_path / "m.msh"
    msh.write_text("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n5\n1 0 0 0\n2 1 0 0\n3 0 1 0\n4 0 0 1\n5 1 1 1\n"
                   "$EndNodes\n$Elements\n4\n1 2 2 1 1 1 2 3\n2 4 2 1 1 1 2 3 4\n3 4 2 1 1 2 3 4 5\n"
                   "4 1 2 1 1 1 2\n$EndElements\n", encoding="ascii")
    assert model_run._msh_counts(str(msh)) == {"nodes": 5, "elements": 4, "tetrahedra": 2}
