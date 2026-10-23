import layouts
from gds2palace_mcp import settings_check


def script(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_known_settings_come_from_the_installed_gds2palace():
    known = settings_check.known_settings_keys()
    assert {"refined_cellsize", "no_gui", "conductor_model", "fstop", "adaptive_mesh_iterations"} <= known
    assert "nogui" not in known


def test_clean_script_has_no_errors_or_warnings(tmp_path, line_gds, copy_to):
    copy_to(line_gds, "line.gds")
    r = settings_check.check_model_script(script(tmp_path, "model.py", layouts.model_script("line.gds")))
    assert r["ok"], r["errors"]
    assert r["warnings"] == []


def test_typos_missing_settings_and_legacy_settings(tmp_path, copy_to, line_gds):
    copy_to(line_gds, "line.gds")
    text = layouts.model_script("line.gds", cellsize=5).replace("settings['no_gui'] = True", "settings['nogui'] = True")
    text = text.replace("settings['refined_cellsize'] = 5", "settings['refined_cellsiz'] = 5")
    text = text.replace("settings = {}", "settings = {}\nsettings['filled_metals'] = True\nsettings['z_thickness_factor'] = 2")
    r = settings_check.check_model_script(script(tmp_path, "bad.py", text))
    assert not r["ok"]
    assert any("settings['refined_cellsize'] is not set" in e for e in r["errors"])
    warnings = " | ".join(r["warnings"])
    assert "settings['nogui']" in warnings and "Did you mean 'no_gui'" in warnings
    assert "settings['refined_cellsiz']" in warnings and "Did you mean 'refined_cellsize'" in warnings
    assert "z_thickness_factor" in warnings and "removed" in warnings
    assert any("filled_metals" in i and "conductor_model" in i for i in r["info"])


def test_port_layers_must_be_added_to_the_layer_list(tmp_path, copy_to, line_gds):
    copy_to(line_gds, "line.gds")
    text = layouts.model_script("line.gds").replace("layernumbers.extend(simulation_ports.portlayers)\n", "")
    r = settings_check.check_model_script(script(tmp_path, "noports.py", text))
    assert any("portlayers is never used" in e for e in r["errors"])


def test_script_ports_are_checked_against_the_layout(tmp_path):
    layouts.line_over_ground(tmp_path / "shifted.gds", port_ref_x=-40.0)
    r = settings_check.check_model_script(script(tmp_path, "model.py", layouts.model_script("shifted.gds")))
    assert not r["ok"]
    assert any("script line" in e and "no TopMetal2 metal" in e for e in r["errors"])


def coupled_script(tmp_path, cellsize, extra=""):
    layouts.coupled_lines(tmp_path / "c.gds", gap=3.0, width=8.0)
    return script(tmp_path, "model.py", layouts.model_script("c.gds", cellsize=cellsize, extra_settings=extra,
                                                              ports=""))


def messages(result):
    return [m for m in result["warnings"] + result["info"] if "refined_cellsize" in m]


def test_cell_size_above_the_smallest_feature_is_a_warning(tmp_path):
    r = settings_check.check_model_script(coupled_script(tmp_path, 5))
    warning = next(w for w in r["warnings"] if "refined_cellsize" in w)
    assert "larger than the smallest trace width or coupled-line gap on TopMetal2 (3 um)" in warning
    assert "Use 3 um or less" in warning and "explicitly asked" in warning


def test_cell_size_at_or_below_the_feature_passes(tmp_path):
    r = settings_check.check_model_script(coupled_script(tmp_path, 3))
    assert not [w for w in r["warnings"] if "refined_cellsize" in w]
    assert any("fine for a quick model" in i for i in r["info"])          # but above the accurate 2 um
    r = settings_check.check_model_script(coupled_script(tmp_path, 2))
    assert messages(r) == []


def test_per_layer_override_takes_precedence(tmp_path):
    extra = "settings['refined_cellsize_override'] = [['TopMetal2', 2.5]]"
    r = settings_check.check_model_script(coupled_script(tmp_path, 5, extra))
    assert not [w for w in r["warnings"] if "refined_cellsize" in w]
    too_coarse = "settings['refined_cellsize_override'] = [['TopMetal2', 4]]"
    r = settings_check.check_model_script(coupled_script(tmp_path, 5, too_coarse))
    assert any("refined_cellsize_override for TopMetal2 = 4 um" in w for w in r["warnings"])


def test_setupem_style_scripts_are_understood(tmp_path):
    layouts.coupled_lines(tmp_path / "c.gds", gap=3.0, width=8.0)
    text = ("settings = {}\nsettings['GdsFile'] = 'c.gds'\nsettings['SubstrateFile'] = 'SG13G2_FEM_200um.xml'\n"
            "settings['cellname'] = ''\nsettings['refined_cellsize'] = 5\n")
    r = settings_check.check_model_script(script(tmp_path, "setupem.py", text))
    assert any("larger than the smallest trace width" in w for w in r["warnings"])
