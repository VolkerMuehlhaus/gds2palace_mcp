"""Layout tool: summarize a GDSII file per layer, optionally against a stackup."""

import os

import numpy as np

from .common import read_stackup

# merging and gap search get slow on dummy fill; above this, only counts and extents are reported
MAX_SHAPES_FOR_GAPS = 20000


def _read_library(gds_file):
    """The GDSII file as a gdstk library."""
    import gdstk
    return gdstk.read_gds(os.path.abspath(gds_file))


def _select_cell(library, cellname):
    """Same choice as gds2palace's read_gds() (select_design_cell): the named cell, else the design cell, the
    top-level cell with the largest bounding box, never KLayout's $$$CONTEXT_INFO$$$. Returns (cell, note)."""
    from gds2palace.util_gds_reader import select_design_cell
    cell = select_design_cell(library, cellname)
    note = (f"Cell '{cellname}' not found, used the design cell '{cell.name}' (the largest top-level cell)."
            if cellname and cell.name != cellname else None)
    return cell, note


def _by_spec(cell):
    """{(layer, datatype): [point arrays]} of the cell with its hierarchy flattened, paths as polygons."""
    by_spec = {}
    for p in cell.get_polygons(include_paths=True):
        by_spec.setdefault((p.layer, p.datatype), []).append(p.points)
    return by_spec


def _gap_and_width(polygons):
    """Smallest gap between separate merged shapes, and smallest bounding box side of the drawn shapes."""
    import shapely
    from shapely.geometry import Polygon
    from shapely.strtree import STRtree

    smallest_side = min(float(np.min(p.max(axis=0) - p.min(axis=0))) for p in polygons)
    if len(polygons) > MAX_SHAPES_FOR_GAPS:
        return smallest_side, None, f"more than {MAX_SHAPES_FOR_GAPS} shapes, gap not computed"

    shapes = [Polygon(p).buffer(0) for p in polygons if len(p) >= 3]
    merged = shapely.unary_union(shapes)
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    parts = [p for p in parts if not p.is_empty]
    if len(parts) < 2:
        return smallest_side, None, "one connected shape, no gap"
    tree = STRtree(parts)
    _, distances = tree.query_nearest(parts, exclusive=True, return_distance=True, all_matches=False)
    return smallest_side, float(np.min(distances)), None


def _stackup_layer_roles(stackup_file, variable_overrides):
    path, _, dielectrics_list, metals_list = read_stackup(stackup_file, variable_overrides)
    roles = {}
    for m in metals_list.metals:
        roles.setdefault(int(m.layernum), []).append(f"{m.type.lower()} {m.name}")
    for n in dielectrics_list.get_boundary_layers():
        roles.setdefault(int(n), []).append("dielectric boundary")
    for d in metals_list.derived_layers.derived_layers:
        for o in d.operands:
            roles.setdefault(int(o), []).append(f"operand of derived layer {d.name} ({d.layernum})")
    return path, roles


def inspect_gds(gds_file, cellname="", stackup_file="", variable_overrides=None):
    gds_file = os.path.abspath(gds_file)
    library = _read_library(gds_file)
    cell, note = _select_cell(library, cellname)
    by_spec = _by_spec(cell)   # flattened hierarchy, keys (layer, datatype)

    roles = {}
    stackup_path = None
    if stackup_file:
        stackup_path, roles = _stackup_layer_roles(stackup_file, variable_overrides)

    layers = []
    all_points = []
    for (layer, datatype), polygons in sorted(by_spec.items()):
        if not polygons:
            continue
        points = np.vstack(polygons)
        all_points.append(points)
        lo, hi = points.min(axis=0), points.max(axis=0)
        smallest_side, gap, gap_note = _gap_and_width(polygons)
        entry = {
            "layer": int(layer), "datatype": int(datatype), "shapes": len(polygons),
            "bbox": [round(float(v), 4) for v in (*lo, *hi)],
            "smallest_shape_side": round(smallest_side, 4),
            "smallest_gap": None if gap is None else round(gap, 4),
        }
        if gap_note:
            entry["gap_note"] = gap_note
        if stackup_file:
            if layer in roles:
                entry["stackup_role"] = roles[layer]
            elif 201 <= layer <= 249:
                entry["stackup_role"] = ["not in stackup: conventional port/thermal layer range"]
            else:
                entry["stackup_role"] = ["not in stackup: ignored by gds2palace"]
        layers.append(entry)

    result = {
        "gds_file": gds_file,
        "cell": cell.name,
        "top_level_cells": [c.name for c in library.top_level()],
        "unit_m": library.unit,
        "bbox": None,
        "layers": layers,
        "notes": [
            "Coordinates are in the GDSII user unit (unit_m; 1e-06 = microns), which gds2palace uses as they are "
            "together with settings['unit'].",
            "Hierarchy is flattened, like gds2palace does.",
            "smallest_shape_side is the shorter side of the smallest shape's bounding box: the line width for "
            "rectangles, an upper bound for other shapes. smallest_gap is the closest distance between separate "
            "(merged) shapes on the same layer. Use both to choose refined_cellsize and merge_polygon_size.",
            "gds2palace reads only datatypes in purposelist (IHP drawing data is datatype 0).",
        ],
    }
    if all_points:
        points = np.vstack(all_points)
        result["bbox"] = [round(float(v), 4) for v in (*points.min(axis=0), *points.max(axis=0))]
    if note:
        result["notes"].insert(0, note)
    if stackup_path:
        result["stackup_file"] = stackup_path
        drawn = {e["layer"] for e in layers}
        result["stackup_layers_without_shapes"] = sorted(n for n in roles if n not in drawn)
    return result
