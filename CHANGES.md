# Changes

Weekly summary of changes to gds2palace_mcp, newest week first. Versions are
those of the package (`gds2palace_mcp.__version__`).

## Week of 2026-10-19 (0.1.0 to 0.5.0)

First week of the project: from a first read-only server to a tool set that
covers layout checks, port setup, mesh settings, model generation, and result
analysis.

### Model preparation

- **Server (0.1.0, 10-20):** stdio MCP server with `list_stackups`,
  `inspect_stackup`, `inspect_gds`, `check_model_script` and `generate_model`;
  resources for the AGENTS guide and the bundled stackups; prompt `new_model`.
  Needs gds2palace 0.9.1 or later.
- **Layout complexity (0.2.0, 10-21):** `check_layout_complexity` reports
  polygon count per stackup layer, layout size and density, and hints for
  simplification. `simplify_layout` runs setupEM's Simplify GDS pipeline
  (gds_prepare_for_EM 1.6) with the layers taken from the stackup.
- **Ground plane openings (0.4.0, 10-22):** `measure_holes` lists the repeated
  openings in ground planes; `simplify_layout(fill_ground_holes=True)` fills
  exactly those. `fill_cutouts` now needs an explicit `max_hole_area` from the
  user (the 1 um2 default is gone). Needs gds_prepare_for_EM 1.7.
- **Repeated patterns (0.5.0, 10-23):** `check_repeated_patterns` finds via
  fields, fill and tiles with holes from the cell hierarchy without flattening,
  names the `simplify_layout` option that reduces them, and recommends
  preprocessing from 1000 repeated polygons.
- **Ports (0.3.0, 10-22):** `check_ports` follows gds2palace's port geometry
  (also run by `check_model_script`); `propose_ports` suggests ports from IHP
  pins, labels or open trace ends; `add_ports` writes a layout copy with the
  port lines and returns the `add_port` lines. The proposals reproduce the real
  ports of the repo examples.
- **Mesh size (0.3.0, 10-22):** `suggest_mesh_size` proposes `refined_cellsize`
  from traces and coupled lines on the top two conductor layers. The 2 um / 5 um
  defaults apply to IHP stackups only; other technologies get a value from the
  smallest feature found, or a question to the user. `check_model_script`
  warns when the script's cell size is above the smallest feature.

### Model generation

- **Resource estimate (0.3.0, 10-22):** `generate_model` counts the mesh
  tetrahedra and estimates the Palace unknowns and the peak RAM per element
  order and coarse solve type, calibrated on runs in the gds2palace repo.
- **Long meshing runs (0.5.0, 10-23):** `generate_model` waits 5 minutes and
  then returns `still_running`; `wait_for_model` keeps waiting. The server never
  stops a model generation. The meshing log goes to `<script>_generate.log` and
  is shown live in a terminal window, whose first line says that closing it does
  not stop the run.
- **show_in_gmsh (10-20):** opens the mesh, or the unmeshed geometry
  (`generate_model` `save_geometry`), in a detached gmsh window.

### Viewing and results

- **preview_layout (10-20 to 10-22):** layout PNG with labelled ports via
  gds_viewer. Without a stackup, or with an IHP stackup, it uses the SG13G2
  look with a semi-transparent fill under the patterns (`fill_alpha` 0.45; 0
  gives the exact KLayout look). With another stackup it takes the material
  colors from the stackup and adds a legend. `style` forces either look. Needs
  gds_viewer 0.2.0. `send_image=False` with `save_png` keeps the picture local.
- **S-parameters (10-20):** `sparameter_summary` (ports, range, passivity,
  reciprocity, values at frequencies), `plot_sparameters` (images),
  `show_sparameters` (plot_snp windows on the screen), `passive_values` (L or C,
  R, Q, peak Q, SRF with lumpedmodel's definitions).
- **summarize_results (0.3.0, 10-22):** Palace run summary through gds2palace's
  `palace_summary`: unknowns, mesh elements, run time, peak RAM, error
  indicator, AMR convergence with optional plot, Touchstone files, and
  "did NOT converge" counts. Needs gds2palace 0.9.2.
- **fit_equivalent_circuit (10-22):** runs `snp2le` (iic-jku) from the command
  line and returns the element values and the netlist. `snp2le` is called as a
  command, not a dependency (`pip install snp2le`).

### Fixes

- `simplify_layout` no longer fills holes of any size by default: on the Butler
  matrix it filled the 4 openings inside the coupler rings on TopMetal2 (+75,600
  um2 of metal) (10-21).
- `propose_ports` (0.4.1, 10-23): a label on a long trace near its end now marks
  that end instead of giving a port line across the middle; a label at a trace
  corner picks the longer of two equally close edges (2 of 8 Butler matrix ports
  got the chamfer edge before).

### Quality, platforms, packaging and docs

- **Tests (0.4.1, 10-23):** 112 pytest tests on generated layouts, stackups and
  Touchstone files with known answers, plus regression tests on the examples of
  the gds2palace, gds_prepare_for_EM and lumpedmodel repos (skipped without
  them), gmsh meshing and the stdio protocol. 9 deliberate changes to a scratch
  copy were all caught.
- **CI and platforms (0.5.0, 10-23):** GitHub Actions on Windows, Linux and
  macOS. Windows open through one helper with a display check; setup docs for
  Linux and macOS.
- **Versions (10-23):** the server instructions name the gds2palace_mcp and
  gds2palace versions, and `list_stackups` returns the versions of all packages
  the tools use, so the assistant can tell which server it is talking to.
- **Docs and packaging (10-20, 10-21):** guide for RFIC designers new to MCP
  (`docs/README.md`), example inductor session (`docs/example_session.md`), data
  flow and deny rules for layout and mesh reads, README banner, PyPI project URLs
  with absolute links and images.
