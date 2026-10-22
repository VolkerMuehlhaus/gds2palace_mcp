"""Layout complexity estimate and stackup-driven simplification (gds_prepare_for_EM)."""

import os
import time

from .common import captured_output, read_stackup
from .layout import _stackup_layer_roles

# gds2palace's conventional port/thermal layer range (setupEM's default, Preferences > Ports)
PORT_LAYER_RANGE = (201, 299)

# a layer gets a simplification hint from this share of the counted polygons on, and only with
# at least HINT_MIN_POLYGONS polygons: floating fill removal needs that many same-size pieces
# (its fill_mincount default), so fewer can't be fill it would remove
HINT_SHARE = 0.10
HINT_MIN_POLYGONS = 20


def _cell_summary(gds_file, cellname):
    from gds_simplify_for_em import open_gds_cell
    library, cell = open_gds_cell(gds_file, cellname)
    (x0, y0), (x1, y1) = cell.get_bounding_box()
    tops = [c.name for c in library.top_level()]
    return cell.name, tops, (float(x0), float(y0), float(x1), float(y1))


def check_layout_complexity(gds_file, stackup_file, cellname="", purposelist=(0,), variable_overrides=None):
    from gds_simplify_for_em import estimate_polygon_count_by_layer

    gds_file = os.path.abspath(gds_file)
    start = time.perf_counter()
    stackup_path, roles = _stackup_layer_roles(stackup_file, variable_overrides)
    counts = estimate_polygon_count_by_layer(gds_file, cellname, list(purposelist) or None)
    cell, tops, (x0, y0, x1, y1) = _cell_summary(gds_file, cellname)

    lo, hi = PORT_LAYER_RANGE
    read = {layer: n for layer, n in counts.items() if layer in roles}
    ports = {layer: n for layer, n in counts.items() if layer not in roles and lo <= layer <= hi}
    ignored = {layer: n for layer, n in counts.items() if layer not in roles and not lo <= layer <= hi}
    total = sum(read.values())
    width, height = x1 - x0, y1 - y0
    area_mm2 = width * height * 1e-6

    layers = []
    hints = []
    for layer, n in sorted(read.items(), key=lambda item: -item[1]):
        share = n / total if total else 0.0
        role = roles[layer]
        layers.append({"layer": layer, "polygons": n, "share": round(share, 3), "stackup_role": role})
        if share >= HINT_SHARE and n >= HINT_MIN_POLYGONS:
            if any(r.startswith("via ") for r in role):
                hints.append(f"Layer {layer} ({', '.join(role)}) has {share:.0%} of the polygons: probably via arrays. "
                             "simplify_layout with merge_vias, or merge_polygon_size in the model script, reduces them.")
            elif any(r.startswith(("conductor ", "sheet ")) for r in role):
                hints.append(f"Layer {layer} ({', '.join(role)}) has {share:.0%} of the polygons: if this is dummy "
                             "fill, simplify_layout with remove_floating_fill removes the unconnected pieces.")

    return {
        "gds_file": gds_file, "cell": cell, "top_level_cells": tops, "stackup_file": stackup_path,
        "size": {"width": round(width, 3), "height": round(height, 3), "area_mm2": round(area_mm2, 4),
                 "unit": "GDSII user unit (microns for IHP layouts)"},
        "polygons_read_by_gds2palace": total,
        "polygons_per_mm2": round(total / area_mm2, 1) if area_mm2 > 0 else None,
        "port_layer_polygons": dict(sorted(ports.items())),
        "ignored_layer_polygons": sum(ignored.values()),
        "layers": layers,
        "hints": hints,
        "seconds": round(time.perf_counter() - start, 2),
        "notes": [
            "Polygon counts come from the cell hierarchy without flattening (arrays counted as columns x rows), "
            f"datatypes {list(purposelist)}; only layers of the stackup count as read by gds2palace.",
            "This is a report, not a pass/fail check: there is no measured polygon limit for FEM. Mesh size and "
            "run time depend on the smallest features and the mesh settings too; generate_model gives the "
            "actual mesh size. (setupEM warns above 50,000 polygons, but only because its layout preview "
            "gets slow.)",
        ],
    }


def simplify_layout(gds_file, stackup_file, output_file="", cellname="", remove_floating_fill=False,
                    fill_cutouts=False, merge_vias=False, via_merge_size=1.0, merge_per_layer=False,
                    fill_minsize=1.0, fill_maxsize=None, fill_mincount=20, max_hole_area=None,
                    exclude_layers=None, variable_overrides=None, fill_ground_holes=False,
                    ground_hole_mincount=20):
    from gds_simplify_for_em import estimate_polygon_count_by_layer, layers_from_stackup, run_simplify

    if not (remove_floating_fill or fill_cutouts or merge_vias or merge_per_layer or fill_ground_holes):
        raise ValueError("Choose at least one operation: remove_floating_fill, fill_ground_holes, merge_vias, "
                         "merge_per_layer (or fill_cutouts with an explicit max_hole_area).")
    if fill_cutouts and max_hole_area is None:
        raise ValueError("fill_cutouts needs an explicit max_hole_area (total hole area per polygon, um2) "
                         "from the user. For repeated openings in ground planes, measured in the layout, use "
                         "fill_ground_holes (see measure_holes).")
    if merge_vias and not via_merge_size > 0:
        raise ValueError("via_merge_size must be > 0.")

    gds_file = os.path.abspath(gds_file)
    if not os.path.isfile(gds_file):
        raise FileNotFoundError(gds_file)
    if not output_file:
        base, ext = os.path.splitext(gds_file)
        output_file = f"{base}_simplified{ext or '.gds'}"
    output_file = os.path.abspath(output_file)
    if os.path.normcase(output_file) == os.path.normcase(gds_file):
        raise ValueError("output_file must differ from gds_file: the original layout is never overwritten.")

    hole_note = None
    if fill_ground_holes:
        from .holes import measure_holes
        holes = measure_holes(gds_file, stackup_file, cellname, ground_hole_mincount, variable_overrides)
        hole_note = "fill_ground_holes: " + holes["suggestion"]["reason"]

    _, _, _, metals_list = read_stackup(stackup_file, variable_overrides)
    excluded = {int(n) for n in exclude_layers or []}
    metal_layers, via_layers = layers_from_stackup(metals_list, PORT_LAYER_RANGE)
    metal_layers = [n for n in metal_layers if n not in excluded]
    via_layers = [v for v in via_layers if v[0] not in excluded]

    before = estimate_polygon_count_by_layer(gds_file, cellname, [0])
    design_name = _cell_summary(gds_file, cellname)[0]
    start = time.perf_counter()
    with captured_output() as log:
        run_simplify(gds_file, metal_layers, output_file, cellname=cellname,
                     do_cutouts=fill_cutouts, max_hole_area=max_hole_area,
                     do_floating=remove_floating_fill, fill_minsize=fill_minsize, fill_maxsize=fill_maxsize,
                     fill_mincount=fill_mincount, do_merge=merge_per_layer,
                     via_layers=via_layers if merge_vias else None, via_merge_size=via_merge_size if merge_vias else 0,
                     fill_ground_holes=fill_ground_holes, ground_hole_mincount=ground_hole_mincount)
    seconds = time.perf_counter() - start

    # the output can have several top-level cells (subcells left over after flattening), and merging
    # renames the design cell to <name>_merged_by_layer...: find the design by its name, else by area
    from gds_simplify_for_em import open_gds_cell
    library, _ = open_gds_cell(output_file, "")
    tops = library.top_level()

    def area(c):
        (x0, y0), (x1, y1) = c.get_bounding_box()
        return (x1 - x0) * (y1 - y0)

    main = (library.cells.get(design_name)
            or next((c for c in sorted(tops, key=lambda c: len(c.name)) if c.name.startswith(design_name)), None)
            or max(tops, key=lambda c: (area(c), c.name)))
    after = estimate_polygon_count_by_layer(output_file, main.name, [0])

    layers = []
    for layer in sorted(set(before) | set(after)):
        if before.get(layer, 0) != after.get(layer, 0):
            layers.append({"layer": layer, "before": before.get(layer, 0), "after": after.get(layer, 0)})
    log_lines = [line for line in log.getvalue().splitlines() if line.strip()]
    result = {
        "output_file": output_file, "cell": main.name, "top_level_cells": [c.name for c in tops],
        "polygons_before": sum(before.values()), "polygons_after": sum(after.values()),
        "changed_layers": layers, "seconds": round(seconds, 1),
        "metal_layers_processed": metal_layers,
        "via_layers_merged": [v[0] for v in via_layers] if merge_vias else [],
        "log_summary": [line for line in log_lines if not line.lstrip().startswith(("repairing", "replacing"))
                        and " : repairing " not in line and " replacing " not in line][-30:],
        "notes": ["The original file is unchanged. Use output_file in the model script (gds_filename)."],
    }
    if hole_note:
        result["notes"].append(hole_note)
    if len(tops) > 1:
        result["notes"].append(f"The output has {len(tops)} top-level cells. gds2palace reads the first top-level "
                               f"cell unless cellname is set: set cellname = '{main.name}' in the model script.")
    return result
