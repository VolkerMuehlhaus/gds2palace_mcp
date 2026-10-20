"""gds2palace MCP server (stdio)."""

import functools
import os
import urllib.request

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError

from . import __version__
from .common import Gds2palaceError
from .layout import inspect_gds as _inspect_gds
from .model_run import generate_model as _generate_model
from .settings_check import check_model_script as _check_model_script
from .stackup import inspect_stackup as _inspect_stackup, list_stackups as _list_stackups
from .viewers import show_in_gmsh as _show_in_gmsh

AGENTS_URL = "https://raw.githubusercontent.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/main/AGENTS.md"

INSTRUCTIONS = """\
Tools for building AWS Palace (and Elmer) EM simulation models with gds2palace from a GDSII
layout and an XML stackup file.

Workflow: read the resource gds2palace://agents-guide first (how model scripts are written,
settings, ports, pitfalls). Inspect the inputs with list_stackups, inspect_stackup and
inspect_gds. Write the model script, starting from the template in the guide. Run
check_model_script, fix what it reports, then generate_model. show_in_gmsh opens the model in a
gmsh window for the user. The Palace solver runs on Linux and is not started by these tools.

Ask the user for design intent (frequency range, ports, stackup, accuracy versus run time);
don't invent it. All paths are on the machine where this server runs.
"""

mcp = MCPServer("gds2palace", version=__version__, instructions=INSTRUCTIONS)


def expected_errors(function):
    """Report bad input (missing file, unreadable stackup, ...) to the client with its message.
    Other exceptions stay crashes, which the SDK reports without details."""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, SyntaxError, KeyError, Gds2palaceError) as e:
            raise ToolError(f"{type(e).__name__}: {e}") from e
    return wrapper


@mcp.tool()
@expected_errors
def list_stackups() -> dict:
    """List the IHP stackup XML files that ship with gds2palace (SG13G2, SG13CMOS5L), with their
    paths and descriptions. A model can use one of these names or a path to its own stackup file."""
    return _list_stackups()


@mcp.tool()
@expected_errors
def inspect_stackup(stackup_file: str, variable_overrides: dict | None = None) -> dict:
    """Summarize a gds2palace XML stackup: variables, materials, dielectrics, conductor/via/sheet
    layers with GDS layer numbers and z positions (bottom to top), derived layers, and free GDS
    layer numbers for ports.

    stackup_file: path, or the name of a bundled stackup such as 'SG13G2_FEM_200um.xml'.
    variable_overrides: e.g. {"total_thickness": 100}, the same as read_substrate() takes."""
    return _inspect_stackup(stackup_file, variable_overrides)


@mcp.tool()
@expected_errors
def inspect_gds(gds_file: str, cellname: str = "", stackup_file: str = "",
                variable_overrides: dict | None = None) -> dict:
    """Summarize a GDSII layout per layer/datatype: shape count, extent, smallest shape side and
    smallest gap (for choosing refined_cellsize and merge_polygon_size). The cell is chosen like
    gds2palace's read_gds(): cellname, else the first top-level cell.

    With stackup_file, each layer also shows its role in the stackup (conductor, via, derived
    layer operand, ...) or that gds2palace ignores it, and stackup layers without shapes are listed."""
    return _inspect_gds(gds_file, cellname, stackup_file, variable_overrides)


@mcp.tool()
@expected_errors
def check_model_script(script_path: str) -> dict:
    """Check a gds2palace model script without running it: settings keys that gds2palace doesn't
    read (gds2palace silently ignores typos such as 'nogui'), missing required settings, port
    layers not added to the layer list, removed or legacy settings. Run it before generate_model."""
    return _check_model_script(script_path)


@mcp.tool()
@expected_errors
def generate_model(script_path: str, timeout_s: int = 1800, save_geometry: bool = False) -> dict:
    """Run a gds2palace model script (python model.py) without gmsh windows and check its output:
    config.json, mesh, port_information.json and run_sim in palace_model/<model>_data, with mesh
    node/element counts. Meshing large layouts can take minutes. Does not run the Palace solver.

    save_geometry: also save the unmeshed geometry (<model>.geo_unrolled) for show_in_gmsh."""
    return _generate_model(script_path, timeout_s, save_geometry)


@mcp.tool()
@expected_errors
def show_in_gmsh(path: str, what: str = "mesh") -> dict:
    """Open the model in the gmsh GUI on the user's screen, so that they can look at it. Returns at
    once; the window stays open. You (the assistant) don't see the window: ask the user.

    path: the model script, its palace_model/<model>_data folder, or a .msh / .geo_unrolled file.
    what: "mesh" (the meshed model) or "geometry" (the unmeshed geometry, which needs
    generate_model with save_geometry=True first)."""
    return _show_in_gmsh(path, what)


def _agents_guide():
    import gds2palace
    local = os.path.join(os.path.dirname(gds2palace.__file__), "..", "..", "AGENTS.md")
    if os.path.isfile(local):
        with open(local, encoding="utf-8") as f:
            return f.read()
    try:
        response = urllib.request.urlopen(AGENTS_URL, timeout=20)
    except OSError as e:
        raise ResourceError(f"AGENTS.md is not in this gds2palace installation, and {AGENTS_URL} "
                            f"could not be read: {e}") from e
    with response:
        return (f"<!-- gds2palace is installed without the repository; this is AGENTS.md from {AGENTS_URL}. "
                f"Paths in it are relative to that repository. -->\n" + response.read().decode("utf-8"))


@mcp.resource("gds2palace://agents-guide", name="gds2palace agent guide", mime_type="text/markdown",
              description="AGENTS.md: workflow, model script template, settings, ports, common mistakes.")
def agents_guide() -> str:
    return _agents_guide()


@mcp.resource("gds2palace://stackups/{filename}", name="bundled stackup file", mime_type="text/plain",
              description="A bundled stackup file (XML) or its README.md, e.g. gds2palace://stackups/README.md")
def stackup_file(filename: str) -> str:
    from gds2palace import utilities
    stackup_dir = utilities.get_stackup_dir()
    path = os.path.join(stackup_dir or "", os.path.basename(filename))
    if stackup_dir is None or not os.path.isfile(path):
        raise ResourceNotFoundError(f"{filename} is not a bundled stackup file, see list_stackups.")
    with open(path, encoding="utf-8") as f:
        return f.read()


@mcp.prompt()
def new_model(gds_file: str = "", what: str = "") -> str:
    """Start a new gds2palace EM model from a layout."""
    return f"""Help me build a gds2palace model for AWS Palace.
Layout: {gds_file or '(ask me)'}. Structure: {what or '(ask me)'}.

1. Read gds2palace://agents-guide.
2. Run inspect_gds on the layout (with the stackup, once chosen) and list_stackups.
3. Before writing the script, ask me for: frequency range and points of interest; which GDS
   layers hold the ports, port type (via or in-plane), the metals they connect, polarity and
   reference impedance; which stackup (planar or conformal passivation, chip thickness);
   accuracy versus run time. Suggest values from the layout, but let me decide.
4. Write the script from the guide's template, run check_model_script, fix, then generate_model.
5. Report the mesh size and anything in important_lines, and how to run Palace (run_sim on Linux)."""


def main():
    mcp.run("stdio")


if __name__ == "__main__":
    main()
