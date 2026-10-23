"""check_repeated_patterns tool: repeated shapes found from the cell hierarchy, without flattening.

Each cell's own polygons are grouped once per layer/datatype by bounding-box size and multiplied by how
often the cell is placed in the design (through nested references and arrays). Fill and via fields are
usually one small cell placed as an array, or many identical shapes in one cell: either way they show up
as a size class with many members, at the cost of inspecting each cell once.
"""

import math
import os

from .common import read_stackup

MINCOUNT = 20                  # a size class with this many members is a repeated pattern
# preprocessing is recommended from this many polygons in repeated patterns on: a judgment, not a measured
# meshing limit (a 20-via array meshes in seconds, 50,000 via and fill polygons take very long)
RECOMMEND_FROM = 1000
SIZE_TOL_UM = 1e-3
TILE_TOL = 0.01                # array pitch equal to the cell size within 1 %: the tiles touch


def _placements(library, top):
    """{cell name: number of placements under top}, multiplied through references and arrays,
    plus {cell name: [(columns, rows, (pitch x, pitch y))]} for the arrays that place it."""
    order, seen = [], set()

    def visit(cell):                       # post-order: children before parents
        if cell.name in seen:
            return
        seen.add(cell.name)
        for ref in cell.references:
            child = ref.ref_cell if not isinstance(ref.ref_cell, str) else library.cells.get(ref.ref_cell)
            if child is not None:
                visit(child)
        order.append(cell)

    visit(top)
    count = {cell.name: 0 for cell in order}
    count[top.name] = 1
    arrays = {}
    for cell in reversed(order):           # parents first
        for ref in cell.references:
            child = ref.ref_cell if not isinstance(ref.ref_cell, str) else library.cells.get(ref.ref_cell)
            if child is None:
                continue
            n = int(getattr(ref, "columns", 1)) * int(getattr(ref, "rows", 1))
            count[child.name] += count[cell.name] * n
            if n > 1:
                spacing = getattr(ref, "spacing", None)
                arrays.setdefault(child.name, []).append(
                    (int(ref.columns), int(ref.rows), tuple(float(v) for v in spacing) if spacing is not None else None))
    return count, arrays, order


def check_repeated_patterns(gds_file, stackup_file, cellname="", mincount=MINCOUNT, purposelist=(0,),
                            variable_overrides=None, recommend_from=RECOMMEND_FROM):
    import numpy as np
    from gds_geometry_utils import decompose_polygon_holes
    from gds_simplify_for_em import open_gds_cell

    _, _, dielectrics_list, metals_list = read_stackup(stackup_file, variable_overrides)
    by_layer = {}
    for m in metals_list.metals:
        by_layer.setdefault(int(m.layernum), m)
    library, top = open_gds_cell(os.path.abspath(gds_file), cellname)
    um = library.unit / 1e-6
    count, arrays, cells = _placements(library, top)

    classes = {}          # (layer, datatype, w, h) -> statistics
    edge_um = {}          # layer -> metal edge length to mesh (all shapes, read datatypes)
    total = 0
    for cell in cells:
        placed = count[cell.name]
        if not placed:
            continue
        (cx0, cy0), (cx1, cy1) = cell.get_bounding_box() if cell.get_bounding_box() is not None else ((0, 0), (0, 0))
        for polyset in cell.polygons:
            for pts, layer, datatype in zip(polyset.polygons, polyset.layers, polyset.datatypes):
                layer, datatype = int(layer), int(datatype)
                if layer not in by_layer:
                    continue
                read = not purposelist or datatype in purposelist
                x0, y0 = pts.min(axis=0)
                x1, y1 = pts.max(axis=0)
                if read:
                    total += placed
                    perimeter = float(np.sum(np.hypot(*np.diff(np.vstack([pts, pts[:1]]), axis=0).T)))
                    edge_um[layer] = edge_um.get(layer, 0.0) + perimeter * um * placed
                key = (layer, datatype, round((x1 - x0) * um / SIZE_TOL_UM), round((y1 - y0) * um / SIZE_TOL_UM))
                c = classes.setdefault(key, {"count": 0, "cells": {}, "sample": pts, "read": read})
                c["count"] += placed
                c["cells"][cell.name] = c["cells"].get(cell.name, 0) + placed

    candidates = []
    for (layer, datatype, w, h), c in sorted(classes.items(), key=lambda kv: -kv[1]["count"]):
        if c["count"] < mincount:
            continue
        metal = by_layer[layer]
        decomp = decompose_polygon_holes(c["sample"])
        holes = decomp is not None and bool(decomp["holes"])
        # the cell that contributes most, and whether its arrays put the tiles edge to edge
        main_cell = max(c["cells"], key=c["cells"].get)
        bbox = library.cells[main_cell].get_bounding_box()
        tiles_touch = None
        for columns, rows, pitch in arrays.get(main_cell, []):
            if pitch is None or bbox is None:
                continue
            cw, ch = (bbox[1] - bbox[0])
            touch_x = columns == 1 or abs(pitch[0] - cw) <= TILE_TOL * cw
            touch_y = rows == 1 or abs(pitch[1] - ch) <= TILE_TOL * ch
            tiles_touch = bool(touch_x and touch_y) if tiles_touch is None else (tiles_touch and touch_x and touch_y)
        if metal.is_via:
            kind, action = "via field", "simplify_layout with merge_vias, or merge_polygon_size in the model script"
        elif holes:
            kind = "tiles with holes" + (", placed edge to edge (a plane)" if tiles_touch else "")
            action = "measure_holes, then simplify_layout with fill_ground_holes for repeated openings in ground planes"
        elif tiles_touch:
            kind, action = "tiles placed edge to edge (a plane)", "simplify_layout with merge_per_layer"
        else:
            kind, action = "repeated pieces", ("simplify_layout with remove_floating_fill if they are unconnected dummy "
                                               "fill; ask the user")
        candidates.append({
            "layer": layer, "datatype": datatype, "name": metal.name, "size_um": [w * SIZE_TOL_UM, h * SIZE_TOL_UM],
            "count": c["count"], "share": round(c["count"] / total, 3) if total and c["read"] else 0.0,
            "read_by_model": c["read"], "has_holes": holes, "kind": kind, "suggested_preprocessing": action,
            "cells": dict(sorted(c["cells"].items(), key=lambda kv: -kv[1])[:5]),
            "arrays": [{"columns": cl, "rows": rw, "pitch_um": None if p is None else [round(v * um, 4) for v in p]}
                       for cl, rw, p in arrays.get(main_cell, [])][:3],
            "tiles_touch": tiles_touch,
        })

    repeated = sum(c["count"] for c in candidates if c["read_by_model"])
    share = repeated / total if total else 0.0
    result = {
        "gds_file": os.path.abspath(gds_file), "cell": top.name, "cells_in_hierarchy": len(cells),
        "polygons_read_by_model": total, "polygons_in_repeated_patterns": repeated,
        "repeated_share": round(share, 3),
        "metal_edge_length_mm": {by_layer[l].name: round(v / 1000, 2) for l, v in
                                 sorted(edge_um.items(), key=lambda kv: -kv[1])},
        "preprocessing_candidates": [c for c in candidates if c["read_by_model"]][:15],
        "not_read_by_model": [{"name": c["name"], "datatype": c["datatype"], "count": c["count"]}
                              for c in candidates if not c["read_by_model"]][:10],
        "recommend_preprocessing": repeated >= recommend_from,
        "notes": [
            f"Found from the cell hierarchy without flattening: each cell's polygons are grouped by layer and "
            f"bounding-box size once and counted as often as the cell is placed. A size class with >= {mincount} "
            "members is a repeated pattern (fill, cheesing tiles, via fields).",
            "Before generate_model: if recommend_preprocessing is true, show the candidates to the user and ask "
            "whether to simplify the layout first (simplify_layout, measure_holes). Don't start meshing until they "
            "decided: many repeated shapes can make gmsh run for a very long time. Smaller patterns are listed too, "
            f"but only from {recommend_from} polygons in repeated patterns on is preprocessing recommended "
            "(a judgment, not a measured limit).",
            "metal_edge_length_mm: total length of metal edges that the mesh refinement follows (no time estimate); "
            "useful to compare layouts or the layout before and after simplification.",
            f"Only datatypes {list(purposelist)} are read by the model (purposelist); repeated shapes on other "
            "datatypes are listed separately and don't affect the mesh.",
        ],
    }
    return result
