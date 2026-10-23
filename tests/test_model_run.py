import shutil

import pytest

import layouts
from gds2palace_mcp import model_run


def write(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_a_failing_script_is_reported_not_raised(tmp_path):
    path = write(tmp_path, "boom.py", "raise RuntimeError('boom')\n")
    r = model_run.generate_model(path, timeout_s=120)
    assert r["ok"] is False and r["returncode"] != 0 and r["models"] == []
    assert any("boom" in line or "Traceback" in line for line in r["important_lines"] + r["output_tail"])


def test_a_script_that_writes_no_model_is_not_ok(tmp_path):
    path = write(tmp_path, "nothing.py", "print('hello')\n")
    r = model_run.generate_model(path, timeout_s=120)
    assert r["ok"] is False and "was not called" in r["note"]


def test_missing_script(tmp_path):
    with pytest.raises(FileNotFoundError):
        model_run.generate_model(str(tmp_path / "missing.py"))


@pytest.mark.slow
def test_generate_model_meshes_and_estimates(tmp_path, line_gds):
    shutil.copy(line_gds, tmp_path / "line.gds")
    # 'nogui' is a typo for no_gui: gds2palace ignores it, generate_model must still run without a gmsh window
    script = layouts.model_script("line.gds", extra_settings="settings['nogui'] = True").replace(
        "settings['no_gui'] = True\n", "")
    path = write(tmp_path, "line_model.py", script)
    r = model_run.generate_model(path, timeout_s=600, save_geometry=True)
    assert r["ok"], r["output_tail"]
    model = r["models"][0]
    assert all(model["files"].values()) and model["files"].keys() >= {"config.json", "port_information.json", "run_sim"}
    assert model["tetrahedra"] > 1000 and model["elements"] > model["tetrahedra"]
    assert model["estimate"]["unknowns"] == pytest.approx(model["tetrahedra"] * 6.775, rel=0.001)
    assert model["estimate"]["peak_ram_gb"]["worst_case"] >= model["estimate"]["peak_ram_gb"]["typical"] > 0
    assert model["geometry_file"].endswith(".geo_unrolled")
    # ANSI colors of gmsh's warnings are removed from the log
    assert not any("\x1b[" in line for line in r["output_tail"] + r["important_lines"])
