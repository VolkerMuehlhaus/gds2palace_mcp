"""Run a gds2palace model script without gmsh windows.

Usage: python -u -m gds2palace_mcp._run_model <script.py>

The script runs as __main__ with its own sys.path, so it uses whichever
gds2palace it imports (installed package or a local copy). An import hook
wraps simulation_setup.create_model() when that module is loaded: it forces
settings['no_gui'] = True (and settings['save_gmsh_unrolled'] = True if the
environment variable GDS2PALACE_MCP_SAVE_GEOMETRY is set) and prints one
marker line with the output paths for generate_model to read.
"""

import importlib.abc
import importlib.machinery
import json
import os
import runpy
import sys

TARGET = "gds2palace.util_simulation_setup"
MARKER = "GDS2PALACE_MCP_RESULT "
SAVE_GEOMETRY_ENV = "GDS2PALACE_MCP_SAVE_GEOMETRY"


def _patch(module):
    original = module.create_model

    def create_model(excite_ports, settings):
        settings["no_gui"] = True
        if os.environ.get(SAVE_GEOMETRY_ENV):
            settings["save_gmsh_unrolled"] = True
        result = original(excite_ports, settings)
        package = sys.modules.get("gds2palace")
        print(MARKER + json.dumps({
            "sim_path": os.path.abspath(settings.get("sim_path", "")),
            "model_basename": settings.get("model_basename"),
            "config_suffix": settings.get("config_suffix", ""),
            "returned": [str(r) for r in result] if isinstance(result, tuple) else str(result),
            "gds2palace_file": getattr(module, "__file__", None),
            "gds2palace_version": getattr(package, "__version__", None),
        }), flush=True)
        return result

    module.create_model = create_model


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name != TARGET:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is None or spec.loader is None:
            return spec
        exec_module = spec.loader.exec_module

        def exec_and_patch(module):
            exec_module(module)
            _patch(module)

        spec.loader.exec_module = exec_and_patch
        return spec


def main():
    script = os.path.abspath(sys.argv[1])
    sys.argv = [script] + sys.argv[2:]
    sys.path.insert(0, os.path.dirname(script))
    os.chdir(os.path.dirname(script))
    os.environ.setdefault("MPLBACKEND", "Agg")
    sys.meta_path.insert(0, _Finder())
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
