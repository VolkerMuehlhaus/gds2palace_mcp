# Changes

Weekly summary of changes to gds2palace_mcp, newest week first. Versions are
those of the package (`gds2palace_mcp.__version__`).

## Week of 2026-10-26 (0.6.0 to 0.8.0)

### Figures of merit for transformers, baluns, couplers and lines (0.8.0, issue #3)

- **New tool `figures_of_merit`:** values at chosen frequencies and min/max (with the worst case)
  in a band, optional plot. Port roles as in `passive_values`: defaults where the usual numbering is
  clear, required for couplers; the assistant asks the user if unclear.
- **Transformer:** L1, L2, k, Q1, Q2, minimum insertion loss (maximum available gain) and the source
  and load impedances of the simultaneous conjugate match, self-resonance of each winding.
- **Balun:** insertion loss (power that reaches neither output) and differential insertion loss
  (-|Sds21|), amplitude and phase imbalance, CMRR, input and differential output return loss.
- **Coupler / hybrid:** coupling, isolation, directivity, through loss, amplitude balance, phase
  difference. **Power divider:** split, balance, isolation, return losses.
- **Line** of known length: Z0, effective permittivity and loss in dB/mm from the ABCD matrix, with
  the phase unwrapped over frequency (warning when the file has too few points).
- **Differential** transformers and lines on 4 ports: `pairs`, evaluated on the differential 2-port.
- Checked on networks with known values (coupled inductors, ideal balun with set imbalance, branch-line
  hybrid, Wilkinson, lines over 1.3 wavelengths, differential versions built from mixed-mode data), and
  the optimum impedances against the transducer gain they give. On the D-band balun of the gds2palace
  study, amplitude and phase imbalance agree with S21/S31 computed directly.

### Mixed-mode S-parameters and network operations (0.7.0, issue #2)

- **New tool `transform_sparameters`:** writes a new Touchstone file (the input is never changed) and
  returns how its ports relate to the input's, so that `sparameter_summary`, `plot_sparameters`,
  `passive_values` and `fit_equivalent_circuit` work on the result.
- **`mixed_mode`:** differential and common-mode S-parameters for the given port pairs, e.g. `[[1, 3],
  [2, 4]]` for a differential line or `[[2, 3]]` for a balun with single-ended port 1. The result names
  each parameter in mixed-mode notation (file S21 → Sdd21 or Sds21), and gives transmission, reflection,
  mode conversion and the common-mode rejection (CMRR) per pair. Reference impedances: differential
  2 × z0, common mode z0 / 2.
- **`renormalize`** (one z0 or one per port), **`terminate`** (ports closed with ohm, open or short, the
  others renumbered) and **`cascade`** (two 2-ports; a second file on other frequency points is
  interpolated, one that doesn't cover the range is refused).
- Files with different reference impedances per port are written as Touchstone 2.0 (`[Reference]`).
- Checked on the D-band balun of the gds2palace mesh convergence study: power is conserved between the
  single-ended and mixed-mode forms to 1e-15, and the CMRR equals |S21 − S31| / |S21 + S31| computed
  directly.
- Needs scikit-rf 2.2.0: it fixes `innerconnect` (used to terminate and cascade), which in 2.1 kept the
  wrong S-parameter definition label for power-wave networks.

### Running Palace (issue #1)

- **Targets file:** the user lists where Palace may run in `~/.gds2palace_mcp/targets.toml`
  (`GDS2PALACE_MCP_TARGETS` for another path): simulation servers (ssh), and the local machine (WSL on Windows, or
  Linux). The tools only take a target name; hosts, folders and commands come from this file.
- **New tools:** `list_targets` (with `check`: reachable, run command found, RAM, Palace processes
  running there), `submit_simulation`, `simulation_status` and `fetch_results`.
- **Submit:** copies mesh, config and port information to the simulation server into a private folder of its own
  per run (`<remote_dir>/<user>/<project>/<model>_<job id>`, mode 700), starts the target's `run_palace`
  detached, and shows the log live in a terminal window. The RAM estimate is compared with the free RAM.
  A target refuses to start while Palace runs there (one run at a time, no queue).
- **Status:** running, finished, failed or stopped; port, frequency step and AMR iteration from
  the log, the run's own Palace processes and RAM (found by the job id in their environment, so other
  users' runs are never counted as ours), and others' runs as counts and user names only. Works after a
  restart: each run is recorded in `mcp_palace_job.json` in the model folder.
- **Fetch:** copies the results back (paraview field dumps only on request), makes the Touchstone files
  with gds2palace's `combine_extend_snp` on this computer, and returns the run summary. The run
  folder on the simulation server is deleted only with `delete_remote=True`.
- Tested on WSL (Palace 0.16) and on two Linux simulation servers over ssh (Palace 0.18): the fetched S-parameters
  of the same line model agree within 0.0013 dB.
- `docs/README.md`: new section "Running Palace"; "Where your data goes" and "What it can't do" updated.
- New dependency `tomli` on Python 3.10 (3.11 and newer read TOML themselves).

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
  then returns `still_running`; `wait_for_model` keeps waiting. The MCP server never
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
- **Versions (10-23):** the MCP server's instructions name the gds2palace_mcp and
  gds2palace versions, and `list_stackups` returns the versions of all packages
  the tools use, so the assistant can tell which MCP server it is talking to.
- **Docs and packaging (10-20, 10-21):** guide for RFIC designers new to MCP
  (`docs/README.md`), example inductor session (`docs/example_session.md`), data
  flow and deny rules for layout and mesh reads, README banner, PyPI project URLs
  with absolute links and images.
