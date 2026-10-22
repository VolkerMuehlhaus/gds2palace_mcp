"""suggest_mesh_size tool: traces and coupled lines on the top conductor layers -> refined_cellsize."""

import math
import os

import numpy as np

from .common import read_stackup

# refined_cellsize defaults in microns (AGENTS.md template / mesh convergence studies)
DEFAULT_ACCURATE_UM = 2.0
DEFAULT_QUICK_UM = 5.0

TOP_LAYERS = 2                 # conductor layers checked, from the top of the stackup
RELEVANT_FRACTION = 0.20       # trace / coupled section length vs. the larger layout side
TRACE_ASPECT = 10.0            # trace length vs. its width
COUPLING_GAP_FACTOR = 2.0      # coupled lines: gap <= this x the narrower line width
MAX_DISTANCE_UM = 50.0         # widths and gaps searched up to this distance
PARALLEL_TOL_DEG = 1.0
MIN_OVERLAP_UM = 0.1


def round_down_cellsize(value_um):
    """Round down to 0.5 um (to 0.1 um below 0.5 um), never above the feature size."""
    step = 0.5 if value_um >= 0.5 else 0.1
    rounded = math.floor(value_um / step + 1e-9) * step
    return round(max(rounded, 0.1 if value_um >= 0.1 else value_um), 3)


def _edges(geometry):
    """(start, unit direction, length, inward normal) of every boundary edge; material on the left."""
    from shapely.geometry.polygon import orient
    parts = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
    edges = []
    for poly in parts:
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        poly = orient(poly.simplify(1e-6, preserve_topology=True), sign=1.0)   # exterior CCW, holes CW
        for ring in [poly.exterior, *poly.interiors]:
            pts = np.asarray(ring.coords)
            for p0, p1 in zip(pts[:-1], pts[1:]):
                v = p1 - p0
                length = float(np.hypot(*v))
                if length <= 0:
                    continue
                d = v / length
                edges.append((p0, d, length, np.array([-d[1], d[0]])))
    return edges


def _facing_pairs(geometry, edges, max_dist, min_overlap):
    """Antiparallel edge pairs facing each other: ('strip', width) through metal or ('gap', gap)
    through free space, with the length of their parallel overlap."""
    from shapely.geometry import LineString
    from shapely.prepared import prep
    from shapely.strtree import STRtree

    lines = [LineString([e[0], e[0] + e[1] * e[2]]) for e in edges]
    tree = STRtree(lines)
    prepared = prep(geometry)
    cos_tol = math.cos(math.radians(PARALLEL_TOL_DEG))
    pairs = []
    for i, (p0, d, length, n) in enumerate(edges):
        for j in tree.query(lines[i], predicate="dwithin", distance=max_dist):
            j = int(j)
            if j <= i:
                continue
            q0, e, qlen, m = edges[j]
            if float(np.dot(d, e)) > -cos_tol:
                continue                                  # not antiparallel
            dist = float(np.dot(q0 - p0, n))              # signed: > 0 means B lies on A's metal side
            t = sorted([float(np.dot(q0 - p0, d)), float(np.dot(q0 + e * qlen - p0, d))])
            lo, hi = max(0.0, t[0]), min(length, t[1])
            overlap = hi - lo
            if overlap < min_overlap or abs(dist) < 1e-9 or abs(dist) > max_dist:
                continue
            back = float(np.dot(p0 - q0, m))
            if dist > 0 and back > 0:
                kind = "strip"
            elif dist < 0 and back < 0:
                kind = "gap"
            else:
                continue
            # the segment between the two edges must stay in metal (strip) or in free space (gap)
            mid = p0 + d * (lo + hi) / 2
            a, b = mid + n * dist * 0.02, mid + n * dist * 0.98
            link = LineString([a, b])
            if kind == "strip" and not prepared.contains(link):
                continue
            if kind == "gap" and prepared.intersects(link):
                continue
            pairs.append((kind, abs(dist), overlap, i, j, mid, mid + n * dist))
    return pairs


def suggest_mesh_size(gds_file, stackup_file, cellname="", variable_overrides=None):
    import gdspy
    import shapely
    from shapely.geometry import Polygon

    from .layout import _select_cell

    gds_file = os.path.abspath(gds_file)
    library = gdspy.GdsLibrary(infile=gds_file)
    cell, note = _select_cell(library, cellname)
    um = library.unit / 1e-6              # layout units -> microns

    _, _, dielectrics_list, metals_list = read_stackup(stackup_file, variable_overrides)
    conductors = sorted({(m.zmax, int(m.layernum), m.name) for m in metals_list.metals if m.is_metal},
                        reverse=True)
    top, seen = [], set()
    for _, layer, name in conductors:
        if layer not in seen:
            seen.add(layer)
            top.append((layer, name))
        if len(top) == TOP_LAYERS:
            break

    by_spec = cell.get_polygons(by_spec=True)
    stackup_layers = {int(m.layernum) for m in metals_list.metals} | set(dielectrics_list.get_boundary_layers())
    points = [p for (l, d), ps in by_spec.items() if int(l) in stackup_layers and int(d) == 0 for p in ps]
    if not points:
        raise ValueError("The layout has no shapes on the stackup's layers (datatype 0).")
    allpts = np.vstack(points)
    size = float(max(allpts.max(axis=0) - allpts.min(axis=0)))     # larger bbox side, layout units
    relevant = RELEVANT_FRACTION * size

    traces, coupled, layers_report = [], [], []
    for layer, name in top:
        polys = [Polygon(p).buffer(0) for (l, d), ps in by_spec.items() if int(l) == layer and int(d) == 0
                 for p in ps if len(p) >= 3]
        if not polys:
            layers_report.append({"layer": layer, "name": name, "shapes": 0})
            continue
        geometry = shapely.unary_union(polys)
        edges = _edges(geometry)
        pairs = _facing_pairs(geometry, edges, MAX_DISTANCE_UM / um, MIN_OVERLAP_UM / um)

        # trace widths: strips through metal, grouped by width (0.01 um)
        width_of_edge = {}
        groups = {}
        for kind, dist, overlap, i, j, a, b in pairs:
            if kind != "strip":
                continue
            for k in (i, j):
                width_of_edge[k] = min(width_of_edge.get(k, dist), dist)
            key = round(dist * um, 2)
            g = groups.setdefault(key, {"length": 0.0, "longest": 0.0, "at": None})
            g["length"] += overlap
            if overlap > g["longest"]:
                g["longest"], g["at"] = overlap, (a + b) / 2
        for width_um, g in sorted(groups.items()):
            length = g["length"]
            if length >= TRACE_ASPECT * width_um / um and length >= relevant:
                traces.append({"layer": layer, "name": name, "width_um": width_um,
                               "length_um": round(length * um, 1),
                               "at": [round(float(c) * um, 2) for c in g["at"]]})

        # coupled lines: gaps between metal edges whose lines are at most COUPLING_GAP_FACTOR x wider
        for kind, dist, overlap, i, j, a, b in pairs:
            if kind != "gap" or overlap < relevant:
                continue
            widths = [width_of_edge.get(k) for k in (i, j)]
            if None in widths:
                continue
            narrower = min(widths)
            if dist <= COUPLING_GAP_FACTOR * narrower:
                coupled.append({"layer": layer, "name": name, "gap_um": round(dist * um, 3),
                                "line_widths_um": [round(w * um, 3) for w in widths],
                                "parallel_length_um": round(overlap * um, 1),
                                "at": [round(float(c) * um, 2) for c in (a + b) / 2]})
        layers_report.append({"layer": layer, "name": name, "shapes": len(polys)})

    coupled.sort(key=lambda c: (c["gap_um"], -c["parallel_length_um"]))
    traces.sort(key=lambda t: t["width_um"])
    features = [t["width_um"] for t in traces] + [c["gap_um"] for c in coupled] \
        + [w for c in coupled for w in c["line_widths_um"]]
    smallest = min(features) if features else None

    def suggestion(default):
        if smallest is None or smallest >= default:
            return default
        return round_down_cellsize(smallest)

    accurate, quick = suggestion(DEFAULT_ACCURATE_UM), suggestion(DEFAULT_QUICK_UM)

    def smallest_feature(item):
        return min([item.get("width_um", 1e9), item.get("gap_um", 1e9), *item.get("line_widths_um", [])])

    # cheaper alternative: the small cell size only on the layers that have the small features
    per_layer = {}
    for level, default, value in (("accurate", DEFAULT_ACCURATE_UM, accurate), ("quick", DEFAULT_QUICK_UM, quick)):
        if value < default:
            names = sorted({f["name"] for f in traces + coupled if smallest_feature(f) < default})
            per_layer[level] = {"refined_cellsize": default,
                                "refined_cellsize_override": [[name, value] for name in names]}

    result = {
        "gds_file": gds_file, "cell": cell.name,
        "layers_checked": layers_report,
        "layout_size_um": round(size * um, 2),
        "relevant_length_um": round(relevant * um, 2),
        "traces": traces[:20], "coupled_lines": coupled[:20],
        "smallest_width_or_gap_um": smallest,
        "suggested_refined_cellsize_um": {"accurate": accurate, "quick": quick},
        "rule": ("Suggest refined_cellsize no larger than these values, also for quick models: a cell size "
                 "above the smallest gap or trace width gave clearly wrong results or solver crashes in the "
                 "mesh convergence studies. Go larger only if the user explicitly asks for it."),
        "notes": [
            f"Checked the top {TOP_LAYERS} conductor layers of the stackup. Traces: metal strips with length "
            f">= {TRACE_ASPECT:g}x width and >= {RELEVANT_FRACTION:.0%} of the larger layout side. Coupled lines: "
            f"parallel edges across a gap <= {COUPLING_GAP_FACTOR:g}x the narrower line width, parallel for >= "
            f"{RELEVANT_FRACTION:.0%} of the larger layout side. Widths and gaps up to {MAX_DISTANCE_UM:g} um.",
            "Suggestions are rounded down to 0.5 um (0.1 um below 0.5 um). Defaults without small features: "
            f"{DEFAULT_ACCURATE_UM:g} um (accurate), {DEFAULT_QUICK_UM:g} um (quick).",
        ],
    }
    if per_layer:
        per_layer["note"] = ("Cheaper alternative to the global value: the small cell size only on the layers "
                             "with the small features (refined_cellsize_override), the default elsewhere.")
        result["per_layer_alternative"] = per_layer
    if note:
        result["notes"].insert(0, note)
    return result
