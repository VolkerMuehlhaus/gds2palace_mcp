"""Small layouts, stackup and Touchstone fixtures with known answers, generated in the tests.

IHP SG13G2 layer numbers (SG13G2_FEM_200um.xml, bundled with gds2palace): Metal1 8, TopMetal1 126,
TopVia2 133, TopMetal2 134, SUBGND 250; ports on 201 and up.
"""

import gdspy
import numpy as np

STACKUP = "SG13G2_FEM_200um.xml"
METAL1, TOPMETAL1, TOPVIA2, TOPMETAL2 = 8, 126, 133, 134

# a non-IHP technology: 2 copper layers on RO4003 (as gds2palace's pcb example); units are microns
PCB_STACKUP_XML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Stackup schemaVersion="2.0">
  <Materials>
    <Material Name="Copper" Type="Conductor" Permittivity="1" DielectricLossTangent="0" Conductivity="3.3e7" Color="00ff00"/>
    <Material Name="RO4003" Type="Dielectric" Permittivity="3.38" DielectricLossTangent="0.0022" Conductivity="0" Color="01e0ff"/>
    <Material Name="AIR" Type="Dielectric" Permittivity="1.0" DielectricLossTangent="0.0" Conductivity="0" Color="d0d0d0"/>
  </Materials>
  <ELayers LengthUnit="um">
    <Dielectrics>
      <Dielectric Name="RO4003" Material="RO4003" Thickness="510" />
    </Dielectrics>
    <Layers>
      <Substrate Offset="0"/>
      <Layer Name="Bottom" Type="Conductor" Zmin="-17" Zmax="0" Material="Copper" Layer="1" />
      <Layer Name="Top" Type="Conductor" Zmin="510" Zmax="527" Material="Copper" Layer="10" />
    </Layers>
  </ELayers>
</Stackup>
"""


def _write(cell, path):
    library = gdspy.GdsLibrary()
    library.add(cell)
    library.write_gds(str(path))
    return str(path)


def _new_cell(name):
    return gdspy.Cell(name, exclude_from_current=True)


def line_over_ground(path, width=15.0, length=800.0, port_ref_x=None, ports=True, labels=None):
    """TopMetal2 line over a Metal1 ground plane (960 x 220 for the defaults), via port lines on 201 and 202
    at the line ends. port_ref_x: x of port 1 (default: the line's left end); a different value moves it off
    the line. ports=False leaves them out. labels: (left, right) texts on TopMetal2 beside the line ends."""
    cell = _new_cell("LINE")
    x0, x1 = 0.0, length
    cell.add(gdspy.Rectangle((x0 - 80, -110), (x1 + 80, 110), layer=METAL1))
    cell.add(gdspy.Rectangle((x0, -width / 2), (x1, width / 2), layer=TOPMETAL2))
    if ports:
        px = x0 if port_ref_x is None else port_ref_x
        cell.add(gdspy.Rectangle((px, -width / 2), (px, width / 2), layer=201))
        cell.add(gdspy.Rectangle((x1, -width / 2), (x1, width / 2), layer=202))
    for text, x in zip(labels or (), (x0 + 5, x1 - 5)):
        cell.add(gdspy.Label(text, (x, 0), layer=TOPMETAL2, texttype=25))
    return _write(cell, path)


def inplane_gap(path, shift_port=0.0):
    """Two TopMetal2 pads with a 10 um gap and an in-plane port rectangle (layer 201) across the gap.
    shift_port moves the port rectangle in y, off the pads."""
    cell = _new_cell("GAP")
    cell.add(gdspy.Rectangle((0, 0), (100, 20), layer=TOPMETAL2))
    cell.add(gdspy.Rectangle((110, 0), (210, 20), layer=TOPMETAL2))
    cell.add(gdspy.Rectangle((100, shift_port), (110, 20 + shift_port), layer=201))
    return _write(cell, path)


def pcb_lines(path, width=500.0, gap=300.0, length=20000.0):
    """Two coupled traces on the PCB 'Top' layer (GDS layer 10), micron units."""
    cell = _new_cell("PCB")
    cell.add(gdspy.Rectangle((0, 0), (length, width), layer=10))
    cell.add(gdspy.Rectangle((0, width + gap), (length, 2 * width + gap), layer=10))
    return _write(cell, path)


def small_patch(path, size=100.0, layer=10):
    """One short square: no trace and no coupled lines."""
    cell = _new_cell("PATCH")
    cell.add(gdspy.Rectangle((0, 0), (size, size), layer=layer))
    return _write(cell, path)


def coupled_lines(path, gap=2.0, width=8.0, length=400.0):
    """Two parallel TopMetal2 traces; the layout is as large as the traces are long."""
    cell = _new_cell("COUPLED")
    cell.add(gdspy.Rectangle((0, 0), (length, width), layer=TOPMETAL2))
    cell.add(gdspy.Rectangle((0, width + gap), (length, 2 * width + gap), layer=TOPMETAL2))
    return _write(cell, path)


def via_array(path, columns=10, rows=10, pitch=1.4, size=0.9, hierarchical=True):
    """TopMetal1 and TopMetal2 squares joined by a TopVia2 array; the array is a sub cell reference."""
    side = columns * pitch + 4
    top = _new_cell("VIAS")
    top.add(gdspy.Rectangle((0, 0), (side, side), layer=TOPMETAL1))
    top.add(gdspy.Rectangle((0, 0), (side, side), layer=TOPMETAL2))
    via = _new_cell("VIA")
    via.add(gdspy.Rectangle((0, 0), (size, size), layer=TOPVIA2))
    if hierarchical:
        top.add(gdspy.CellArray(via, columns, rows, (pitch, pitch), origin=(2, 2)))
        library = gdspy.GdsLibrary()
        library.add(top)
        library.add(via)
        library.write_gds(str(path))
    else:
        for i in range(columns):
            for j in range(rows):
                top.add(gdspy.Rectangle((2 + i * pitch, 2 + j * pitch), (2 + i * pitch + size, 2 + j * pitch + size),
                                        layer=TOPVIA2))
        _write(top, path)
    return str(path)


def ground_plane_tiles(path, tiles=10, pitch=20.0, hole=8.0, missing=((3, 3), (3, 4), (4, 3), (4, 4)), slots=25):
    """Metal1 ground plane of tiles (half with a hole drawn inside, half as four strips whose opening appears
    only when merged), a 40 x 40 um circuit opening where 4 tiles are missing, and a TopMetal1 signal line with
    slots. Known: 48 + 48 = 96 repeated openings of hole x hole, 1 other opening, 24 holes outside ground planes."""
    cell = _new_cell("GROUND")
    h0, h1 = (pitch - hole) / 2, (pitch + hole) / 2
    for i in range(tiles):
        for j in range(tiles):
            if (i, j) in missing:
                continue
            x, y = i * pitch, j * pitch
            if (i + j) % 2 == 0:
                cell.add(gdspy.boolean(gdspy.Rectangle((x, y), (x + pitch, y + pitch)),
                                       gdspy.Rectangle((x + h0, y + h0), (x + h1, y + h1)), "not", layer=METAL1))
            else:
                for a, b in [((0, 0), (pitch, h0)), ((0, h1), (pitch, pitch)), ((0, h0), (h0, h1)),
                             ((h1, h0), (pitch, h1))]:
                    cell.add(gdspy.Rectangle((x + a[0], y + a[1]), (x + b[0], y + b[1]), layer=METAL1))
    line = gdspy.Rectangle((20, 205), (170, 210))
    cut = [gdspy.Rectangle((25 + 6 * k, 207), (26 + 6 * k, 208)) for k in range(slots)]
    cell.add(gdspy.boolean(line, cut, "not", layer=TOPMETAL1))
    return _write(cell, path)


def with_subcell_design(path):
    """A design cell that only references a sub cell (the sub cell must not stay a top-level cell after
    simplification)."""
    return via_array(path, hierarchical=True)


# ------------------------------------------------------------------------------ Touchstone files

def series_network(path, kind="rl", resistance=0.5, inductance=1e-9, capacitance=100e-15, f_start=1e8, f_stop=100e9,
                   points=1001):
    """2-port Touchstone file of a single series element (known R, L or C); returns the path."""
    import skrf as rf

    f = np.linspace(f_start, f_stop, points)
    w = 2 * np.pi * f
    if kind == "rl":
        z = resistance + 1j * w * inductance
    elif kind == "rc":
        z = resistance + 1 / (1j * w * capacitance)
    elif kind == "rlc":          # series resonance: L and C in series, with R
        z = resistance + 1j * w * inductance + 1 / (1j * w * capacitance)
    else:
        raise ValueError(kind)
    z0 = 50.0
    s = np.zeros((len(f), 2, 2), dtype=complex)
    s[:, 0, 0] = s[:, 1, 1] = z / (z + 2 * z0)
    s[:, 0, 1] = s[:, 1, 0] = 2 * z0 / (z + 2 * z0)
    network = rf.Network(frequency=rf.Frequency.from_f(f, unit="hz"), s=s, z0=z0)
    path = str(path)
    network.write_touchstone(path[:-4] if path.lower().endswith(".s2p") else path, write_z0=True)
    return path


def gain_network(path, gain=1.1):
    """Matched 2-port with |S21| = gain (not passive)."""
    import skrf as rf

    f = np.linspace(1e9, 10e9, 10)
    s = np.zeros((len(f), 2, 2), dtype=complex)
    s[:, 1, 0] = gain
    network = rf.Network(frequency=rf.Frequency.from_f(f, unit="hz"), s=s, z0=50.0)
    path = str(path)
    network.write_touchstone(path[:-4], write_z0=True)
    return path


# ------------------------------------------------------------------------------ model scripts

VIA_PORTS = """simulation_ports.add_port(simulation_setup.simulation_port(portnumber=1, voltage=1, port_Z0=50, source_layernum=201,
                          from_layername='Metal1', to_layername='TopMetal2', direction='z'))
simulation_ports.add_port(simulation_setup.simulation_port(portnumber=2, voltage=1, port_Z0=50, source_layernum=202,
                          from_layername='Metal1', to_layername='TopMetal2', direction='z'))
"""


def model_script(gds_name, extra_settings="", ports=VIA_PORTS, cellsize=5, xml=STACKUP, fstop="20e9"):
    """A complete gds2palace model script (as in AGENTS.md); extra_settings are more settings lines."""
    return f"""import os, sys
from gds2palace import *

gds_filename = "{gds_name}"
XML_filename = utilities.get_stackup_path("{xml}")
cellname = ""
merge_polygon_size = 0

script_path = utilities.get_script_path(__file__)
model_basename = utilities.get_basename(__file__)
sim_path = utilities.create_sim_path(script_path, model_basename)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

settings = {{}}
settings['unit'] = 1e-6
settings['margin'] = 50
settings['fstart'] = 0
settings['fstop'] = {fstop}
settings['fstep'] = 5e9
settings['refined_cellsize'] = {cellsize}
settings['order'] = 2
settings['no_gui'] = True
{extra_settings}
simulation_ports = simulation_setup.all_simulation_ports()
{ports}
materials_list, dielectrics_list, metals_list = stackup_reader.read_substrate(XML_filename)
layernumbers = metals_list.getlayernumbers()
layernumbers.extend(simulation_ports.portlayers)
allpolygons = gds_reader.read_gds(gds_filename, layernumbers, purposelist=[0], metals_list=metals_list,
                                  merge_polygon_size=merge_polygon_size,
                                  gds_boundary_layers=dielectrics_list.get_boundary_layers(), cellname=cellname)

settings['simulation_ports'] = simulation_ports
settings['materials_list'] = materials_list
settings['dielectrics_list'] = dielectrics_list
settings['metals_list'] = metals_list
settings['layernumbers'] = layernumbers
settings['allpolygons'] = allpolygons
settings['sim_path'] = sim_path
settings['model_basename'] = model_basename

excite_ports = simulation_ports.all_active_excitations()
config_name, data_dir = simulation_setup.create_palace(excite_ports, settings)
utilities.create_run_script(sim_path)
"""
