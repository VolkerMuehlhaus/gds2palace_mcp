"""Port tools: check_ports (geometry against gds2palace's port model), propose_ports, add_ports."""

import ast
import os

from .common import read_stackup

COVERAGE_OK = 0.99            # share of a port line / port end that must lie on metal
EDGE_FRACTION = 0.02          # trace ends: within this share of the signal layers' extent from their edge
GROUND_AREA_FRACTION = 0.10   # a ground shape covers at least this share of the layout area ...
GROUND_FILL = 0.60            # ... and fills at least this share of its bounding box (solid, not a line network)
LABEL_DISTANCE_FRACTION = 0.05  # a label belongs to the nearest metal shape within this share of the layout size
PORT_LAYER_FIRST = 201
PORT_PARAMS = ["portnumber", "voltage", "port_Z0", "source_layernum", "target_layername", "from_layername",
               "to_layername", "direction"]   # simulation_port() argument order


# ---------------------------------------------------------------- geometry helpers

def _layout(gds_file, cellname):
    import gdspy
    from .layout import _select_cell
    library = gdspy.GdsLibrary(infile=os.path.abspath(gds_file))
    cell, note = _select_cell(library, cellname)
    return library, cell, note


def _metal(by_spec, layer):
    """Merged shapes of one layer (datatype 0), or None."""
    import shapely
    from shapely.geometry import Polygon
    polys = [Polygon(p).buffer(0) for (l, d), ps in by_spec.items() if int(l) == layer and int(d) == 0
             for p in ps if len(p) >= 3]
    return shapely.unary_union(polys) if polys else None


def _coverage(line, metal, tol):
    """Share of the line's length that lies on metal (with a small tolerance)."""
    if metal is None or line.length == 0:
        return 0.0
    return min(1.0, line.intersection(metal.buffer(tol)).length / line.length)


def via_port_line(xmin, ymin, xmax, ymax):
    """The line gds2palace makes from a via port shape: along the longer side, at xmin or ymin."""
    from shapely.geometry import LineString
    if ymax - ymin > xmax - xmin:
        return LineString([(xmin, ymin), (xmin, ymax)])
    return LineString([(xmin, ymin), (xmax, ymin)])


# ---------------------------------------------------------------- port definitions from a script

def ports_from_script(script_path):
    """simulation_port(...) calls with literal arguments, as dicts."""
    with open(script_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    ports = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if name != "simulation_port":
            continue
        port = {"line": node.lineno}
        try:
            for i, arg in enumerate(node.args):
                port[PORT_PARAMS[i]] = ast.literal_eval(arg)
            for kw in node.keywords:
                port[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError, IndexError):
            port["unresolved"] = True
        ports.append(port)
    return tree, ports


# ---------------------------------------------------------------- check_ports

def check_ports(gds_file, stackup_file, ports, cellname="", variable_overrides=None):
    _, _, dielectrics_list, metals_list = read_stackup(stackup_file, variable_overrides)
    library, cell, note = _layout(gds_file, cellname)
    tol = 1e-3 * 1e-6 / library.unit          # 1 nm in layout units
    by_spec = cell.get_polygons(by_spec=True)
    by_name = {m.name: m for m in metals_list.metals}
    stackup_layers = {int(m.layernum) for m in metals_list.metals} | set(dielectrics_list.get_boundary_layers())
    derived_outputs = {d.name for d in metals_list.derived_layers.derived_layers}
    conductors = [m for m in metals_list.metals if m.is_metal or m.is_sheet]

    errors, warnings, info, results = [], [], [], []
    numbers, layers = {}, {}
    for port in ports:
        tag = f"port {port.get('portnumber', '?')}" + (f" (script line {port['line']})" if "line" in port else "")
        if port.get("unresolved"):
            info.append(f"{tag}: arguments are not literals, not checked.")
            continue
        number, layer = port.get("portnumber"), port.get("source_layernum")
        if number in numbers:
            errors.append(f"{tag}: port number {number} is used twice.")
        numbers[number] = True
        if layer in layers:
            errors.append(f"{tag}: GDS layer {layer} is used by two ports.")
        layers[layer] = True
        if layer in stackup_layers:
            errors.append(f"{tag}: GDS layer {layer} is a stackup layer; gds2palace would read it as metal, "
                          "not as a port. Use a free layer (201 and up).")
            continue
        if port.get("voltage", 1) == 0:
            info.append(f"{tag}: voltage 0, the port is not excited (its S-parameter rows stay zero).")

        shapes = [p for (l, d), ps in by_spec.items() if int(l) == layer and int(d) == 0 for p in ps]
        if not shapes:
            errors.append(f"{tag}: no shape on GDS layer {layer} (datatype 0).")
            continue
        if len(shapes) > 1:
            warnings.append(f"{tag}: {len(shapes)} shapes on GDS layer {layer}; gds2palace uses only one of them "
                            "(the last one read). Use one shape per port layer.")
        xmin, ymin = shapes[-1].min(axis=0)
        xmax, ymax = shapes[-1].max(axis=0)
        entry = {"portnumber": number, "source_layernum": layer,
                 "shape": [round(float(v), 4) for v in (xmin, ymin, xmax, ymax)]}
        direction = str(port.get("direction", "")).lower()

        if port.get("target_layername") is not None:          # in-plane port
            target = by_name.get(port["target_layername"])
            entry["type"] = "in-plane"
            if target is None:
                errors.append(f"{tag}: target_layername '{port['target_layername']}' is not in the stackup.")
                results.append(entry)
                continue
            if direction not in ("x", "-x", "y", "-y"):
                errors.append(f"{tag}: an in-plane port needs direction x, -x, y or -y, not '{direction}'.")
            if xmax - xmin <= 0 or ymax - ymin <= 0:
                errors.append(f"{tag}: an in-plane port needs a rectangle with an area.")
                results.append(entry)
                continue
            from shapely.geometry import LineString, box
            metal = _metal(by_spec, int(target.layernum))
            if "x" in direction:
                ends = [LineString([(xmin, ymin), (xmin, ymax)]), LineString([(xmax, ymin), (xmax, ymax)])]
            else:
                ends = [LineString([(xmin, ymin), (xmax, ymin)]), LineString([(xmin, ymax), (xmax, ymax)])]
            cover = [_coverage(e, metal, tol) for e in ends]
            entry["end_coverage"] = [round(c, 3) for c in cover]
            for side, c in zip(("start", "end"), cover):
                if c == 0:
                    errors.append(f"{tag}: the {side} edge of the port (along {direction}) touches no "
                                  f"{target.name} metal: the port is not connected there.")
                elif c < COVERAGE_OK:
                    warnings.append(f"{tag}: only {c:.0%} of the {side} edge touches {target.name} metal.")
            if metal is not None:
                overlap = box(xmin, ymin, xmax, ymax).intersection(metal).area / ((xmax - xmin) * (ymax - ymin))
                entry["metal_overlap"] = round(overlap, 3)
                if overlap > 0.01:
                    warnings.append(f"{tag}: {overlap:.0%} of the port rectangle overlaps {target.name} metal; "
                                    "an in-plane port normally fills a gap between two metal ends.")
        else:                                                   # via port
            entry["type"] = "via"
            names = [port.get("from_layername"), port.get("to_layername")]
            missing = [n for n in names if n not in by_name]
            if missing:
                errors.append(f"{tag}: layer(s) {missing} not in the stackup.")
                results.append(entry)
                continue
            if names[0] == names[1]:
                errors.append(f"{tag}: from_layername and to_layername are the same.")
                results.append(entry)
                continue
            if direction not in ("z", "-z"):
                errors.append(f"{tag}: a via port needs direction z or -z, not '{direction}'.")
            if xmax - xmin > 0 and ymax - ymin > 0:
                info.append(f"{tag}: drawn with an area; gds2palace uses only its "
                            f"{'left' if ymax - ymin > xmax - xmin else 'bottom'} edge (xmin/ymin), not the "
                            "center line. Draw via ports as lines.")
            line = via_port_line(xmin, ymin, xmax, ymax)
            entry["port_line"] = [round(float(v), 4) for v in (*line.coords[0], *line.coords[1])]
            ends = sorted((by_name[n] for n in names), key=lambda m: m.zmin)
            lower, upper = ends
            entry["coverage"] = {}
            for metal_layer in (lower, upper):
                if metal_layer.name in derived_outputs:
                    info.append(f"{tag}: {metal_layer.name} is a derived layer, its shapes are not checked.")
                    continue
                c = _coverage(line, _metal(by_spec, int(metal_layer.layernum)), tol)
                entry["coverage"][metal_layer.name] = round(c, 3)
                if c == 0:
                    errors.append(f"{tag}: the port line has no {metal_layer.name} metal at its position: "
                                  "the port is not connected to that layer.")
                elif c < COVERAGE_OK:
                    warnings.append(f"{tag}: only {c:.0%} of the port line lies on {metal_layer.name} metal.")
            for m in conductors:
                if lower.zmax < m.zmin and m.zmax < upper.zmin:
                    c = _coverage(line, _metal(by_spec, int(m.layernum)), tol)
                    if c > 0:
                        warnings.append(f"{tag}: the port sheet crosses {m.name} metal between {lower.name} and "
                                        f"{upper.name} ({c:.0%} of the line).")
        results.append(entry)

    out = {"gds_file": os.path.abspath(gds_file), "cell": cell.name, "ok": not errors, "errors": errors,
           "warnings": warnings, "info": info, "ports": results,
           "notes": ["Via port: gds2palace turns the shape's bounding box into a line along its longer side, at "
                     "xmin or ymin, and a vertical sheet from the top of the lower metal to the bottom of the "
                     "upper one: the line must lie on metal of both layers. In-plane port: a rectangle in the "
                     "target metal's plane; current flows along direction, so both ends along it must touch metal."]}
    if note:
        out["notes"].insert(0, note)
    return out


# ---------------------------------------------------------------- propose_ports

def _top_signal_layers(metals_list, drawn, count=2):
    layers = []
    for m in sorted((m for m in metals_list.metals if m.is_metal), key=lambda m: -m.zmax):
        if int(m.layernum) in drawn and all(int(m.layernum) != int(x.layernum) for x in layers):
            layers.append(m)
        if len(layers) == count:
            break
    return layers


def _is_ground(part, layout_area):
    """A ground plane: large and solid (a network of lines can be large too, but not solid)."""
    bx0, by0, bx1, by1 = part.bounds
    box_area = (bx1 - bx0) * (by1 - by0)
    return part.area >= GROUND_AREA_FRACTION * layout_area and box_area > 0 and part.area / box_area >= GROUND_FILL


def _reference_for(line, signal, metals_list, by_spec, layout_area, tol):
    """Closest metal layer below the signal layer with a large ground shape under the whole line, else SUBGND."""
    candidates = sorted((m for m in metals_list.metals if (m.is_metal or m.is_sheet) and m.zmax <= signal.zmin),
                        key=lambda m: -m.zmax)
    for m in candidates:
        metal = _metal(by_spec, int(m.layernum))
        if metal is None:
            continue
        parts = list(metal.geoms) if hasattr(metal, "geoms") else [metal]
        for part in parts:
            if _is_ground(part, layout_area) and _coverage(line, part, tol) >= COVERAGE_OK:
                return m.name, f"large {m.name} shape under the port"
    subgnd = next((m for m in metals_list.metals if m.name.upper() == "SUBGND"), None)
    if subgnd is not None:
        covered = _coverage(line, _metal(by_spec, int(subgnd.layernum)), tol) >= COVERAGE_OK
        return subgnd.name, ("SUBGND shape under the port" if covered else
                             "no ground metal below; SUBGND has no shape under the port yet: draw SUBGND "
                             f"(GDS layer {int(subgnd.layernum)}) under the port, or choose another reference")
    return None, "no ground metal below the port found: choose the reference layer"


def propose_ports(gds_file, stackup_file, cellname="", variable_overrides=None):
    import numpy as np
    from shapely.geometry import LineString, Point, box

    _, _, dielectrics_list, metals_list = read_stackup(stackup_file, variable_overrides)
    library, cell, note = _layout(gds_file, cellname)
    tol = 1e-3 * 1e-6 / library.unit
    by_spec = cell.get_polygons(by_spec=True)
    drawn = {int(l) for (l, d), ps in by_spec.items() if int(d) == 0 and len(ps)}
    stackup_layers = {int(m.layernum) for m in metals_list.metals} | set(dielectrics_list.get_boundary_layers())
    conductor_layers = {int(m.layernum): m for m in metals_list.metals if m.is_metal}

    existing = sorted(l for l in drawn if l >= PORT_LAYER_FIRST and l not in stackup_layers)
    pts = np.vstack([p for (l, d), ps in by_spec.items() if int(l) in stackup_layers and int(d) == 0 for p in ps])
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    layout_area = (x1 - x0) * (y1 - y0)

    size = max(x1 - x0, y1 - y0)
    skipped = []

    def parts_of(layer):
        metal = _metal(by_spec, layer)
        return [] if metal is None else (list(metal.geoms) if hasattr(metal, "geoms") else [metal])

    def is_ground(part):
        return _is_ground(part, layout_area)

    def nearest_edge(part, point):
        """The boundary edge of a metal shape closest to a point (a line end next to its label)."""
        coords = np.asarray(part.exterior.coords)
        edges = [LineString([a, b]) for a, b in zip(coords[:-1], coords[1:]) if not np.allclose(a, b)]
        return min(edges, key=lambda e: e.distance(point))

    candidates = []
    # 1. pins (IHP: datatype 2 on the metal layer) and text labels on conductor layers; markings on
    #    ground-sized shapes (ground pins, title texts) are skipped
    labels = [lb for lb in cell.get_labels() if int(lb.layer) in conductor_layers]
    for (l, d), ps in by_spec.items():
        if int(l) not in conductor_layers or int(d) != 2:
            continue
        for p in ps:
            bx0, by0 = p.min(axis=0)
            bx1, by1 = p.max(axis=0)
            pin = box(bx0, by0, bx1, by1)
            name = next((lb.text for lb in labels if int(lb.layer) == int(l)
                         and pin.buffer(tol).contains(Point(lb.position))), None)
            under = [g for g in parts_of(int(l)) if g.intersects(pin)]
            if under and all(is_ground(g) for g in under):
                skipped.append(f"pin {name or ''} on a ground shape of {conductor_layers[int(l)].name}".replace("  ", " "))
                continue
            candidates.append({"source": "pin", "name": name, "signal": conductor_layers[int(l)],
                               "bbox": (bx0, by0, bx1, by1)})
    # overlapping pins on one layer (e.g. nested pad cells) are one port candidate: keep the largest
    merged = []
    for c in sorted((c for c in candidates if c["source"] == "pin"),
                    key=lambda c: -(c["bbox"][2] - c["bbox"][0]) * (c["bbox"][3] - c["bbox"][1])):
        b = box(*c["bbox"])
        same = next((m for m in merged if m["signal"] is c["signal"] and box(*m["bbox"]).intersects(b)), None)
        if same is None:
            c["names"] = [c["name"]] if c["name"] else []
            merged.append(c)
        elif c["name"] and c["name"] not in same["names"]:
            same["names"].append(c["name"])
    for c in merged:
        c["name"] = "/".join(c.pop("names")) or None
    candidates = merged + [c for c in candidates if c["source"] != "pin"]
    pinned = [c["bbox"] for c in candidates if c["source"] == "pin"]
    for lb in labels:
        point = Point(lb.position)
        if any(box(*b).buffer(tol).contains(point) for b in pinned):
            continue
        parts = parts_of(int(lb.layer))
        if not parts:
            continue
        part = min(parts, key=lambda g: g.distance(point))
        if part.distance(point) > LABEL_DISTANCE_FRACTION * size:
            continue
        if is_ground(part):
            skipped.append(f"label '{lb.text}' on a ground shape of {conductor_layers[int(lb.layer)].name}")
            continue
        edge = nearest_edge(part, point)
        candidate = {"source": "label", "name": lb.text, "signal": conductor_layers[int(lb.layer)]}
        if edge.length <= 0.2 * size and not part.buffer(tol).contains(point):
            candidate["line"] = (*edge.coords[0], *edge.coords[1])     # label beside a line end
        else:
            candidate["bbox"] = part.bounds
        candidates.append(candidate)

    # 2. without pins or labels: open trace ends at the outer edge of the top signal layers
    if not candidates:
        # extent of the signal shapes on the top layers: ground planes left out, since they usually
        # reach beyond the feed line ends
        signal_parts = []
        for m in _top_signal_layers(metals_list, drawn):
            metal = _metal(by_spec, int(m.layernum))
            for part in (list(metal.geoms) if hasattr(metal, "geoms") else [metal]):
                if not is_ground(part):
                    signal_parts.append((m, part))
        other_metal = {int(m.layernum): [p for mm, p in signal_parts if mm is not m]
                       for m, _ in signal_parts}
        # via shapes touching each top layer: an end next to a via is a connection, not an open end
        vias_at = {}
        for m, _ in signal_parts:
            near_vias = [v for v in metals_list.metals if v.is_via and (abs(v.zmax - m.zmin) < 1e-6 or
                                                                        abs(v.zmin - m.zmax) < 1e-6)]
            vias_at[int(m.layernum)] = [g for v in near_vias for g in parts_of(int(v.layernum))]
        if signal_parts:
            bounds = np.array([part.bounds for _, part in signal_parts])
            sx0, sy0 = bounds[:, 0].min(), bounds[:, 1].min()
            sx1, sy1 = bounds[:, 2].max(), bounds[:, 3].max()
            size = max(sx1 - sx0, sy1 - sy0)
            near = EDGE_FRACTION * size
        for m, part in signal_parts:
            coords = np.asarray(part.exterior.coords)
            for a, b in zip(coords[:-1], coords[1:]):
                seg = LineString([a, b])
                if seg.length < 1000 * tol or seg.length > 0.2 * size:
                    continue
                horizontal, vertical = abs(a[1] - b[1]) < tol, abs(a[0] - b[0]) < tol
                at_edge = ((horizontal and (abs(a[1] - sy0) <= near or abs(a[1] - sy1) <= near)) or
                           (vertical and (abs(a[0] - sx0) <= near or abs(a[0] - sx1) <= near)))
                # an end under or over metal of the other top layer is a via connection, not an open end
                covered = any(seg.intersection(o.buffer(tol)).length > 0.5 * seg.length
                              for o in other_metal[int(m.layernum)])
                covered = covered or any(v.distance(seg) <= seg.length for v in vias_at[int(m.layernum)])
                if at_edge and not covered:
                    candidates.append({"source": "trace end", "name": None, "signal": m,
                                       "line": (float(a[0]), float(a[1]), float(b[0]), float(b[1]))})

    proposals = []
    for c in candidates:
        if "line" in c:
            xa, ya, xb, yb = c["line"]
        else:   # across the pin/shape at its center, along its shorter side (across the trace)
            bx0, by0, bx1, by1 = c["bbox"]
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            if bx1 - bx0 <= by1 - by0:
                xa, ya, xb, yb = bx0, cy, bx1, cy
            else:
                xa, ya, xb, yb = cx, by0, cx, by1
        line = LineString([(xa, ya), (xb, yb)])
        reference, why = _reference_for(line, c["signal"], metals_list, by_spec, layout_area, tol)
        proposals.append({"source": c["source"], "name": c["name"], "signal_layer": c["signal"].name,
                          "reference_layer": reference, "reference_note": why,
                          "line": [round(float(v), 4) for v in (xa, ya, xb, yb)]})

    # numbers: from the labels if they are all different integers, else left to right, bottom to top
    names = [p["name"] for p in proposals]
    if names and all(isinstance(n, str) and n.strip().isdigit() for n in names) and len(set(names)) == len(names):
        proposals.sort(key=lambda p: int(p["name"]))
        numbers = [int(p["name"]) for p in proposals]
    else:
        proposals.sort(key=lambda p: ((p["line"][0] + p["line"][2]) / 2, (p["line"][1] + p["line"][3]) / 2))
        numbers = list(range(1, len(proposals) + 1))
    free = [l for l in range(PORT_LAYER_FIRST, PORT_LAYER_FIRST + 200) if l not in stackup_layers and l not in drawn]
    for i, (p, number) in enumerate(zip(proposals, numbers)):
        p["portnumber"] = number
        p["source_layernum"] = free[i]
        p["add_port"] = (f"simulation_ports.add_port(simulation_setup.simulation_port(portnumber={number}, voltage=1, "
                         f"port_Z0=50, source_layernum={free[i]}, from_layername='{p['reference_layer']}', "
                         f"to_layername='{p['signal_layer']}', direction='z'))")

    result = {"gds_file": os.path.abspath(gds_file), "cell": cell.name, "proposals": proposals,
              "notes": ["Proposals only: show them to the user (preview_layout with the proposed lines, or "
                        "describe them) and let them confirm port positions, references and Z0 (default 50 ohm) "
                        "before add_ports writes them. Via ports from the reference (from_layername) up to the "
                        "signal metal (to_layername), drawn as lines."]}
    if skipped:
        result["skipped"] = skipped
    if len(proposals) > 8:
        result["notes"].insert(0, f"{len(proposals)} candidates: pins of pad cells or test structures are probably "
                                  "among them. Ask the user which ones are the ports.")
    if existing:
        result["notes"].insert(0, f"The layout already has shapes on layers {existing}, probably ports: check them "
                                  "with check_ports instead of adding new ones.")
    if not proposals:
        result["notes"].insert(0, "No pins, labels or open trace ends at the layout edge found: ask the user "
                                  "where the ports are.")
    if note:
        result["notes"].insert(0, note)
    return result


# ---------------------------------------------------------------- add_ports

def add_ports(gds_file, ports, output_file="", cellname=""):
    """ports: [{"portnumber": n, "line": [x1, y1, x2, y2], "source_layernum": optional}], zero-width lines."""
    import gdspy

    gds_file = os.path.abspath(gds_file)
    if not output_file:
        base, ext = os.path.splitext(gds_file)
        output_file = f"{base}_ports{ext or '.gds'}"
    output_file = os.path.abspath(output_file)
    if os.path.normcase(output_file) == os.path.normcase(gds_file):
        raise ValueError("output_file must differ from gds_file: the original layout is never overwritten.")
    library, cell, note = _layout(gds_file, cellname)
    used = {int(l) for (l, d) in cell.get_polygons(by_spec=True)}
    written = []
    for port in ports:
        n = int(port["portnumber"])
        layer = int(port.get("source_layernum") or (PORT_LAYER_FIRST - 1 + n))
        if layer in used:
            raise ValueError(f"GDS layer {layer} already has shapes in the layout: choose another source_layernum "
                             f"for port {n}.")
        xa, ya, xb, yb = (float(v) for v in port["line"])
        if xa != xb and ya != yb:
            raise ValueError(f"Port {n}: a via port line must be horizontal or vertical.")
        cell.add(gdspy.Rectangle((min(xa, xb), min(ya, yb)), (max(xa, xb), max(ya, yb)), layer=layer, datatype=0))
        used.add(layer)
        written.append({"portnumber": n, "source_layernum": layer, "line": [xa, ya, xb, yb]})
    library.write_gds(output_file)
    return {"output_file": output_file, "cell": cell.name, "ports": written,
            "notes": ["The original layout is unchanged. Use output_file as gds_filename, add the add_port lines "
                      "to the script, then run check_ports."] + ([note] if note else [])}
