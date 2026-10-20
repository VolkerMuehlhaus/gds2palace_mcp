"""Static check of a gds2palace model script: settings keys, ports, GUI."""

import ast
import difflib
import glob
import os
import re

# settings keys that model scripts set by convention, although gds2palace does not read them
CONVENTIONAL_UNREAD_KEYS = {"layernumbers"}

# needed for every model (create_model() reads them without a default)
REQUIRED_KEYS = {"margin", "refined_cellsize", "materials_list", "dielectrics_list", "metals_list",
                 "allpolygons", "sim_path", "model_basename"}

_KEY_RE = re.compile(r"""get_optional_setting\s*\(\s*settings\s*,\s*['"](\w+)['"]"""
                     r"""|settings\s*\[\s*['"](\w+)['"]\s*\]"""
                     r"""|settings\.get\s*\(\s*['"](\w+)['"]""")


def known_settings_keys():
    """Every settings key that the installed gds2palace reads, taken from its source."""
    import gds2palace
    keys = set()
    for source in glob.glob(os.path.join(os.path.dirname(gds2palace.__file__), "*.py")):
        with open(source, encoding="utf-8", errors="replace") as f:
            for match in _KEY_RE.finditer(f.read()):
                keys.add(next(g for g in match.groups() if g))
    return keys


def _settings_key(node):
    """'key' for settings['key'], else None."""
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "settings"
            and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
        return node.slice.value
    return None


def _call_name(node):
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def check_model_script(script_path):
    script_path = os.path.abspath(script_path)
    with open(script_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=script_path)

    assigned = {}        # key -> (line, literal value or None)
    read_by_script = set()
    calls = set()
    attributes = set()
    for node in ast.walk(tree):
        key = _settings_key(node)
        if key is not None:
            if isinstance(node.ctx, ast.Store):
                assigned.setdefault(key, (node.lineno, None))   # literal value is set from the Assign node
            else:
                read_by_script.add(key)
        elif isinstance(node, ast.Call):
            calls.add(_call_name(node))
        elif isinstance(node, ast.Attribute):
            attributes.add(node.attr)
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target_key = _settings_key(node.targets[0])
            if target_key is not None:
                try:
                    assigned[target_key] = (node.lineno, ast.literal_eval(node.value))
                except (ValueError, SyntaxError, TypeError):
                    pass

    known = known_settings_keys()
    errors, warnings, info = [], [], []

    for key, (line, _) in sorted(assigned.items(), key=lambda item: item[1][0]):
        if key in known or key in read_by_script:
            continue
        if key in CONVENTIONAL_UNREAD_KEYS:
            continue
        close = difflib.get_close_matches(key, known, n=2, cutoff=0.6)
        hint = f" Did you mean {' or '.join(repr(c) for c in close)}?" if close else ""
        warnings.append(f"line {line}: settings['{key}'] is not read by gds2palace and not by this script, "
                        f"so it has no effect.{hint}")

    creates_model = calls & {"create_palace", "create_elmer", "create_elmer_thermal", "create_model"}
    thermal = "create_elmer_thermal" in calls or assigned.get("elmer_thermal", (0, False))[1] is True
    if not creates_model:
        warnings.append("The script calls none of create_palace(), create_elmer(), create_elmer_thermal(): "
                        "it does not write a model.")
    else:
        for key in sorted(REQUIRED_KEYS - set(assigned)):
            errors.append(f"settings['{key}'] is not set, but gds2palace needs it.")
        if not thermal and not ({"fstart", "fstop"} <= set(assigned) or {"fpoint", "fdump"} & set(assigned)):
            errors.append("No frequencies: set settings['fstart'] and settings['fstop'], or settings['fpoint'].")
        if not thermal and "simulation_ports" not in assigned:
            errors.append("settings['simulation_ports'] is not set: an EM model needs ports.")

    if "add_port" in calls and "portlayers" not in attributes:
        errors.append("Ports are added, but simulation_ports.portlayers is never used: add it to the layer list "
                      "before read_gds(), or the port shapes are not read.")
    if thermal and "layers" not in attributes and {"add_heatsource", "add_consttemp"} & calls:
        warnings.append("Thermal objects are added, but thermal_objects.layers is never used: add it to the layer "
                        "list before read_gds().")
    if creates_model and not thermal and "create_run_script" not in calls:
        info.append("utilities.create_run_script(sim_path) is not called, so no run_sim script is written.")

    no_gui = assigned.get("no_gui", (0, None))[1]
    if no_gui is not True:
        info.append("settings['no_gui'] is not True: gmsh opens a window when the script runs by hand. "
                    "generate_model sets no_gui itself.")

    filled = assigned.get("filled_metals")
    if filled:
        info.append(f"line {filled[0]}: settings['filled_metals'] is the setting from before gds2palace 0.9.0; "
                    "use settings['conductor_model'] ('surface', 'solve_inside', 'solve_inside_skin').")
    if "z_thickness_factor" in assigned:
        warnings.append(f"line {assigned['z_thickness_factor'][0]}: settings['z_thickness_factor'] was removed in "
                        "gds2palace 0.9.0 and is ignored.")

    return {
        "script": script_path,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "settings": {key: value for key, (line, value) in sorted(assigned.items(), key=lambda i: i[1][0])
                     if value is not None},
        "notes": ["Static check: values computed at run time are not evaluated. "
                  "Known keys come from the installed gds2palace source."],
    }
