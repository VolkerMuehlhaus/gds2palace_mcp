"""Without a cell name, the tools take the design cell, as gds2palace's read_gds() does: the largest top-level cell,
never KLayout's $$$CONTEXT_INFO$$$ or a small device cell placed nowhere, whatever their order in the file."""

import gdstk

from gds2palace_mcp import complexity, layout, ports


def multi_top(path):
    lib = gdstk.Library()
    lib.new_cell("$$$CONTEXT_INFO$$$").add(gdstk.rectangle((0, 0), (1000, 1000), layer=1))
    lib.new_cell("small_device").add(gdstk.rectangle((0, 0), (2, 2), layer=8))
    lib.new_cell("DESIGN").add(gdstk.rectangle((0, 0), (50, 40), layer=8))
    lib.write_gds(str(path))
    return str(path)


def test_tools_take_the_design_cell(tmp_path, stackup_name):
    path = multi_top(tmp_path / "multi.gds")
    assert gdstk.read_gds(path).top_level()[0].name != "DESIGN"
    assert layout.inspect_gds(path)["cell"] == "DESIGN"
    assert complexity.check_layout_complexity(path, stackup_name)["cell"] == "DESIGN"
    library, cell, note = ports._layout(path, "")
    assert cell.name == "DESIGN" and note is None
    _, cell, note = ports._layout(path, "nope")
    assert cell.name == "DESIGN" and "not found" in note
    assert ports._layout(path, "small_device")[1].name == "small_device"
