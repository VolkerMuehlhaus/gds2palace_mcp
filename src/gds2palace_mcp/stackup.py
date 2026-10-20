"""Stackup tools: list the bundled stackups, summarize a stackup file."""

import os
import xml.etree.ElementTree

from .common import captured_output, read_stackup


def _round(value, digits=4):
    return None if value is None else round(float(value), digits)


def list_stackups():
    from gds2palace import utilities, stackup_reader
    with captured_output():
        stackup_dir = utilities.get_stackup_dir()
    if stackup_dir is None:
        return {"stackup_dir": None, "stackups": [],
                "note": "This gds2palace installation has no bundled stackups (needs gds2palace 0.9.1 or newer)."}
    stackups = []
    for name in sorted(os.listdir(stackup_dir)):
        if name.endswith('.xml'):
            path = os.path.join(stackup_dir, name)
            stackups.append({"name": name, "path": path,
                             "description": stackup_reader.read_file_description(path)})
    return {"stackup_dir": stackup_dir, "stackups": stackups,
            "note": "Details: README resource gds2palace://stackups/README.md, or inspect_stackup."}


def _variables(path, variable_overrides):
    from gds2palace import stackup_reader
    root = xml.etree.ElementTree.parse(path).getroot()
    variables = stackup_reader.variables_list()
    for data in root.iter("Variable"):
        variables.append(stackup_reader.variable(data))
    with captured_output():
        variables.apply_overrides(variable_overrides)
        variables.resolve_all()
    return {name: (_round(value) if isinstance(value, (int, float)) else value)
            for name, value in variables.as_dict().items()}


def inspect_stackup(stackup_file, variable_overrides=None):
    path, materials_list, dielectrics_list, metals_list = read_stackup(stackup_file, variable_overrides)
    from gds2palace import stackup_reader

    materials = []
    for m in materials_list.materials:
        entry = {"name": m.name, "type": m.type}
        if m.type == "DIELECTRIC":
            entry.update(permittivity=m.eps, loss_tangent=m.tand)
        if m.sigma:
            entry["conductivity_S_per_m"] = m.sigma
        if getattr(m, "Rs", 0):
            entry["sheet_resistance_ohm"] = m.Rs
        if m.thermalcond:
            entry["thermal_conductivity"] = m.thermalcond
        materials.append(entry)

    dielectrics = [{"name": d.name, "material": d.material, "zmin": _round(d.zmin), "zmax": _round(d.zmax),
                    "thickness": _round(d.thickness),
                    **({"gds_boundary_layer": int(d.gdsboundary)} if d.gdsboundary is not None else {})}
                   for d in dielectrics_list.dielectrics]

    layers = [{"name": m.name, "gds_layer": int(m.layernum), "type": m.type.lower(), "material": m.material,
               "zmin": _round(m.zmin), "zmax": _round(m.zmax), "thickness": _round(m.thickness)}
              for m in sorted(metals_list.metals, key=lambda m: (m.zmin, m.zmax))]

    derived = [{"name": d.name, "gds_layer": int(d.layernum), "operation": d.operation,
                "operands": [int(o) for o in d.operands], "oversize": d.oversize}
               for d in metals_list.derived_layers.derived_layers]

    used = set(metals_list.getlayernumbers()) | set(dielectrics_list.get_boundary_layers()) \
        | {int(o) for d in metals_list.derived_layers.derived_layers for o in d.operands}
    free_port_layers = [n for n in range(201, 250) if n not in used][:10]

    return {
        "path": path,
        "description": stackup_reader.read_file_description(path),
        "variables": _variables(path, variable_overrides),
        "materials": materials,
        "dielectrics": dielectrics,
        "layers_bottom_to_top": layers,
        "derived_layers": derived,
        "free_port_layers": free_port_layers,
        "notes": [
            "z values and thicknesses are in model units (settings['unit'], microns for the IHP stackups), resolved with the given variable overrides.",
            "Port and thermal layers must use GDS layer numbers that the stackup does not use; "
            "free_port_layers lists the first free ones from the conventional range 201-249.",
            "gds2palace stackups and gds2openEMS stackups are not interchangeable.",
        ],
    }
