# Example session: from an inductor layout to a Palace model

This is a complete session with Claude Code and the gds2palace MCP server. The layout is `L_2n0_twoport.gds`, a
2-turn octagon inductor (about 2 nH) in IHP SG13G2, from the gds2palace examples. The goal is a Palace model for
S-parameters up to 50 GHz.

The tool results and numbers below are from a real run (gds2palace 0.9.1, Windows). The assistant's wording will be
different in your session, because it writes its answers freshly each time. What matters is the sequence: look at the
inputs, ask you, write the script, check it, build the model.

Lines starting with **You** are typed by you. **Tool call** blocks show what the assistant calls; in Claude Code
these appear as collapsed lines that you can expand. Long tool results are shortened here (`...`).

## 1. Describe what you want

Start Claude Code in the folder with the layout:

```
cd d:\designs\inductor
claude
```

> **You:** I have L_2n0_twoport.gds, a 2-turn inductor in SG13G2. I want a Palace model for S-parameters up to
> 50 GHz.

The assistant first reads the gds2palace guide (resource `gds2palace://agents-guide`). Then it looks at the
available stackups and at the layout.

**Tool call** `list_stackups`

```json
{
  "stackups": [
    {"name": "SG13CMOS5L_200um.xml",
     "description": "SG13CMOS5L parameterized stackup for gds2palace, EM+thermal, includes resistors, full height SiO2+Passivation over TopMetal1. ..."},
    {"name": "SG13G2_FEM_200um.xml",
     "description": "SG13G2 parameterized stackup for gds2palace, EM+thermal, includes resistors, full height SiO2+Passivation over TopMetal2. ..."},
    {"name": "SG13G2_FEM_200um_passi3D.xml", "description": "..."}
  ]
}
```

**Tool call** `inspect_gds` with `gds_file: L_2n0_twoport.gds`, `stackup_file: SG13G2_FEM_200um.xml`

```json
{
  "cell": "L_2n0_simplify",
  "unit_m": 1e-06,
  "bbox": [-127.0, -54.0, 127.0, 284.0],
  "layers": [
    {"layer": 126, "shapes": 3, "smallest_shape_side": 12.0,   "smallest_gap": 44.4,   "stackup_role": ["conductor TopMetal1"]},
    {"layer": 133, "shapes": 4, "smallest_shape_side": 10.75,  "smallest_gap": 24.09,  "stackup_role": ["via TopVia2"]},
    {"layer": 134, "shapes": 2, "smallest_shape_side": 100.77, "smallest_gap": 2.998,  "stackup_role": ["conductor TopMetal2"]},
    {"layer": 201, "shapes": 1, "smallest_shape_side": 0.0,    "smallest_gap": null,   "stackup_role": ["not in stackup: conventional port/thermal layer range"]},
    {"layer": 202, "shapes": 1, "smallest_shape_side": 0.0,    "smallest_gap": null,   "stackup_role": ["not in stackup: conventional port/thermal layer range"]},
    {"layer": 250, "shapes": 1, "smallest_shape_side": 64.0,   "smallest_gap": null,   "stackup_role": ["sheet SUBGND"]}
  ],
  ...
}
```

The assistant summarizes this and asks you for the decisions it can't make:

> **Claude:** The layout (cell `L_2n0_simplify`, 254 × 338 µm) uses:
>
> | Layer | Stackup | Shapes | Smallest gap |
> |---|---|---|---|
> | 134 | TopMetal2 (the coil) | 2 | 3.0 µm between the turns |
> | 133 | TopVia2 | 4 | |
> | 126 | TopMetal1 (underpass and feed lines) | 3 | 44 µm |
> | 250 | SUBGND (ground sheet on the EPI layer) | 1 | |
> | 201, 202 | not in the stackup: probably your ports | 1 each | |
>
> The port shapes on 201 and 202 are lines with zero width, which is how gds2palace via ports are drawn. They sit at
> y = 0, at the ends of the TopMetal1 feed lines, above the SUBGND sheet.
>
> Before I write the script, please confirm:
>
> 1. Ports: port 1 on layer 201 and port 2 on layer 202, each a via port from SUBGND up to TopMetal1, 50 Ω?
> 2. Stackup: `SG13G2_FEM_200um.xml` (planar passivation, 200 µm chip), or the conformal passivation variant?
> 3. Frequency: 0 to 50 GHz in 0.5 GHz steps?
> 4. Mesh: the turns are 3 µm apart, so I suggest `refined_cellsize = 2` µm and FEM order 2. Coarser is faster but
>    less accurate for the coupling between the turns. OK?

Note what the assistant did and didn't do. It read the gap between the turns from the layout, and suggested a mesh
size from it. It did not decide the port connection, the stackup or the frequency range on its own.

> **You:** Yes to all four.

## 2. The assistant writes and checks the script

The assistant writes `L2n0_model.py` from the template in the gds2palace guide, with your answers filled in. The
important lines:

```python
XML_filename = utilities.get_stackup_path("SG13G2_FEM_200um.xml")   # ships with gds2palace
...
settings['fstart'] = 0
settings['fstop'] = 50e9
settings['fstep'] = 0.5e9
settings['refined_cellsize'] = 2
settings['order'] = 2
settings['no_gui'] = True
...
simulation_ports.add_port(simulation_setup.simulation_port(portnumber=1, voltage=1, port_Z0=50, source_layernum=201,
                          from_layername='SUBGND', to_layername='TopMetal1', direction='z'))
simulation_ports.add_port(simulation_setup.simulation_port(portnumber=2, voltage=1, port_Z0=50, source_layernum=202,
                          from_layername='SUBGND', to_layername='TopMetal1', direction='z'))
```

Then it checks the script before running it.

**Tool call** `check_model_script` with `script_path: L2n0_model.py`

```json
{"ok": true, "errors": [], "warnings": [], "info": []}
```

### What the check catches

The script above is clean. Here is what the same check reports when a script has a typo, `settings['nogui'] = True`
instead of `no_gui`:

```json
{
  "ok": true,
  "errors": [],
  "warnings": [
    "line 24: settings['nogui'] is not read by gds2palace and not by this script, so it has no effect. Did you mean 'no_gui'?"
  ],
  "info": [
    "settings['no_gui'] is not True: gmsh opens a window when the script runs by hand. generate_model sets no_gui itself."
  ]
}
```

gds2palace itself gives no error for this. The script would open a gmsh window and wait, which on a server without a
screen looks like a hang. The same check finds missing required settings (for example no `refined_cellsize`), and
ports whose GDS layer is never added to the layer list.

## 3. The assistant builds the model

**Tool call** `generate_model` with `script_path: L2n0_model.py`

```json
{
  "returncode": 0,
  "ok": true,
  "models": [
    {
      "sim_path": "d:\\designs\\inductor\\palace_model\\L2n0_model_data",
      "files": {"config.json": true, "port_information.json": true, "run_sim": true},
      "gds2palace_version": "0.9.1",
      "mesh_size_MB": 4.7,
      "nodes": 18019,
      "elements": 101847
    }
  ],
  "important_lines": [
    "WARNING: Start frequency changed from DC to  0.01  GHz!",
    "Warning : 8 ill-shaped tets are still in the mesh",
    "Warning : Mesh generation error summary",
    "Warning :     1 warning",
    "Warning :     0 errors"
  ]
}
```

This took 9 seconds. Larger layouts or finer meshes take minutes.

> **Claude:** The model is ready in `palace_model\L2n0_model_data`: `config.json`, the mesh `L2n0_model.msh`
> (101,847 elements, 4.7 MB), `port_information.json` and `run_sim`.
>
> Two warnings, both expected:
> - The start frequency was moved from 0 to 0.01 GHz. FEM can't solve at DC. `combine_snp` adds an extrapolated
>   DC point later, in a separate `_dc` file.
> - gmsh reports 8 ill-shaped tetrahedra. A few of these are normal near thin layers, and there were 0 mesh errors.
>
> Next step: copy the `L2n0_model_data` folder to your Linux machine and run `./run_sim` there. It runs Palace and
> converts the result to `L2n0_model.s2p`. I can't start Palace from here.

## 4. After the simulation

The Palace run itself is outside this server (see [README.md](README.md#what-it-cant-do)). When the `.s2p` file is
back, you can continue in the same conversation, for example:

> **You:** Plot L and Q of L2n0_model.s2p up to 50 GHz.

The assistant can do this with ordinary Python (scikit-rf and matplotlib, if installed), without an MCP tool. Check
the result against something you know, for example the PDK model or a measurement of the same inductor. The
gds2palace examples include such a comparison for another inductor (L6n2) in
`more_examples/measured_vs_simulated/`.

## Things to try next

- "Use the conformal passivation stackup instead and compare the mesh size."
- "Make the chip 100 µm thick." (The assistant uses the stackup variable `total_thickness`, no XML editing.)
- "What changes if I use `conductor_model = 'solve_inside_skin'`?" (The assistant explains it from the guide;
  generating the model shows the cost in mesh size.)
- "Check all model scripts in this folder."
