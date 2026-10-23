"""Open results in a desktop window for the user: show_in_gmsh."""

import glob
import os
import sys

from .common import open_window

# the same as the 'gmsh' command of the pip gmsh package, with the server's Python
GMSH_GUI = "import sys, gmsh; gmsh.initialize(['gmsh'] + sys.argv[1:], run=True); gmsh.finalize()"

SUFFIXES = {"mesh": ".msh", "geometry": ".geo_unrolled"}


def _model_dir(path):
    """<model>_data folder from a model script, a _data folder or a file in it."""
    path = os.path.abspath(path)
    if path.endswith(".py"):
        basename = os.path.splitext(os.path.basename(path))[0]
        return os.path.join(os.path.dirname(path), "palace_model", f"{basename}_data")
    return path if os.path.isdir(path) else os.path.dirname(path)


def _find_file(path, what):
    suffix = SUFFIXES[what]
    if os.path.isfile(path) and path.endswith(suffix):
        return os.path.abspath(path)
    model_dir = _model_dir(path)
    candidates = sorted(glob.glob(os.path.join(model_dir, "*" + suffix)))
    if candidates:
        return candidates[0]
    if what == "geometry":
        raise FileNotFoundError(
            f"No {suffix} file in {model_dir}. The unmeshed geometry is only saved with "
            "settings['save_gmsh_unrolled'] = True; run generate_model with save_geometry=True.")
    raise FileNotFoundError(f"No {suffix} file in {model_dir}. Run generate_model first.")


def show_in_gmsh(path, what="mesh"):
    if what not in SUFFIXES:
        raise ValueError(f"what must be one of {list(SUFFIXES)}")
    filename = _find_file(path, what)
    process = open_window([sys.executable, "-c", GMSH_GUI, filename], cwd=os.path.dirname(filename))

    size_mb = os.path.getsize(filename) / 1e6
    return {
        "opened": filename,
        "what": what,
        "pid": process.pid,
        "note": "gmsh opens in its own window on the user's screen and keeps running after this call. "
                + ("Large file: loading takes a moment. " if size_mb > 50 else "")
                + "This tool does not see the window; ask the user what they see.",
    }


# fill transparency for the stackup colors: conductors let the layers below show through
CONDUCTOR_ALPHA = "99"
VIA_ALPHA = "dd"
# for layers whose material color is missing (e.g. PEC) or already used by another drawn layer
FALLBACK_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2",
                   "#bcbd22", "#17becf", "#7f7f7f"]


def _style_for(stackup_file, style):
    if style not in ("auto", "sg13g2", "stackup"):
        raise ValueError("style must be 'auto', 'sg13g2' or 'stackup'.")
    if style != "auto":
        if style == "stackup" and not stackup_file:
            raise ValueError("style='stackup' needs stackup_file.")
        return style
    if not stackup_file or os.path.basename(stackup_file).upper().startswith("SG13"):
        return "sg13g2"
    return "stackup"


def _rgb(color):
    return tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))


def _distinct_color(used):
    """The fallback color farthest (RGB distance) from all colors already used."""
    def distance(color):
        return min((sum((a - b) ** 2 for a, b in zip(_rgb(color), _rgb(u))) for u in used), default=1e9)
    return max(FALLBACK_COLORS, key=distance)


def _stackup_colors(stackup_file, variable_overrides, drawn_layers):
    """{layer: color} bottom to top, outline layers and a legend, from the stackup's material colors."""
    from .common import read_stackup
    _, materials_list, dielectrics_list, metals_list = read_stackup(stackup_file, variable_overrides)
    material_color = {m.name: m.color for m in materials_list.materials if m.color}

    drawn = {}
    for m in metals_list.metals:
        if int(m.layernum) in drawn_layers:
            drawn.setdefault(int(m.layernum), m)

    # conductors choose first, so that a via with its metal's color gets a different one
    chosen, used = {}, set()
    for layer, m in sorted(drawn.items(), key=lambda item: (item[1].is_via, item[1].zmin)):
        color = material_color.get(m.material)
        color = f"#{color.lstrip('#').lower()}" if color else None
        if color is None or color in used:
            color = _distinct_color(used)
        used.add(color)
        chosen[layer] = color

    colors, outline, legend = {}, set(), []
    for layer, m in sorted(drawn.items(), key=lambda item: (item[1].zmin, item[1].zmax)):   # bottom to top
        if m.is_sheet:
            outline.add(layer)
        else:
            colors[layer] = chosen[layer] + (VIA_ALPHA if m.is_via else CONDUCTOR_ALPHA)
        legend.append({"layer": layer, "name": m.name, "type": m.type.lower(), "material": m.material,
                       "color": chosen[layer], "drawn_as": "outline" if m.is_sheet else "fill"})
    for layer in dielectrics_list.get_boundary_layers():
        if int(layer) in drawn_layers and int(layer) not in colors:
            outline.add(int(layer))
            legend.append({"layer": int(layer), "name": "dielectric boundary", "drawn_as": "outline"})
    outline |= {layer for layer in drawn_layers if layer not in colors}
    return colors, outline, legend


def preview_layout(gds_file, cellname="", ports=None, width_px=1000, save_png="", stackup_file="",
                   variable_overrides=None, style="auto", fill_alpha=0.45, pattern_scale=1):
    """Render the layout to PNG bytes with gds_viewer, ports labelled.

    style "sg13g2": IHP SG13G2 colors and fill patterns (KLayout look); layers without SG13G2
    style (ports, SUBGND, ...) are outlines. style "stackup": colors from the stackup materials,
    semi-transparent fills drawn bottom to top, for any technology; sheets, dielectric boundaries
    and layers not in the stackup are outlines. "auto": sg13g2 without a stackup or for an IHP
    stackup (file name SG13...), else stackup.
    ports: {port number: GDS layer}; each shape on that layer gets the label P<n> at its
    center. Without ports, shapes on the conventional port layers 201-249 are labelled
    L<layer>.
    fill_alpha: opacity of a solid fill under the fill patterns (sg13g2 style), so that metal
    stays visible when the image is shown small; 0 = exact KLayout look.
    pattern_scale: thickness of fill pattern lines and outlines (sg13g2 style), 1 = KLayout.
    Returns (png bytes, summary dict).
    """
    import io

    import gdspy
    import gds_viewer

    from .layout import _select_cell

    gds_file = os.path.abspath(gds_file)
    cell, note = _select_cell(gdspy.GdsLibrary(infile=gds_file), cellname)
    by_spec = cell.get_polygons(by_spec=True)
    drawn_layers = {int(layer) for (layer, _), polygons in by_spec.items() if polygons}

    if ports:
        label_layers = {int(layer): f"P{number}" for number, layer in ports.items()}
    else:
        label_layers = {layer: f"L{layer}" for layer in drawn_layers if 201 <= layer <= 249}

    labels, missing = [], []
    for layer, text in sorted(label_layers.items()):
        shapes = [p for (l, _), polygons in by_spec.items() if l == layer for p in polygons]
        if not shapes:
            missing.append(f"{text}: no shapes on layer {layer}")
        for points in shapes:
            x, y = (points.min(axis=0) + points.max(axis=0)) / 2
            labels.append((float(x), float(y), text))

    used_style = _style_for(stackup_file, style)
    legend = None
    if used_style == "stackup":
        layer_colors, outline, legend = _stackup_colors(stackup_file, variable_overrides, drawn_layers)
    else:
        layer_colors = None
        styled = {layer for layer, _ in gds_viewer.LAYER_STYLES}
        outline = {layer for layer in drawn_layers if layer not in styled}

    buffer = io.BytesIO()
    gds_viewer.render_screenshot(cell, buffer, width_px=width_px, labels=labels, outline_layers=outline,
                                 layer_colors=layer_colors, pattern_scale=max(1, int(pattern_scale)),
                                 fill_alpha=fill_alpha or None)
    png = buffer.getvalue()
    if save_png:
        save_png = os.path.abspath(save_png)
        with open(save_png, "wb") as f:
            f.write(png)

    notes = [n for n in [note] + missing if n]
    if used_style == "sg13g2" and not stackup_file:
        notes.append("Colors assume IHP SG13G2 layer numbers. For another technology, pass stackup_file: "
                     "the colors then come from the stackup materials.")

    (x0, y0), (x1, y1) = cell.get_bounding_box()
    summary = {
        "gds_file": gds_file, "cell": cell.name, "style": used_style,
        "bbox": [round(float(v), 4) for v in (x0, y0, x1, y1)],
        "labels": [{"text": t, "x": round(x, 3), "y": round(y, 3)} for x, y, t in labels],
        "outline_layers": sorted(outline),
        "saved_png": save_png or None,
        "notes": notes,
    }
    if legend is not None:
        summary["legend"] = legend
    return png, summary
