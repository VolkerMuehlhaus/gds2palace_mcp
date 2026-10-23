import hashlib

import pytest

import layouts
from gds2palace_mcp import ports


def via_port(number, layer, from_layer="Metal1", to_layer="TopMetal2", **extra):
    return {"portnumber": number, "voltage": 1, "port_Z0": 50, "source_layernum": layer,
            "from_layername": from_layer, "to_layername": to_layer, "direction": "z", **extra}


def inplane_port(number, layer, target="TopMetal2", direction="x"):
    return {"portnumber": number, "voltage": 1, "port_Z0": 50, "source_layernum": layer,
            "target_layername": target, "direction": direction}


LINE_PORTS = [via_port(1, 201), via_port(2, 202)]


def check(gds, port_list, stackup_name="SG13G2_FEM_200um.xml"):
    return ports.check_ports(gds, stackup_name, port_list)


def test_connected_via_ports_pass(line_gds):
    r = check(line_gds, LINE_PORTS)
    assert r["ok"] and r["errors"] == [] and r["warnings"] == []
    for p in r["ports"]:
        assert p["coverage"] == {"Metal1": 1.0, "TopMetal2": 1.0}


def test_port_line_off_the_trace_is_an_error(tmp_path):
    path = layouts.line_over_ground(tmp_path / "shifted.gds", port_ref_x=-40.0)   # off the line, still on the plane
    r = check(path, LINE_PORTS)
    assert not r["ok"]
    assert any("port 1" in e and "no TopMetal2 metal" in e for e in r["errors"])
    assert not any("port 2" in e for e in r["errors"])


def test_port_layer_collisions_and_missing_shapes(line_gds):
    r = check(line_gds, [via_port(1, 134), via_port(1, 203), via_port(2, 202, to_layer="NoSuchMetal")])
    text = " | ".join(r["errors"])
    assert "GDS layer 134 is a stackup layer" in text
    assert "port number 1 is used twice" in text
    assert "no shape on GDS layer 203" in text
    assert "NoSuchMetal" in text and not r["ok"]


def test_direction_and_voltage(line_gds):
    bad = via_port(1, 201, direction="x")
    off = via_port(2, 202, voltage=0)
    r = check(line_gds, [bad, off])
    assert any("direction z or -z" in e for e in r["errors"])
    assert any("voltage 0" in i for i in r["info"])


def test_inplane_port_across_a_gap(tmp_path):
    good = layouts.inplane_gap(tmp_path / "gap.gds")
    r = check(good, [inplane_port(1, 201)])
    assert r["ok"], r["errors"]
    assert r["ports"][0]["type"] == "in-plane" and r["ports"][0]["end_coverage"] == [1.0, 1.0]
    off = layouts.inplane_gap(tmp_path / "off.gds", shift_port=30.0)               # rectangle beside the pads
    r = check(off, [inplane_port(1, 201)])
    assert not r["ok"] and any("touches no TopMetal2 metal" in e for e in r["errors"])


def test_ports_are_read_from_a_script(tmp_path, line_gds):
    script = layouts.model_script("line.gds")
    path = tmp_path / "model.py"
    path.write_text(script, encoding="utf-8")
    _, defs = ports.ports_from_script(str(path))
    assert [(d["portnumber"], d["source_layernum"], d["from_layername"], d["to_layername"]) for d in defs] == \
        [(1, 201, "Metal1", "TopMetal2"), (2, 202, "Metal1", "TopMetal2")]


def test_proposals_reproduce_the_line_ends(tmp_path, stackup_name):
    path = layouts.line_over_ground(tmp_path / "noports.gds", ports=False)
    r = ports.propose_ports(path, stackup_name)
    assert [p["portnumber"] for p in r["proposals"]] == [1, 2]
    left, right = r["proposals"]
    assert left["source"] == "trace end" and left["signal_layer"] == "TopMetal2" and left["reference_layer"] == "Metal1"
    assert sorted(left["line"][1::2]) == pytest.approx([-7.5, 7.5]) and left["line"][0] == left["line"][2] == 0.0
    assert right["line"][0] == right["line"][2] == 800.0
    assert left["add_port"].startswith("simulation_ports.add_port(")


def test_integer_labels_give_the_port_numbers(tmp_path, stackup_name):
    path = layouts.line_over_ground(tmp_path / "labels.gds", ports=False, labels=("2", "1"))
    r = ports.propose_ports(path, stackup_name)
    by_number = {p["portnumber"]: p for p in r["proposals"]}
    assert by_number[2]["line"][0] == 0.0 and by_number[1]["line"][0] == 800.0     # label "2" is on the left
    assert {p["source"] for p in r["proposals"]} == {"label"}


def test_proposed_ports_round_trip_through_add_ports(tmp_path, stackup_name):
    source = layouts.line_over_ground(tmp_path / "noports.gds", ports=False)
    proposals = ports.propose_ports(source, stackup_name)["proposals"]
    written = ports.add_ports(source, [{"portnumber": p["portnumber"], "line": p["line"],
                                        "source_layernum": p["source_layernum"]} for p in proposals])
    assert written["output_file"].endswith("noports_ports.gds")
    defs = [via_port(p["portnumber"], p["source_layernum"], p["reference_layer"], p["signal_layer"])
            for p in proposals]
    assert check(written["output_file"], defs)["ok"]


def test_add_ports_never_overwrites_or_collides(tmp_path, line_gds):
    with open(line_gds, "rb") as f:
        before = hashlib.sha1(f.read()).hexdigest()
    with pytest.raises(ValueError, match="never overwritten"):
        ports.add_ports(line_gds, [{"portnumber": 3, "line": [0, 0, 0, 5]}], output_file=line_gds)
    with pytest.raises(ValueError, match="already has shapes"):
        ports.add_ports(line_gds, [{"portnumber": 1, "line": [0, 0, 0, 5]}], output_file=str(tmp_path / "o.gds"))
    with pytest.raises(ValueError, match="horizontal or vertical"):
        ports.add_ports(line_gds, [{"portnumber": 3, "line": [0, 0, 5, 5]}], output_file=str(tmp_path / "o.gds"))
    with open(line_gds, "rb") as f:
        assert hashlib.sha1(f.read()).hexdigest() == before


def test_no_candidates_says_so(tmp_path, stackup_name):
    path = layouts.small_patch(tmp_path / "patch.gds", size=20.0, layer=layouts.TOPMETAL2)
    r = ports.propose_ports(path, stackup_name)
    assert r["proposals"] == [] and "ask the user" in " ".join(r["notes"])
