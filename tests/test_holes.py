from gds2palace_mcp import complexity, holes


def layer(result, name):
    return next(l for l in result["layers"] if l["name"] == name)


def sizes(openings):
    return [(o["size_um"], o["count"]) for o in openings]


def test_measure_holes_finds_repeated_and_other_openings(ground_gds, stackup_name):
    r = holes.measure_holes(ground_gds, stackup_name)
    metal1 = layer(r, "Metal1")
    # 48 holes drawn inside tiles + 48 gaps that only exist when the strips are merged
    assert sizes(metal1["repeated_openings"]) == [([8.0, 8.0], 96)]
    assert sizes(metal1["other_openings"]) == [([40.0, 40.0], 1)]
    assert metal1["ground_planes"] == 1
    # the slotted signal line is no ground plane: its slots are only counted
    line = layer(r, "TopMetal1")
    assert line["repeated_openings"] == [] and line["holes_outside_ground_planes"] == 24
    assert r["openings_to_fill"] == 96 and r["suggestion"]["fill_ground_holes"] is True


def test_measure_holes_mincount_is_the_repeat_threshold(ground_gds, stackup_name):
    r = holes.measure_holes(ground_gds, stackup_name, mincount=100)
    assert r["openings_to_fill"] == 0 and r["suggestion"]["fill_ground_holes"] is False


def test_measure_holes_nothing_in_a_line_layout(line_gds, stackup_name):
    r = holes.measure_holes(line_gds, stackup_name)
    assert r["openings_to_fill"] == 0
    assert all(not l["repeated_openings"] and not l["other_openings"] for l in r["layers"])


def test_fill_ground_holes_fills_exactly_the_repeated_openings(copy_to, ground_gds, stackup_name):
    source = copy_to(ground_gds, "ground.gds")
    r = complexity.simplify_layout(source, stackup_name, fill_ground_holes=True)
    metal1 = next(l for l in r["changed_layers"] if l["layer"] == 8)
    assert metal1["after"] < metal1["before"] / 10
    after = holes.measure_holes(r["output_file"], stackup_name)
    m1 = layer(after, "Metal1")
    assert m1["repeated_openings"] == []                                    # all 96 filled
    assert sizes(m1["other_openings"]) == [([40.0, 40.0], 1)]               # circuit opening kept
    assert layer(after, "TopMetal1")["holes_outside_ground_planes"] == 24   # slots kept
    assert after["openings_to_fill"] == 0
