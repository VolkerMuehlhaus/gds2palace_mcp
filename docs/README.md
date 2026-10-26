# gds2palace_mcp for RFIC designers

This guide is for RFIC designers who use, or want to use, gds2palace for EM simulation with AWS Palace, and who have
never used MCP. It explains what the gds2palace MCP server is, what it can and can't do for you, and how to set it
up. [example_session.md](example_session.md) shows a complete session with an inductor, with real output.

## What MCP is

An AI assistant such as Claude can read and write text, but on its own it can't open your GDSII file or run
gds2palace. **MCP** (Model Context Protocol) is a standard way to give an assistant extra tools. An **MCP server**
is a small program that offers such tools. You install it once and register it with your assistant.

After that, you work in plain language. You say what you want, for example "build a Palace model for this
inductor". The assistant decides which tools it needs, calls them, reads the results and answers you. You don't
type tool names or commands yourself.

Three things are useful to know:

- **The tools run on your computer**, in your Python environment, on your files. The server doesn't upload your
  layout anywhere. The assistant itself is a cloud service, though: what a tool returns (layer statistics, file
  paths, log lines) is sent to it as part of the conversation, like anything else you type or paste.
- **You approve tool calls.** The first time the assistant wants to use a tool, it asks you. You can allow it once
  or for the rest of the session.
- **The assistant can still make mistakes.** The tools give it facts from your files instead of guesses, but you
  remain the designer. Check the model before you trust the results.

## Where your data goes

Your files stay on your computer. Facts taken from them, and pictures you ask for, don't.

The server reads and writes your GDSII file, the mesh, the Palace input files and your S-parameter files on your own
disk. It never uploads them, and no tool returns polygon coordinates or mesh contents. But the assistant runs in the
cloud, so everything a tool **returns** goes to it as part of the conversation, including images:

| Tool | What the assistant receives |
|---|---|
| `inspect_gds` | File path, cell name, the layer numbers used, shape count per layer, bounding boxes, smallest shape side and smallest gap per layer |
| `inspect_stackup`, `list_stackups` | The stackup contents: layer heights, materials, conductivities. For the IHP stackups that ship with gds2palace, this is public data |
| `check_layout_complexity`, `check_repeated_patterns`, `simplify_layout`, `suggest_mesh_size`, `propose_ports`, `check_ports` | File paths, cell names, polygon counts per layer, layout size, widths, gaps and port positions |
| `check_model_script` | The setting values in the script (frequencies, mesh sizes, ...) |
| `generate_model`, `wait_for_model` | File paths, log lines from gds2palace and gmsh, mesh size |
| `show_in_gmsh`, `show_sparameters` | Only the file paths. The windows are shown on your screen; the assistant doesn't see them |
| `preview_layout` | **A picture of your layout.** This is the one tool that sends the design itself, as an image. For confidential layouts, ask for the picture to be saved locally only ("save the preview as preview.png, don't send it to yourself") |
| `sparameter_summary`, `passive_values` | Numbers from your S-parameter files: dB values, L, Q, C, resonance frequency |
| `plot_sparameters`, `passive_values` with plot | Plots of your S-parameters or of L and Q, as images. Like the layout picture, they can be saved locally only |

Apart from the layout picture, this is a coarse fingerprint of a design (size, layers, smallest features, performance
numbers), not the design itself.

Two things outside these tools also send data:

- **The assistant's own file access.** Claude Code can read files by itself. A model script that it writes or edits
  is sent in full, and so is any file you ask it to look at. To make sure it never reads layouts or meshes, add deny
  rules to your Claude Code settings (`.claude/settings.json`):

  ```json
  { "permissions": { "deny": ["Read(**/*.gds)", "Read(**/*.gds2)", "Read(**/*.oas)", "Read(**/*.msh)"] } }
  ```

- **The guide download.** If gds2palace is installed without its repository, the server downloads the gds2palace
  guide (AGENTS.md) from GitHub. This request sends nothing about your design.

How long conversations are kept, and whether they may be used for training, depends on your Claude plan and its data
settings. Check them before you work on confidential designs.

## What you can do with it

The gds2palace workflow needs three inputs: a GDSII layout, an XML stackup file and a Python model script. The
server helps with all three and then builds the model.

| You ask | Tool the assistant uses | What you get |
|---|---|---|
| "Which stackups are available?" | `list_stackups` | The IHP stackups that ship with gds2palace: SG13G2, SG13G2 with conformal passivation, SG13CMOS5L |
| "Show me the SG13G2 stackup with a 100 µm thick chip" | `inspect_stackup` | Layers with GDS numbers and heights, materials, dielectrics, free layer numbers for ports |
| "What is in my layout?" | `inspect_gds` | For each layer: number of shapes, size, smallest line and smallest gap, and what the layer is in the stackup (or that gds2palace ignores it) |
| "Is this layout too complex for FEM?" | `check_layout_complexity` | Polygon count per layer, layout size and density, which layers dominate (e.g. via arrays, dummy fill), and what simplification would help |
| "Will this mesh in reasonable time?" | `check_repeated_patterns` | The shapes that repeat many times (via fields, dummy fill, cheesed ground tiles), found from the cell hierarchy, and which simplification would reduce them. The assistant asks you before meshing a layout with many of them |
| "Which holes are in my ground planes?" | `measure_holes` | The opening sizes that repeat many times in each ground plane (cheesing, tiled meshes), measured in your layout, and the openings that belong to the circuit and stay open |
| "Remove the dummy fill and merge the via arrays" | `simplify_layout` | A simplified copy of your layout (`<name>_simplified.gds`), with polygon counts before and after. The original file is not changed |
| "Which mesh size do I need?" | `suggest_mesh_size` | The `refined_cellsize` for an accurate and a quick model, from the narrowest traces and the smallest gaps between coupled lines on the top metal layers |
| "Where should the ports go?" | `propose_ports`, `add_ports` | Port positions from your pins, labels or open line ends, each with a ground reference; after you confirm, a copy of the layout with the port lines |
| "Are my ports connected?" | `check_ports` | Whether each port line lies on metal of both layers it connects, before any meshing |
| "Write a model script for it" | (writes the file) | A gds2palace script, based on the official template |
| "Check my script" | `check_model_script` | Typos in setting names, missing settings, port layers that are not read, outdated settings |
| "Generate the model" | `generate_model` | The Palace input files (`config.json`, mesh, `run_sim`), the mesh size, an estimate of the unknowns and the peak RAM Palace will need, and the warnings from meshing. A terminal window on your screen shows the meshing log live |
| "Keep waiting" | `wait_for_model` | If meshing takes longer than 5 minutes, the assistant asks you whether to keep waiting. It never stops the run; the log window shows how far it is |
| "Show me the mesh" | `show_in_gmsh` | The meshed model, or the geometry before meshing, in a gmsh window on your screen |
| "Look at the layout. Are the ports at the right place?" | `preview_layout` | The assistant sees a picture of the layout with the ports labelled P1, P2, ... IHP SG13G2 layouts get the KLayout colors; for another technology, give the stackup file, and the colors come from its materials |
| "Did the Palace run finish, and how long did it take?" | `summarize_results` | Unknowns, mesh size, run time, peak RAM, and for adaptive mesh refinement how much S changed per iteration, for one run or a whole folder of runs |
| "Show me S11 and S21 of result.s2p" | `show_sparameters` | The plot_snp windows (dB, phase, Smith chart) on your screen |
| "Compare simulated.s2p with measured.s2p" | `plot_sparameters`, `sparameter_summary` | The assistant sees both curves in one plot, and gets the numbers to compare |
| "Make a SPICE model of this inductor" | `fit_equivalent_circuit` | An equivalent circuit (Ngspice or VACASK subcircuit) with its element values, from snp2le. The assistant proposes the model type and the extraction frequency; you confirm |
| "What is L and Q of this inductor?" | `passive_values` | L, R and Q at low frequency and at chosen frequencies, peak Q, self-resonance frequency |

Some things this helps with in practice:

- **Choosing the mesh size.** The mesh cell size must not be larger than the narrowest trace or the smallest gap
  between coupled lines: in the gds2palace mesh convergence studies, 5 µm cells on a 2 µm gap gave clearly wrong
  results or crashed the solver. The assistant finds these features in your layout and suggests the cell size,
  also for quick models. It only goes coarser if you ask for it.
- **Catching silent mistakes.** gds2palace ignores setting names it doesn't know. `settings['nogui'] = True` (instead
  of `no_gui`) gives no error, and the run then hangs in an invisible gmsh window. `check_model_script` finds such
  typos.
- **Missing ports.** A port polygon on a layer that is not added to the layer list is never read. The model then has
  fewer ports than you think. The check finds this too.
- **Understanding a stackup.** You can ask questions such as "how thick is the oxide between TopMetal1 and
  TopMetal2?" or "which layer is the substrate ground?", and the assistant answers from the stackup file.
- **Learning gds2palace.** The assistant has the gds2palace guide for AI assistants (AGENTS.md) available, and can
  explain settings and suggest the right example to start from.

The assistant is told to **ask you for design decisions** instead of inventing them: frequency range, where the ports
are and what they connect, port impedance, which stackup, and how much accuracy you need versus run time.

## What it can't do

- **It doesn't run the Palace solver.** Palace runs on Linux. After `generate_model`, you run `run_sim` on your Linux
  machine (or WSL, or a remote server), as before. A later version may send jobs to a simulation server.
- **It reads results, it doesn't run Palace.** After you copy the results back from the Linux machine, it summarizes
  the run (`summarize_results`) and works with the Touchstone files. It can only check the Palace log for solver
  problems ("did NOT converge") if the log was saved in the run folder.
- **It doesn't change your original layout.** `simplify_layout` and `add_ports` write a new GDS file next to it. Ports
  are polygons on extra GDS layers (201, 202, ...); `propose_ports` only proposes via ports to ground, so draw
  in-plane and differential ports in KLayout yourself.
- **Equivalent circuits come from snp2le's model structures.** `fit_equivalent_circuit` fits the structures snp2le
  offers (inductor, MIM capacitor, line, Wilkinson, balun, branch-line) or a vector fit. Check the fit with its plot
  before you use the netlist.
- **The assistant can't see the 3D model.** Ask "show me the mesh" (or "the geometry before meshing"), and the
  `show_in_gmsh` tool opens it in a gmsh window on your screen. The assistant only knows that the window opened, so
  tell it what you see. During model generation, gmsh windows are switched off so that the run doesn't stop and
  wait; a terminal window shows the meshing log instead (also saved as `<script>_generate.log`).
- **It doesn't stop a running model generation.** If meshing takes very long, the assistant asks you whether to keep
  waiting. To end it, end the Python process yourself (Task Manager, Activity Monitor or `kill`).
- **It doesn't know your design intent.** It can tell you that the smallest gap is 2 µm. It can't know if you care
  about 60 GHz or 160 GHz, or which pads are the ports.
- **It doesn't guarantee correct results.** A model that meshes without errors can still be wrong (wrong stackup,
  wrong port, mesh too coarse). Compare with something you know: a measured device, the resistance of a line, the
  value of a capacitor.
- **It only sees your own computer.** Paths in the conversation are paths on the computer where the server runs.
- **It is for gds2palace (Palace and Elmer) only**, not for openEMS.

## Setup

You need:

1. **Python 3.10 or newer.**
2. **An MCP-capable assistant.** These instructions use [Claude Code](https://claude.com/claude-code), which runs in
   a terminal or in VS Code. Claude Desktop and other MCP clients work too; their documentation explains where to
   add a server.
3. **gds2palace_mcp.** Install it with pip, ideally in a virtual environment. This also installs gds2palace and
   everything it needs:

   ```
   python -m venv d:\venv\palace
   d:\venv\palace\Scripts\activate
   pip install gds2palace_mcp
   ```

   On Linux or macOS:

   ```
   python3 -m venv ~/venv/palace
   source ~/venv/palace/bin/activate
   pip install gds2palace_mcp
   ```

Then register the server with Claude Code. Give it the full path of the Python in that environment, so that it
always uses the right one:

```
claude mcp add gds2palace -- d:\venv\palace\Scripts\python.exe -m gds2palace_mcp.server
```

On Linux or macOS:

```
claude mcp add gds2palace -- ~/venv/palace/bin/python -m gds2palace_mcp.server
```

Check that it works:

```
claude mcp list
```

Expected output (the path is yours):

```
gds2palace: d:\venv\palace\Scripts\python.exe -m gds2palace_mcp.server - ✓ Connected
```

Inside a Claude Code session, the command `/mcp` shows the same list, and lets you see the tools of each server.

## How to start

Open a terminal in the folder with your layout, start `claude`, and describe what you want. A few examples:

```
Which stackup files does gds2palace include, and what is the difference between them?
```

```
Look at my_inductor.gds with the SG13G2 stackup. Which layers are used, and what is the smallest gap?
```

```
Build a Palace model for my_inductor.gds. The ports are on layers 201 and 202.
```

```
Check palace_balun.py and tell me if any settings are wrong.
```

The server also provides a ready-made starting prompt for a new model. In Claude Code, type `/` and choose
`/mcp__gds2palace__new_model` from the list. The assistant then inspects the layout and asks you the design questions before it writes
anything.

## Next

[example_session.md](example_session.md) shows a full session: from a layout to a meshed Palace model, with the
output you can expect at each step.
