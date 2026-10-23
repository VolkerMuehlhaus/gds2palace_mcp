# gds2palace_mcp

![gds2palace_mcp: agent assisted RFIC EM simulation](docs/png/gds2palace_mcp_banner.png)

An [MCP](https://modelcontextprotocol.io) server for
[gds2palace](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2). It lets an AI assistant (Claude Code,
Claude Desktop, or any other MCP client) inspect GDSII layouts and stackup files, check model scripts, and generate
AWS Palace models on your computer.

The server runs locally, in the same Python environment as gds2palace, and works with the files on your disk. It
does not run the Palace solver: Palace runs on Linux, as before (`run_sim`, see the gds2palace documentation).

## Installation

```
pip install gds2palace_mcp
```

This also installs gds2palace and its requirements (gdspy, gmsh, shapely, numpy), gds_prepare_for_EM, gds_viewer,
plot_snp and scikit-rf. It needs gds2palace 0.9.2 or newer, which ships the IHP stackup files and the result tools.

Add the server to Claude Code, using the Python environment where it is installed:

```
claude mcp add gds2palace -- python -m gds2palace_mcp.server
```

Or, without installing anything first (needs [uv](https://docs.astral.sh/uv/)):

```
claude mcp add gds2palace -- uvx gds2palace_mcp
```

For Claude Desktop and other clients, add the same command to the client's MCP server configuration.

## What it offers

| Tool | What it does |
|---|---|
| `list_stackups` | The IHP stackup files that ship with gds2palace (SG13G2, SG13G2 with conformal passivation, SG13CMOS5L) |
| `inspect_stackup` | Variables, materials, dielectrics, layers with GDS numbers and z positions, derived layers, free port layer numbers. Accepts variable overrides such as `{"total_thickness": 100}` |
| `inspect_gds` | Per layer: number of shapes, extent, smallest shape and smallest gap (for choosing the mesh size and via merging). With a stackup, the role of each layer, and which layers gds2palace ignores |
| `check_layout_complexity` | Before meshing: polygon count per layer (only layers the stackup uses, without flattening the layout, so it is fast for dense fill and via arrays), layout size, polygon density, the layers that dominate, and simplification hints. A report, no pass/fail limit |
| `measure_holes` | Before ground plane openings are filled: per metal layer the repeated opening sizes in ground planes (cheesing, tiled meshes; holes inside tiles and gaps between tiles alike, measured on the merged layer) and the other openings, which stay |
| `simplify_layout` | Writes a simplified copy of the layout, like setupEM's Tools > Simplify GDS: remove floating dummy fill, fill the repeated openings of ground planes (measured in the layout, no size threshold), merge via arrays, merge each layer. Layers come from the stackup; returns polygon counts before and after |
| `suggest_mesh_size` | Before the mesh settings are discussed: traces and coupled lines on the top 2 conductor layers with shapes, and the `refined_cellsize` that follows from their smallest width or gap (IHP: accurate 2 µm, quick 5 µm by default; other technologies: the smallest feature itself; never larger than the smallest feature), also as a per-layer override |
| `propose_ports` | For a layout without port shapes: port candidates from pins, labels (integer labels give the port numbers) or open trace ends, each with a reference ground (closest solid ground metal below, else SUBGND) |
| `add_ports` | Writes a copy of the layout with the confirmed port lines on layers 201 and up, plus the `add_port` lines for the script |
| `check_ports` | Checks the ports against the layout before meshing: via port lines on metal of both layers, in-plane ports touching metal at both ends, layer collisions, several shapes on one port layer |
| `check_repeated_patterns` | Before meshing: repeated shapes found from the cell hierarchy without flattening (via fields, fill, tiles with holes placed as arrays), with how often each repeats, whether tiles touch, and which `simplify_layout` option would reduce them. Recommends preprocessing from 1000 repeated polygons on, so that gmsh doesn't run for hours |
| `check_model_script` | Checks a model script without running it: settings that gds2palace doesn't read (typos such as `nogui` are silently ignored by gds2palace), missing required settings, port layers missing from the layer list, removed or legacy settings, a `refined_cellsize` larger than the layout's smallest trace width or coupled-line gap, and the port check |
| `generate_model` | Runs the model script without gmsh windows and checks the output: `config.json`, mesh with node and element counts, `port_information.json`, `run_sim`. Estimates the Palace unknowns and peak RAM from the mesh (calibrated on 158 Palace runs). With `save_geometry`, it also saves the unmeshed geometry. The output goes to `<script>_generate.log`, shown live in a terminal window on the user's screen. After 5 minutes it returns with `still_running`, and the assistant asks whether to keep waiting; the server never stops a run |
| `wait_for_model` | Keeps waiting for a model generation that is still running |
| `show_in_gmsh` | Opens the mesh, or the unmeshed geometry, in the gmsh GUI on the user's screen |
| `preview_layout` | The layout as an image (via [gds_viewer](https://github.com/VolkerMuehlhaus/gds_viewer)) with the ports labelled, for the assistant to look at. IHP SG13G2 colors and patterns; for other technologies, colors from the stackup materials, with a legend |
| `summarize_results` | Summary of finished Palace runs (gds2palace's palace_summary): unknowns, mesh elements, run time, peak RAM, AMR convergence (Max \|ΔS\| per iteration, optional plot), Touchstone files found, "did NOT converge" in saved logs |
| `sparameter_summary` | Numbers from Touchstone files: ports, frequency range, passivity and reciprocity, dB minimum/maximum, dB/phase at chosen frequencies |
| `plot_sparameters` | dB/phase plots and Smith charts as images for the assistant (via [plot_snp](https://github.com/VolkerMuehlhaus/plot_snp)), several files in one plot |
| `show_sparameters` | Opens the interactive plot_snp windows on the user's screen |
| `fit_equivalent_circuit` | SPICE equivalent circuit (Ngspice/VACASK) from a Touchstone file with [snp2le](https://github.com/iic-jku/snp2le) in command-line mode: inductor, MIM capacitor, line, Wilkinson, balun, branch-line structures or a universal vector fit. Needs `pip install snp2le` |
| `passive_values` | L or C, R and Q of an inductor or capacitor (differential, single-ended or series), peak Q, self-resonance frequency, optional L/Q plot |

Resources: `gds2palace://agents-guide` (gds2palace's [AGENTS.md](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2/blob/main/AGENTS.md):
workflow, script template, settings, pitfalls) and `gds2palace://stackups/<file>` (the bundled stackup files and their
README).

Prompt: `new_model` starts a new model: it inspects the layout and asks you for the design decisions (frequency
range, ports, stackup, accuracy) before writing the script.

New to MCP? [docs/README.md](https://github.com/VolkerMuehlhaus/gds2palace_mcp/blob/main/docs/README.md) explains what this can and can't do for an RFIC designer, and
[docs/example_session.md](https://github.com/VolkerMuehlhaus/gds2palace_mcp/blob/main/docs/example_session.md) shows a full session with an inductor.

## Tests

```
pip install -e ".[test]"
pytest                 # fast: all tools on small layouts generated in the tests (about 6 s; 30 s with the example tests)
pytest -m slow         # gmsh meshing and the stdio MCP protocol (about 10 s)
```

The layouts, stackup and Touchstone files of the tests are generated in the tests, with known answers (a line over
a ground plane, coupled lines, a tiled ground plane with openings, series R-L-C networks). Tests marked `examples`
check the values found on the real example layouts of the gds2palace, gds_prepare_for_EM and lumpedmodel
repositories; they run when those repositories are next to this one and are skipped otherwise. The snp2le tests
are skipped when snp2le is not installed. GitHub runs the tests on every pull request (Windows, Linux and macOS,
Python 3.10 and 3.13).

## Notes

- All paths are paths on the computer where the server runs.
- `generate_model` runs the script with the Python of the server's environment. If the script adds a local
  `gds2palace` folder to `sys.path`, that copy is used; the result shows which gds2palace version and file were used.
- With the stdio transport, gds2palace's printed output is captured and returned in the tool results.
- Windows (gmsh, plots, the live log) open on the computer where the server runs: Windows, macOS (Terminal for the
  log), or Linux with a display (the log uses the first working terminal of x-terminal-emulator, gnome-terminal,
  konsole, xfce4-terminal, xterm). Without a display, the tools say so and work without the window.

## License

GPL-3.0-or-later, like gds2palace.
