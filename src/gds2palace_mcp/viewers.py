"""Open results in a desktop window for the user: show_in_gmsh."""

import glob
import os
import subprocess
import sys

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


def _has_display():
    return sys.platform in ("win32", "darwin") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def show_in_gmsh(path, what="mesh"):
    if what not in SUFFIXES:
        raise ValueError(f"what must be one of {list(SUFFIXES)}")
    filename = _find_file(path, what)
    if not _has_display():
        raise OSError("No display (DISPLAY is not set): gmsh can't open a window on this computer.")

    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "cwd": os.path.dirname(filename)}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    process = subprocess.Popen([sys.executable, "-c", GMSH_GUI, filename], **options)

    size_mb = os.path.getsize(filename) / 1e6
    return {
        "opened": filename,
        "what": what,
        "pid": process.pid,
        "note": "gmsh opens in its own window on the user's screen and keeps running after this call. "
                + ("Large file: loading takes a moment. " if size_mb > 50 else "")
                + "This tool does not see the window; ask the user what they see.",
    }


def preview_layout(gds_file, cellname="", ports=None, width_px=1000, save_png=""):
    """Render the layout to PNG bytes with gds_viewer (IHP SG13G2 colors), ports labelled.

    ports: {port number: GDS layer}; each shape on that layer gets the label P<n> at its
    center. Without ports, shapes on the conventional port layers 201-249 are labelled
    L<layer>. Layers without SG13G2 style (ports, SUBGND, ...) are drawn as outlines.
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

    styled = {layer for layer, _ in gds_viewer.LAYER_STYLES}
    outline = {layer for layer in drawn_layers if layer not in styled}

    buffer = io.BytesIO()
    gds_viewer.render_screenshot(cell, buffer, width_px=width_px, labels=labels, outline_layers=outline)
    png = buffer.getvalue()
    if save_png:
        save_png = os.path.abspath(save_png)
        with open(save_png, "wb") as f:
            f.write(png)

    (x0, y0), (x1, y1) = cell.get_bounding_box()
    summary = {
        "gds_file": gds_file, "cell": cell.name,
        "bbox": [round(float(v), 4) for v in (x0, y0, x1, y1)],
        "labels": [{"text": t, "x": round(x, 3), "y": round(y, 3)} for x, y, t in labels],
        "outline_layers": sorted(outline),
        "saved_png": save_png or None,
        "notes": [n for n in [note] + missing if n],
    }
    return png, summary
