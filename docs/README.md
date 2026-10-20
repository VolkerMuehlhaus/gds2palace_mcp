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

## What you can do with it

The gds2palace workflow needs three inputs: a GDSII layout, an XML stackup file and a Python model script. The
server helps with all three and then builds the model.

| You ask | Tool the assistant uses | What you get |
|---|---|---|
| "Which stackups are available?" | `list_stackups` | The IHP stackups that ship with gds2palace: SG13G2, SG13G2 with conformal passivation, SG13CMOS5L |
| "Show me the SG13G2 stackup with a 100 µm thick chip" | `inspect_stackup` | Layers with GDS numbers and heights, materials, dielectrics, free layer numbers for ports |
| "What is in my layout?" | `inspect_gds` | For each layer: number of shapes, size, smallest line and smallest gap, and what the layer is in the stackup (or that gds2palace ignores it) |
| "Write a model script for it" | (writes the file) | A gds2palace script, based on the official template |
| "Check my script" | `check_model_script` | Typos in setting names, missing settings, port layers that are not read, outdated settings |
| "Generate the model" | `generate_model` | The Palace input files (`config.json`, mesh, `run_sim`), the mesh size, and the warnings from meshing |

Some things this helps with in practice:

- **Choosing the mesh size.** The smallest gap on each metal layer is the number you need for `refined_cellsize`.
  The assistant reads it from your layout instead of guessing.
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
- **It doesn't evaluate results yet.** Plotting S-parameters and checking a finished Palace run are not part of this
  version. Use plot_snp or setupEM for now.
- **It doesn't edit your layout.** Ports are polygons on extra GDS layers (201, 202, ...). You draw them in KLayout,
  or you ask the assistant to write a small Python script that adds them.
- **It doesn't show the 3D model.** gmsh windows are switched off so that the run doesn't stop and wait. To look at
  the geometry, run your script yourself (`python model.py`), or use setupEM.
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

   (On Linux/macOS: `source ~/venv/palace/bin/activate`.)

Then register the server with Claude Code. Give it the full path of the Python in that environment, so that it
always uses the right one:

```
claude mcp add gds2palace -- d:\venv\palace\Scripts\python.exe -m gds2palace_mcp.server
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
