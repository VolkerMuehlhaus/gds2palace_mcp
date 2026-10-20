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
