import sys
import time

import pytest

import layouts
from gds2palace_mcp import model_run, patterns


def candidate(result, name):
    return next(c for c in result["preprocessing_candidates"] if c["name"] == name)


def test_placements_are_multiplied_through_nested_arrays(tmp_path, stackup_name):
    path = layouts.nested_arrays(tmp_path / "nested.gds")
    r = patterns.check_repeated_patterns(path, stackup_name)
    vias = candidate(r, "TopVia2")
    assert vias["count"] == 600 and vias["kind"] == "via field" and vias["cells"] == {"VIA": 600}
    assert "merge_vias" in vias["suggested_preprocessing"]
    # 1 + 1 pad per BLOCK (6 each), 600 vias, 64 tiles
    assert r["polygons_read_by_model"] == 600 + 6 + 6 + 64


def test_tiles_with_holes_placed_edge_to_edge_are_a_plane(tmp_path, stackup_name):
    r = patterns.check_repeated_patterns(layouts.nested_arrays(tmp_path / "n.gds"), stackup_name)
    tiles = candidate(r, "Metal1")
    assert tiles["count"] == 64 and tiles["size_um"] == [20.0, 20.0]
    assert tiles["has_holes"] is True and tiles["tiles_touch"] is True
    assert "edge to edge" in tiles["kind"] and "fill_ground_holes" in tiles["suggested_preprocessing"]
    assert tiles["arrays"] == [{"columns": 8, "rows": 8, "pitch_um": [20.0, 20.0]}]


def test_tiles_without_holes(tmp_path, stackup_name):
    r = patterns.check_repeated_patterns(layouts.nested_arrays(tmp_path / "n.gds", tile_holes=False), stackup_name)
    tiles = candidate(r, "Metal1")
    assert tiles["has_holes"] is False and "merge_per_layer" in tiles["suggested_preprocessing"]


def test_shapes_on_datatypes_the_model_does_not_read_are_listed_apart(tmp_path, stackup_name):
    r = patterns.check_repeated_patterns(layouts.nested_arrays(tmp_path / "n.gds", fill_datatype=22), stackup_name)
    assert {"name": "Metal1", "datatype": 22, "count": 50} in r["not_read_by_model"]
    assert all(c["datatype"] == 0 for c in r["preprocessing_candidates"])
    assert r["polygons_read_by_model"] == 676                     # the fill is not counted


def test_preprocessing_is_recommended_only_for_many_repeated_shapes(tmp_path, stackup_name):
    small = patterns.check_repeated_patterns(layouts.nested_arrays(tmp_path / "s.gds"), stackup_name)
    assert small["preprocessing_candidates"] and small["recommend_preprocessing"] is False      # 664 < 1000
    big = patterns.check_repeated_patterns(layouts.nested_arrays(tmp_path / "b.gds", inner=(20, 20)), stackup_name)
    assert big["recommend_preprocessing"] is True                                             # 2400 vias
    assert candidate(big, "TopVia2")["count"] == 2400


def test_flat_layout_patterns_without_hierarchy(ground_gds, stackup_name):
    r = patterns.check_repeated_patterns(ground_gds, stackup_name)
    # 48 tiles drawn as one polygon with a hole; the strip tiles give 4 x 48 strips of 2 sizes
    tiles = next(c for c in r["preprocessing_candidates"] if c["has_holes"])
    assert tiles["count"] == 48 and tiles["size_um"] == [20.0, 20.0] and tiles["tiles_touch"] is None
    assert r["cells_in_hierarchy"] == 1


def test_simple_layout_has_no_candidates(line_gds, stackup_name):
    r = patterns.check_repeated_patterns(line_gds, stackup_name)
    assert r["preprocessing_candidates"] == [] and r["recommend_preprocessing"] is False
    assert r["metal_edge_length_mm"]["TopMetal2"] == pytest.approx(2 * (800 + 15) / 1000, rel=1e-6)


# ------------------------------------------------------------------------------ meshing jobs

def script(tmp_path, seconds):
    path = tmp_path / f"sleep{seconds}.py"
    path.write_text(f"import time\nprint('started', flush=True)\ntime.sleep({seconds})\nprint('done')\n",
                    encoding="utf-8")
    return str(path)


def test_long_run_keeps_running_and_waiting_longer_returns_the_result(tmp_path):
    path = script(tmp_path, 5)
    r = model_run.generate_model(path, timeout_s=1, show_log=False)
    assert r["still_running"] is True and r["job_id"] in model_run.JOBS
    assert "Ask the user" in r["note"] and "does not stop" in r["note"] and "check_repeated_patterns" in r["note"]
    assert r["log_file"] == str(tmp_path / "sleep5_generate.log") and r["log_window"] is None
    done = model_run.wait_for_model(r["job_id"], timeout_s=60)
    assert "still_running" not in done and done["returncode"] == 0
    assert any("done" in line for line in done["output_tail"])
    assert done["ok"] is False and "was not called" in done["note"]      # it wrote no model
    # the log stays next to the script and ends with the finished line
    log = (tmp_path / "sleep5_generate.log").read_text(encoding="utf-8")
    assert "closing it does not stop the run" in log.splitlines()[0]
    assert "started" in log and log.rstrip().splitlines()[-1].startswith(model_run.FINISHED + " (exit code 0")
    assert r["job_id"] not in model_run.JOBS
    with pytest.raises(ValueError, match="No running model job"):
        model_run.wait_for_model(r["job_id"])


def test_ansi_colors_are_removed_from_the_result(tmp_path):
    path = tmp_path / "colors.py"
    path.write_text("print('\\x1b[33mWarning: colored\\x1b[0m')\n", encoding="utf-8")
    r = model_run.generate_model(str(path), timeout_s=60)
    assert "Warning: colored" in r["output_tail"]
    assert r["log_window"] == "test window (not opened)"


def test_log_window_failure_is_reported_not_raised(tmp_path, monkeypatch):
    def no_window(log_path, title):
        raise OSError("no display")
    monkeypatch.setattr(model_run, "open_log_window", no_window)
    r = model_run.generate_model(script(tmp_path, 0), timeout_s=60)
    assert r["returncode"] == 0 and "still_running" not in r and r["log_window"] == "none (no display)"


def test_log_window_opens_with_the_log_path(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(model_run, "open_log_window", lambda log_path, title: opened.append(log_path) or "window")
    r = model_run.generate_model(script(tmp_path, 3), timeout_s=1)
    assert opened == [r["log_file"]] and r["log_window"] == "window"
    model_run.wait_for_model(r["job_id"], timeout_s=60)


@pytest.mark.skipif(sys.platform == "win32" or sys.platform == "darwin", reason="Linux display check")
def test_no_log_window_without_display(tmp_path, monkeypatch):
    from gds2palace_mcp import common
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with pytest.raises(OSError, match="no display"):
        common.open_log_window(str(tmp_path / "x.log"), "t")


@pytest.mark.skipif(sys.platform == "win32" or sys.platform == "darwin", reason="Linux terminal choice")
def test_a_terminal_that_closes_at_once_is_skipped(tmp_path, monkeypatch):
    from gds2palace_mcp import common
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in [("x-terminal-emulator", "exit 0"), ("xterm", f'echo "$@" > {bin_dir}/args; exec /bin/sleep 3')]:
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("DISPLAY", ":0")
    assert common.open_log_window(str(tmp_path / "x.log"), "t") == "xterm window"
    for _ in range(50):
        if (bin_dir / "args").exists():
            break
        time.sleep(0.1)
    assert (bin_dir / "args").read_text().strip() == f"-e sh -c tail -n +1 -f {tmp_path / 'x.log'}"
    (bin_dir / "xterm").write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(OSError, match="closed at once"):
        common.open_log_window(str(tmp_path / "x.log"), "t")
