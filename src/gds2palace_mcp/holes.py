"""measure_holes tool: repeated openings in ground planes, measured in the layout at run time.

Uses gds_prepare_for_EM's ground_plane_holes() (gds_simplify_for_em), the same measurement that
simplify_layout's fill_ground_holes fills from: each metal layer is merged, so holes inside tiles
and gaps between side-by-side tiles are the same kind of opening; only openings in ground planes
are candidates, and only sizes that repeat at least mincount times are filled.
"""

import os

from .common import captured_output, read_stackup


def measure_holes(gds_file, stackup_file, cellname="", mincount=20, variable_overrides=None):
    from gds_simplify_for_em import ground_plane_holes, layers_from_stackup

    from .ports import _layout

    _, _, _, metals_list = read_stackup(stackup_file, variable_overrides)
    metal_layers, _ = layers_from_stackup(metals_list)
    names = {int(m.layernum): m.name for m in metals_list.metals}
    library, cell, note = _layout(gds_file, cellname)
    um = library.unit / 1e-6
    with captured_output():
        measured = ground_plane_holes(cell, metal_layers, mincount=mincount, size_tol=1e-3 / um)

    def describe(size, holes):
        x, y = holes[0].centroid.coords[0]
        return {"size_um": [round(size[0] * um, 3), round(size[1] * um, 3)], "count": len(holes),
                "area_um2": round(holes[0].area * um * um, 4), "example_at": [round(x * um, 2), round(y * um, 2)]}

    layers, to_fill = [], 0
    for (layer, datatype), v in sorted(measured.items()):
        if not (v["repeated"] or v["other"] or v["outside_ground"]):
            continue
        repeated = sorted((describe(s, h) for s, h in v["repeated"].items()), key=lambda d: -d["count"])
        other = sorted((describe(s, h) for s, h in v["other"].items()),
                       key=lambda d: -d["size_um"][0] * d["size_um"][1])
        to_fill += sum(d["count"] for d in repeated)
        layers.append({"layer": layer, "datatype": datatype, "name": names.get(layer),
                       "ground_planes": v["ground_planes"], "repeated_openings": repeated,
                       "other_openings": other[:5], "other_openings_total": sum(d["count"] for d in other),
                       "holes_outside_ground_planes": v["outside_ground"]})

    result = {
        "gds_file": os.path.abspath(gds_file), "cell": cell.name, "mincount": mincount, "layers": layers,
        "openings_to_fill": to_fill,
        "suggestion": ({"fill_ground_holes": True,
                        "reason": f"{to_fill} repeated openings in ground planes would be filled (sizes listed per "
                                  "layer); other openings and holes outside ground planes stay."}
                       if to_fill else
                       {"fill_ground_holes": False,
                        "reason": "No repeated openings in ground planes: nothing to fill."}),
        "notes": [
            "Measured at run time on the merged metal layers: holes drawn inside tiles and gaps between "
            "side-by-side tiles both count. A merged shape is a ground plane if, with its holes filled, it covers "
            ">= 10 % of the layout's bounding box and >= 60 % of its own.",
            f"Repeated: >= {mincount} openings of the same bounding-box size on a layer (cheesing, tiled mesh). "
            "Other openings (circuit openings, pads, the core opening of a frame) stay open. There is no size "
            "threshold: only the repeated sizes listed are filled.",
            "Holes in shapes that are not ground planes (e.g. small dummy fill tiles) are not candidates; "
            "remove_floating_fill handles dummy fill.",
        ],
    }
    if note:
        result["notes"].insert(0, note)
    return result
