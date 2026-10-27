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

- **The tools run on your computer**, in your Python environment, on your files. The MCP server doesn't upload your
  layout anywhere. The assistant itself is a cloud service, though: what a tool returns (layer statistics, file
  paths, log lines) is sent to it as part of the conversation, like anything else you type or paste.
- **You approve tool calls.** The first time the assistant wants to use a tool, it asks you. You can allow it once
  or for the rest of the session.
- **The assistant can still make mistakes.** The tools give it facts from your files instead of guesses, but you
  remain the designer. Check the model before you trust the results.

## Where your data goes

Your files stay on your computer. Facts taken from them, and pictures you ask for, don't.

The MCP server reads and writes your GDSII file, the mesh, the Palace input files and your S-parameter files on your own
disk. No tool returns polygon coordinates or mesh contents. It copies files to another computer only if you run Palace
on a simulation server that you configured yourself (see [Running Palace](#running-palace)): then the mesh, `config.json`
and `port_information.json` go to that simulation server, and the results come back. But the assistant runs in the cloud, so
everything a tool **returns** goes to it as part of the conversation, including images:

| Tool | What the assistant receives |
|---|---|
| `inspect_gds` | File path, cell name, the layer numbers used, shape count per layer, bounding boxes, smallest shape side and smallest gap per layer |
| `inspect_stackup`, `list_stackups` | The stackup contents: layer heights, materials, conductivities. For the IHP stackups that ship with gds2palace, this is public data |
| `check_layout_complexity`, `check_repeated_patterns`, `simplify_layout`, `suggest_mesh_size`, `propose_ports`, `check_ports` | File paths, cell names, polygon counts per layer, layout size, widths, gaps and port positions |
| `check_model_script` | The setting values in the script (frequencies, mesh sizes, ...) |
| `generate_model`, `wait_for_model` | File paths, log lines from gds2palace and gmsh, mesh size |
| `show_in_gmsh`, `show_sparameters` | Only the file paths. The windows are shown on your screen; the assistant doesn't see them |
| `preview_layout` | **A picture of your layout.** This is the one tool that sends the design itself, as an image. For confidential layouts, ask for the picture to be saved locally only ("save the preview as preview.png, don't send it to yourself") |
| `sparameter_summary`, `passive_values`, `transform_sparameters` | Numbers from your S-parameter files: dB values, L, Q, C, resonance frequency |
| `plot_sparameters`, `passive_values` with plot | Plots of your S-parameters or of L and Q, as images. Like the layout picture, they can be saved locally only |
| `list_targets`, `submit_simulation`, `simulation_status`, `fetch_results` | Your target names and the names of your simulation servers, file paths, RAM, the last lines of the Palace log, and how many Palace processes other users run on the simulation server, with their user names (never their files or model names) |

Apart from the layout picture, this is a coarse fingerprint of a design (size, layers, smallest features, performance
numbers), not the design itself.

Two things outside these tools also send data:

- **The assistant's own file access.** Claude Code can read files by itself. A model script that it writes or edits
  is sent in full, and so is any file you ask it to look at. To make sure it never reads layouts or meshes, add deny
  rules to your Claude Code settings (`.claude/settings.json`):

  ```json
  { "permissions": { "deny": ["Read(**/*.gds)", "Read(**/*.gds2)", "Read(**/*.oas)", "Read(**/*.msh)"] } }
  ```

- **The guide download.** If gds2palace is installed without its repository, the MCP server downloads the gds2palace
  guide (AGENTS.md) from GitHub. This request sends nothing about your design.

How long conversations are kept, and whether they may be used for training, depends on your Claude plan and its data
settings. Check them before you work on confidential designs.

## What you can do with it

The gds2palace workflow needs three inputs: a GDSII layout, an XML stackup file and a Python model script. The
MCP server helps with all three and then builds the model.

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
| "Where can I run Palace?" | `list_targets` | The computers you configured for Palace (see [Running Palace](#running-palace)), and whether they are reachable and free |
| "Run it on the simulation server" | `submit_simulation` | Palace starts on that computer; a terminal window on your screen shows its log live. The assistant tells you the RAM estimate and asks you first |
| "How far is the simulation?" | `simulation_status` | Running, finished or failed; which port, frequency and AMR iteration Palace is at, its RAM, and the last log lines |
| "Get the results" | `fetch_results` | The results copied back into the model folder, the Touchstone files (`combine_snp`) and the run summary |
| "Did the Palace run finish, and how long did it take?" | `summarize_results` | Unknowns, mesh size, run time, peak RAM, and for adaptive mesh refinement how much S changed per iteration, for one run or a whole folder of runs |
| "Show me S11 and S21 of result.s2p" | `show_sparameters` | The plot_snp windows (dB, phase, Smith chart) on your screen |
| "Compare simulated.s2p with measured.s2p" | `plot_sparameters`, `sparameter_summary` | The assistant sees both curves in one plot, and gets the numbers to compare |
| "Show me Sdd21 and the CMRR of my balun" | `transform_sparameters` | A new Touchstone file with the differential and common-mode S-parameters of the port pairs you name, and the numbers: Sdd/Sds, mode conversion (Scd/Scs) and the common-mode rejection. Also: "renormalize to 100 Ω", "terminate port 3 with 50 Ω", "cascade with the pad model" |
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

- **It runs Palace only where you allowed it.** Palace runs on Linux: on a simulation server (over ssh) or in WSL/Linux on your own
  computer, as listed in your targets file (see [Running Palace](#running-palace)). Without that file, you run
  `run_sim` on your Linux machine as before, and the MCP server summarizes the results you copy back.
- **It doesn't stop a Palace run.** To end one, end its processes yourself (`kill` on the simulation server).
- **It is no batch system.** A target refuses a second run while Palace runs there; there is no queue (see
  [One run at a time](#one-run-at-a-time)).
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
- **It only sees your own computer.** Paths in the conversation are paths on the computer where the MCP server runs.
- **It is for gds2palace (Palace and Elmer) only**, not for openEMS.

## Setup

You need:

1. **Python 3.10 or newer.**
2. **An MCP-capable assistant.** These instructions use [Claude Code](https://claude.com/claude-code), which runs in
   a terminal or in VS Code. Claude Desktop and other MCP clients work too; their documentation explains where to
   add an MCP server.
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

Then register the MCP server with Claude Code. Give it the full path of the Python in that environment, so that it
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

## Running Palace

Palace needs Linux. The MCP server can start it for you on a Linux computer, watch it, and copy the results back.
That computer is a **simulation server** that you reach with ssh, or WSL/Linux on your own computer. You decide
which computers it may use: the MCP server only runs Palace on the targets in a small text file that you write.
The assistant can only choose a target by its name; it can never send your model anywhere else.

The file is `~/.gds2palace_mcp/targets.toml` (on Windows `C:\Users\<you>\.gds2palace_mcp\targets.toml`). To keep it
elsewhere, set the environment variable `GDS2PALACE_MCP_TARGETS` to its path. Each target is one section:

```toml
# a Linux simulation server, over ssh
[targets.simserver]
type = "ssh"
host = "simserver"                  # name from ~/.ssh/config, or user@host
description = "16 cores, 128 GB"

# WSL on this Windows computer (or this computer itself, on Linux)
[targets.wsl]
type = "local"
```

| Option | For | Default | Meaning |
|---|---|---|---|
| `type` | all | (needed) | `"ssh"` for a simulation server, `"local"` for WSL on Windows or this Linux computer |
| `host` | ssh | (needed) | The simulation server, as `ssh` knows it |
| `run_command` | all | `"run_palace {config}"` | How Palace starts in the run folder. `{config}` is the config file. The default uses the `run_palace` that the gds2palace installers set up |
| `remote_dir` | ssh | `"palace_runs/mcp"` | Where the runs go on the simulation server, relative to your home folder |
| `wsl_distro` | local | the default distribution | The WSL distribution, e.g. `"Ubuntu-24.04"` |
| `description` | all | | A note that the assistant sees, e.g. the size of the machine |

What the targets need:

- **Palace on the target**, started by `run_command`. The number of processes and the container are set there (in
  `run_palace`), not in the targets file.
- **For ssh: login without a password** (an ssh key). The MCP server never answers a password prompt. See
  [Login to the simulation server without a password](#login-to-the-simulation-server-without-a-password) below.
- **For local on Windows: WSL** with Palace, as the gds2palace Windows installer sets it up. The model folder must be
  on a local drive, not on a network drive.

Ask "where can I run Palace?", and the assistant checks each target: reachable, Palace found, free RAM, and whether
Palace is already running there.

### Login to the simulation server without a password

The MCP server runs `ssh` in the background, where nobody can type a password. So ssh must log in with a **key**
instead: a pair of files on your computer, of which the public half is stored on the simulation server. You set this
up once per computer. The commands below are the same in a Windows PowerShell, a Linux terminal and a macOS terminal
(Windows 10 and 11 include ssh); replace `you@simserver.example.com` with your login and the server's name.

1. **Make a key**, if you don't have one yet (`~/.ssh/id_ed25519` exists, on Windows
   `C:\Users\<you>\.ssh\id_ed25519`):

   ```
   ssh-keygen -t ed25519
   ```

   Press Enter for the file name. For the passphrase, see step 4.

   This writes two files into your `.ssh` folder. `id_ed25519` is the **private key** (Ed25519 is the key type, the
   current standard): it stays on your computer, never give it to anyone. `id_ed25519.pub` is the **public key**:
   that is the half that goes to the simulation server in step 2. When you log in, the server checks that your
   computer has the private key that matches the public key it stores, so no password is needed. An older key
   (`id_rsa`) works the same way; then use `id_rsa.pub` in step 2.

2. **Copy the public key to the simulation server.** It asks for your password one last time.

   On Linux and macOS:

   ```
   ssh-copy-id you@simserver.example.com
   ```

   In a Windows PowerShell (Windows has no `ssh-copy-id`):

   ```
   type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh you@simserver.example.com "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
   ```

3. **Give the server a short name** (optional, but then `host = "simserver"` is enough in the targets file). Add to
   `~/.ssh/config` (on Windows `C:\Users\<you>\.ssh\config`, a text file without extension):

   ```
   Host simserver
       HostName simserver.example.com
       User you
   ```

4. **Passphrase.** A key without a passphrase works at once, but anyone who copies the key file can log in. With a
   passphrase, let the ssh agent remember it, so that ssh doesn't ask:
   - Windows: once, in a PowerShell as administrator: `Get-Service ssh-agent | Set-Service -StartupType Automatic;
     Start-Service ssh-agent`, then (as yourself) `ssh-add`.
   - macOS: `ssh-add --apple-use-keychain ~/.ssh/id_ed25519`.
   - Linux: `ssh-add` in your desktop session (most desktops start an agent).

5. **Check** that it works without any question:

   ```
   ssh -o BatchMode=yes simserver true
   ```

   No output means it works. "Permission denied" means the key isn't accepted yet (repeat step 2, or ask the
   server's administrator whether key login is allowed). The first connection to a new server asks you to confirm
   its fingerprint: answer it once in a terminal (`ssh simserver`), the MCP server can't.

More background: [Microsoft: OpenSSH key management](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
(Windows), [Red Hat: passwordless SSH](https://www.redhat.com/en/blog/passwordless-ssh) (Linux).

### What happens during a run

1. **Submit.** The assistant tells you the target and the RAM estimate, and asks you first. For an ssh target, the
   mesh, `config.json` and `port_information.json` are copied to
   `<remote_dir>/<your user name>/<project folder>/<model>_<job id>` on the simulation server. That folder can only be read
   by you. Every run gets a folder of its own; nothing on the simulation server is overwritten.
2. **Watch.** A terminal window on your screen shows the Palace log live. Closing it does not stop the run. Ask "how
   far is it?" at any time: the assistant reports the port, frequency and AMR iteration, the RAM, and the last log
   lines. This also works in a new session, or after the assistant was restarted: each run is recorded in
   `mcp_palace_job.json` in the model folder.
3. **Fetch.** When Palace finished, the results are copied back into `palace_model/<model>_data` (the Palace log as
   `palace.log`). The Touchstone files are made on your computer, as `combine_snp` does, and the assistant summarizes
   the run. The paraview field dumps are only copied if you ask for them, because they are large. The run folder
   stays on the simulation server until you ask for it to be deleted.

The MCP server never stops a run. If you want to end one, end its processes on the simulation server (`kill`).

### One run at a time

A target refuses to start a run while any Palace runs on that computer, also one started by someone else or from
setupEM, so that two runs never fight for the same cores. The assistant then tells you how many Palace processes run
there and whose they are (user names and process counts only).

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

The MCP server also provides a ready-made starting prompt for a new model. In Claude Code, type `/` and choose
`/mcp__gds2palace__new_model` from the list. The assistant then inspects the layout and asks you the design questions before it writes
anything.

## Next

[example_session.md](example_session.md) shows a full session: from a layout to a meshed Palace model, with the
output you can expect at each step.
