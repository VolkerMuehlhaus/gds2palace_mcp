"""Palace run targets: the user's targets.toml, which says where submit_simulation may run Palace.

The tools only take a target name. Hosts, folders and commands come from this file, which the user writes;
the assistant can't send a model anywhere else.
"""

import os
import re
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

TARGETS_ENV = "GDS2PALACE_MCP_TARGETS"
DEFAULT_FILE = os.path.join("~", ".gds2palace_mcp", "targets.toml")
DOCS = "https://github.com/VolkerMuehlhaus/gds2palace_mcp/blob/main/docs/README.md#running-palace"
DEFAULT_RUN_COMMAND = "run_palace {config}"
DEFAULT_REMOTE_DIR = "palace_runs/mcp"

# option: (target types, required, type)
OPTIONS = {
    "type": (("ssh", "local"), True, str),
    "description": (("ssh", "local"), False, str),
    "run_command": (("ssh", "local"), False, str),
    "host": (("ssh",), True, str),
    "remote_dir": (("ssh",), False, str),
    "wsl_distro": (("local",), False, str),
}
# names that become command line arguments: no spaces, no leading '-' (would be read as an option)
WORD_RE = re.compile(r"^[A-Za-z0-9_.@:+=%/~-]+$")


def targets_path():
    return os.path.expanduser(os.environ.get(TARGETS_ENV) or DEFAULT_FILE)


def _word(name, option, value):
    if not WORD_RE.match(value) or value.startswith("-"):
        raise ValueError(f"Target '{name}': {option} = '{value}' is not a valid name (no spaces, no leading '-').")
    return value


def _check(name, raw):
    kind = raw.get("type")
    if kind not in OPTIONS["type"][0]:
        raise ValueError(f"Target '{name}': type must be one of {list(OPTIONS['type'][0])}, not {kind!r}.")
    for option, value in raw.items():
        if option not in OPTIONS:
            raise ValueError(f"Target '{name}': unknown option '{option}' (known: {', '.join(OPTIONS)}).")
        types, _, value_type = OPTIONS[option]
        if kind not in types:
            raise ValueError(f"Target '{name}': '{option}' is not an option of {kind} targets.")
        if not isinstance(value, value_type):
            raise ValueError(f"Target '{name}': {option} must be a {value_type.__name__}.")
    missing = [o for o, (types, required, _) in OPTIONS.items() if required and kind in types and o not in raw]
    if missing:
        raise ValueError(f"Target '{name}' ({kind}) needs {', '.join(missing)}.")

    target = {"name": name, "type": kind, "description": raw.get("description", ""),
              "run_command": raw.get("run_command", DEFAULT_RUN_COMMAND)}
    if not target["run_command"].strip():
        raise ValueError(f"Target '{name}': run_command is empty.")
    if kind == "ssh":
        target["host"] = _word(name, "host", raw["host"])
        remote_dir = raw.get("remote_dir", DEFAULT_REMOTE_DIR).strip("/")
        if remote_dir.startswith("~") or ".." in remote_dir.split("/") or not remote_dir:
            raise ValueError(f"Target '{name}': remote_dir must be a folder relative to the remote home folder "
                             f"(e.g. '{DEFAULT_REMOTE_DIR}'), without '..'.")
        target["remote_dir"] = "/".join(_word(name, "remote_dir", part) for part in remote_dir.split("/"))
    else:
        target["wsl_distro"] = _word(name, "wsl_distro", raw["wsl_distro"]) if raw.get("wsl_distro") else ""
    return target


def load_targets():
    """(path of the targets file, {name: target}). Raises FileNotFoundError without a targets file."""
    path = targets_path()
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"No Palace targets configured ({path} does not exist). The user writes this file to say where "
            f"Palace may run (a simulation server over ssh, or WSL/local Linux), see {DOCS}. Without it, Palace runs as before: "
            "run_sim in palace_model/<model>_data on the Linux machine.")
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{path} is not valid TOML: {e}") from None
    unknown = [key for key in data if key != "targets"]
    if unknown or not isinstance(data.get("targets"), dict) or not data["targets"]:
        raise ValueError(f"{path}: expected one [targets.<name>] section per target"
                         + (f", found {', '.join(unknown)}" if unknown else "") + ".")
    targets = {}
    for name, raw in data["targets"].items():
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: targets.{name} must be a section [targets.{name}].")
        _word(name, "name", name)
        targets[name] = _check(name, raw)
    return path, targets


def get_target(name=""):
    """The target with this name; without a name, the only configured target."""
    path, targets = load_targets()
    if not name:
        if len(targets) == 1:
            return next(iter(targets.values()))
        raise ValueError(f"Several targets are configured ({', '.join(targets)}): ask the user which one to use.")
    if name not in targets:
        raise ValueError(f"No target '{name}' in {path} (configured: {', '.join(targets)}).")
    return targets[name]
