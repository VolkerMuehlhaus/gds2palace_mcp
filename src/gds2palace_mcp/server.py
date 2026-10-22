"""gds2palace MCP server (stdio)."""

import functools
import json
import os
import urllib.request

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError, ToolError
from mcp.server.mcpserver.utilities.types import Image

from . import __version__
from .common import Gds2palaceError
from .layout import inspect_gds as _inspect_gds
from .model_run import generate_model as _generate_model
from .settings_check import check_model_script as _check_model_script
from . import complexity, meshsize, results, sparams
from .stackup import inspect_stackup as _inspect_stackup, list_stackups as _list_stackups
from .viewers import preview_layout as _preview_layout, show_in_gmsh as _show_in_gmsh

AGENTS_URL = "https://raw.githubusercontent.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/main/AGENTS.md"

INSTRUCTIONS = """\
Tools for building AWS Palace (and Elmer) EM simulation models with gds2palace from a GDSII
layout and an XML stackup file.

Workflow: read the resource gds2palace://agents-guide first (how model scripts are written,
settings, ports, pitfalls). Inspect the inputs with list_stackups, inspect_stackup and
inspect_gds; for unknown or tape-out layouts, check_layout_complexity and, if needed,
simplify_layout. Before asking the user about mesh settings, run suggest_mesh_size and propose
its refined_cellsize: never a larger one, also not for quick models, unless the user explicitly
asks for it. Write the model script, starting from the template in the guide. Run
check_model_script, fix what it reports, then generate_model. preview_layout shows you the layout
as an image with ports labelled; show_in_gmsh opens the model in a gmsh window for the user.
For finished Palace runs, summarize_results gives run time, RAM, unknowns and AMR convergence.
For results (.sNp files): sparameter_summary and passive_values give numbers, plot_sparameters
gives you plots, show_sparameters opens plot windows for the user. The Palace solver runs on Linux and is not started by these tools.

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
def check_layout_complexity(gds_file: str, stackup_file: str, cellname: str = "",
                            variable_overrides: dict | None = None) -> dict:
    """Estimate how complex a layout is for FEM before meshing: polygon count per layer (only layers
    the stackup uses, from the cell hierarchy without flattening, so it is fast even for dense fill
    and via arrays), layout size, polygon density, which layers dominate, and hints for
    simplify_layout. A report, not a pass/fail verdict: there is no measured polygon limit.
    Run it on unknown or tape-out layouts before writing the model script."""
    return complexity.check_layout_complexity(gds_file, stackup_file, cellname,
                                              variable_overrides=variable_overrides)


@mcp.tool()
@expected_errors
def simplify_layout(gds_file: str, stackup_file: str, output_file: str = "", cellname: str = "",
                    remove_floating_fill: bool = False, fill_cutouts: bool = False, merge_vias: bool = False,
                    via_merge_size: float = 1.0, merge_per_layer: bool = False, fill_minsize: float = 1.0,
                    fill_maxsize: float | None = None, fill_mincount: int = 20,
                    max_hole_area: float | None = 1.0, exclude_layers: list[int] | None = None,
                    variable_overrides: dict | None = None) -> dict:
    """Simplify a layout for EM simulation and write it to a new GDS file (default
    <name>_simplified.gds; the original is never changed). Same operations as setupEM's
    Tools > Simplify GDS (gds_prepare_for_EM), with metal and via layers from the stackup:

    remove_floating_fill: remove unconnected dummy fill (groups of at least fill_mincount pieces of
      the same size, between fill_minsize and fill_maxsize um).
    fill_cutouts: fill small cutouts (holes) in metal, up to max_hole_area um^2 (default 1, as in
      setupEM). None fills holes of any size, including openings that belong to the circuit, e.g.
      inside the rings of a coupler: only with the user's explicit OK.
    merge_vias: merge vias closer than via_merge_size um that connect the same metal shapes above
      and below (never shorts different shapes).
    merge_per_layer: merge touching polygons on each metal layer.
    exclude_layers: GDS layers to leave unchanged.

    These change the geometry: ask the user before removing fill or filling cutouts, since both can
    change the result (e.g. fill near a coil, slots in a ground plane). Returns polygon counts per
    changed layer before/after, the cell to use, and the log summary."""
    return complexity.simplify_layout(gds_file, stackup_file, output_file, cellname, remove_floating_fill,
                                      fill_cutouts, merge_vias, via_merge_size, merge_per_layer,
                                      fill_minsize, fill_maxsize, fill_mincount, max_hole_area,
                                      exclude_layers, variable_overrides)


@mcp.tool()
@expected_errors
def suggest_mesh_size(gds_file: str, stackup_file: str, cellname: str = "",
                      variable_overrides: dict | None = None) -> dict:
    """Suggest refined_cellsize from the layout, BEFORE asking the user about mesh settings.
    Looks at the top 2 conductor layers of the stackup that have shapes in the layout for traces (length >= 10x width and >= 20 %
    of the larger layout side) and coupled lines (gap <= 2x the narrower line width, parallel for
    >= 20 % of the larger layout side), and suggests refined_cellsize for accurate (default 2 um) and
    quick (default 5 um) models: the smallest flagged width or gap, rounded down to 0.5 um, if that
    is smaller than the default. The 2/5 um defaults are IHP chip values (SG13... stackups); for
    other technologies (e.g. PCB) the suggestion is the smallest flagged width or gap itself, and
    without flagged features there is none: then ask the user. Also gives a per-layer alternative
    (refined_cellsize_override).

    Never propose a larger refined_cellsize than suggested, also not for quick models; go larger
    only if the user explicitly asks for it."""
    return meshsize.suggest_mesh_size(gds_file, stackup_file, cellname, variable_overrides)


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


@mcp.tool()
@expected_errors
def preview_layout(gds_file: str, cellname: str = "", ports: dict[int, int] | None = None,
                   width_px: int = 1000, send_image: bool = True, save_png: str = "",
                   stackup_file: str = "", variable_overrides: dict | None = None,
                   style: str = "auto", fill_alpha: float = 0.45, pattern_scale: int = 1) -> list:
    """Render the layout as a PNG with ports labelled, so that you can see it: check port
    positions against the feed lines, see the structure type.

    Colors: for IHP SG13G2 (no stackup_file, or an SG13... stackup) the KLayout colors and fill
    patterns. For any other technology pass stackup_file: colors then come from the stackup
    materials, semi-transparent and drawn bottom to top, and the result has a legend (layer, name,
    color). style forces "sg13g2" or "stackup". Without a stackup, IHP layer numbers are assumed:
    other technologies get misleading colors.

    The image is sent to the assistant, i.e. a picture of the user's design leaves their machine.
    For confidential layouts, use send_image=False with save_png: the PNG is then only saved
    locally for the user to open.

    ports: {port number: GDS layer}, e.g. {1: 201, 2: 202}; each shape on that layer is labelled
    P<n>. Without ports, shapes on layers 201-249 are labelled L<layer>. Layers without SG13G2
    style (ports, SUBGND, ...) are drawn as outlines.
    width_px: image width; 1000 is enough for an overview, larger for small details.
    fill_alpha: opacity of a solid fill under the SG13G2 fill patterns, so that metal stays visible
    when the image is shown small; 0 for the exact KLayout look.
    pattern_scale: thicker pattern lines and outlines (2, 3, ...); 1 = KLayout.
    save_png: optional path to also save the PNG."""
    if not send_image and not save_png:
        raise ValueError("send_image=False needs save_png, otherwise nothing is produced.")
    png, summary = _preview_layout(gds_file, cellname, ports, width_px, save_png, stackup_file,
                                   variable_overrides, style, fill_alpha, pattern_scale)
    text = json.dumps(summary, indent=1)
    return [Image(data=png, format="png"), text] if send_image else [text]


@mcp.tool()
@expected_errors
def summarize_results(path: str, plot: bool = False) -> list:
    """Summary of finished (or running) Palace simulations, with gds2palace's palace_summary:
    unknowns (degrees of freedom), mesh elements, run time, peak RAM, error indicator, and for
    adaptive mesh refinement (AMR) the change of S between iterations (Max |dS|). Also lists the
    Touchstone files found, and counts 'did NOT converge' in log files saved in the run folder.

    path: the model script, its palace_model/<model>_data folder (after the results were copied
    back from the Linux machine), or a folder with several runs (searched recursively).
    plot: also return the AMR convergence plot (Max |dS| per iteration) as an image.
    Needs gds2palace 0.9.2 or newer."""
    summary, images = results.summarize_results(path, plot)
    return [json.dumps(summary, indent=1)] + [Image(data=png, format="png") for png in images]


@mcp.tool()
@expected_errors
def sparameter_summary(files: list[str], parameters: list[str] | None = None,
                       frequencies_ghz: list[float] | None = None) -> dict:
    """Numbers from Touchstone files (.s1p, .s2p, ... from Palace/combine_snp, other simulators or
    a VNA): ports, frequency range, reference impedances, passivity and reciprocity checks, and per
    S-parameter the dB minimum/maximum with frequency, plus dB/phase at the given frequencies.

    parameters: e.g. ["S11", "S21"]; default all Smn with m >= n (up to 4 ports).
    Prefer this over plots when you need exact values."""
    return sparams.sparameter_summary(files, parameters, frequencies_ghz)


@mcp.tool()
@expected_errors
def plot_sparameters(files: list[str], parameters: list[str] | None = None, smith: bool = False,
                     zoom: bool = False, width_px: int = 1000, send_image: bool = True,
                     save_png: str = "") -> list:
    """Plot S-parameters as images for you to look at, with plot_snp: dB and phase per parameter,
    one curve per file (e.g. simulated vs measured), optionally Smith charts of the reflection
    parameters (smith; zoom = |Gamma| <= 0.5).

    The images are sent to the assistant. send_image=False with save_png only saves them locally.
    parameters: default ["S11", "S21"]. For a window on the user's screen, use show_sparameters."""
    if not send_image and not save_png:
        raise ValueError("send_image=False needs save_png, otherwise nothing is produced.")
    images, summary = sparams.plot_sparameters(files, parameters, smith, zoom, width_px, save_png)
    text = json.dumps(summary, indent=1)
    return ([Image(data=png, format="png") for png in images] + [text]) if send_image else [text]


@mcp.tool()
@expected_errors
def show_sparameters(files: list[str], parameters: list[str] | None = None, smith: bool = False,
                     zoom: bool = False) -> dict:
    """Open the interactive plot_snp window(s) on the user's screen (zoom, pan, save). Returns at
    once. You don't see the windows; ask the user. parameters: default S11."""
    return sparams.show_sparameters(files, parameters, smith, zoom)


@mcp.tool()
@expected_errors
def passive_values(file: str, device: str = "inductor", connection: str = "differential",
                   frequencies_ghz: list[float] | None = None, plot: bool = False) -> list:
    """L (or C), R and Q of an inductor or capacitor from a 1-port or 2-port Touchstone file:
    low-frequency value, peak Q and its frequency, self-resonance frequency (SRF), and values at the
    given frequencies.

    device: "inductor" or "capacitor".
    connection (2-port files): "differential" (Z11-Z12-Z21+Z22, port to port, floating),
    "single_ended" (1/Y11, port 2 grounded) or "series" (-2/(Y12+Y21), pi-model series branch).
    Ask the user which one matches how the device is used if it is not clear.
    plot: also return an image of L/C and Q versus frequency (sent to the assistant)."""
    result, images = sparams.passive_values(file, device, connection, frequencies_ghz, plot)
    return [json.dumps(result, indent=1)] + [Image(data=png, format="png") for png in images]


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
2. Run inspect_gds on the layout (with the stackup, once chosen) and list_stackups. For a large or
   tape-out layout, run check_layout_complexity and suggest simplify_layout where it helps.
3. Run suggest_mesh_size (layout + stackup) before asking about the mesh, and propose its
   refined_cellsize for accurate and quick models; never larger unless I explicitly ask.
4. Before writing the script, ask me for: frequency range and points of interest; which GDS
   layers hold the ports, port type (via or in-plane), the metals they connect, polarity and
   reference impedance; which stackup (planar or conformal passivation, chip thickness);
   accuracy versus run time. Suggest values from the layout, but let me decide.
5. Write the script from the guide's template, run check_model_script, fix, then generate_model.
6. Report the mesh size and anything in important_lines, and how to run Palace (run_sim on Linux)."""


def main():
    mcp.run("stdio")


if __name__ == "__main__":
    main()
