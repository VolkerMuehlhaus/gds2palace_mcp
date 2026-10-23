"""Helpers shared by the tools."""

import contextlib
import io
import os
import shlex
import shutil
import subprocess
import sys


class Gds2palaceError(Exception):
    """gds2palace stopped (exit() or exception) while a tool called it."""


@contextlib.contextmanager
def captured_output():
    """Run gds2palace code without writing to stdout.

    With the stdio transport, stdout carries the MCP protocol, and gds2palace
    prints progress and errors with print(). Some of its readers also call
    exit() on bad input. Both are caught here; the printed text is kept in
    the yielded buffer so it can go into the tool result or the error.
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            yield buffer
    except SystemExit as e:
        raise Gds2palaceError(f"gds2palace stopped (exit code {e.code}):\n{buffer.getvalue().strip()}") from None


def resolve_stackup(stackup_file):
    """Path of a stackup file: an existing path, or the name of a stackup that
    ships with gds2palace (e.g. 'SG13G2_FEM_200um.xml')."""
    if os.path.isfile(stackup_file):
        return os.path.abspath(stackup_file)
    from gds2palace import utilities
    try:
        return utilities.get_stackup_path(os.path.basename(stackup_file))
    except FileNotFoundError as e:
        raise FileNotFoundError(f"{stackup_file} is not a file. {e}") from None


def read_stackup(stackup_file, variable_overrides=None):
    """read_substrate() with captured output. Returns path, materials, dielectrics, metals."""
    from gds2palace import stackup_reader
    path = resolve_stackup(stackup_file)
    with captured_output():
        materials_list, dielectrics_list, metals_list = stackup_reader.read_substrate(path, variable_overrides)
    return path, materials_list, dielectrics_list, metals_list


def has_display():
    """Can a window open here? Windows and macOS always; Linux needs an X11 or Wayland display."""
    return sys.platform in ("win32", "darwin") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_window(command, cwd=None, env=None):
    """Start a program that opens a window on the user's screen, detached from this server: it keeps
    running after the tool call returns. Raises OSError if there is no display (Linux without X11/Wayland)."""
    if not has_display():
        raise OSError("No display (DISPLAY/WAYLAND_DISPLAY not set): a window can't open on this computer.")
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "cwd": cwd, "env": env}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def open_log_window(log_path, title):
    """Open a terminal window on the user's screen that shows a log file live (it keeps updating while the
    file grows). Returns which window was opened, or raises OSError if none can be opened."""
    if not has_display():
        raise OSError("no display (DISPLAY/WAYLAND_DISPLAY not set)")
    detached = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        quote = lambda text: "'" + text.replace("'", "''") + "'"
        command = f"$host.UI.RawUI.WindowTitle = {quote(title)}; Get-Content -LiteralPath {quote(log_path)} -Wait"
        subprocess.Popen(["powershell", "-NoProfile", "-NoExit", "-Command", command],
                         creationflags=subprocess.CREATE_NEW_CONSOLE, **detached)
        return "PowerShell window"
    tail = f"tail -n +1 -f {shlex.quote(log_path)}"
    if sys.platform == "darwin":
        script = 'tell application "Terminal" to do script "{}"'.format(tail.replace("\\", "\\\\").replace('"', '\\"'))
        subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'],
                         start_new_session=True, **detached)
        return "Terminal window"
    failed = []
    for terminal in ("x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"):
        if shutil.which(terminal) is None:
            continue
        if terminal == "gnome-terminal":
            command = [terminal, "--title", title, "--", "sh", "-c", tail]
        elif terminal == "xfce4-terminal":
            command = [terminal, "-T", title, "-x", "sh", "-c", tail]
        else:
            command = [terminal, "-e", "sh", "-c", tail]
        process = subprocess.Popen(command, start_new_session=True, **detached)
        if terminal in ("gnome-terminal", "xfce4-terminal"):     # these hand the window to a server and exit
            return f"{terminal} window"
        try:                                    # tail -f never ends: a terminal that exits at once has failed
            process.wait(timeout=1)
            failed.append(terminal)
        except subprocess.TimeoutExpired:
            return f"{terminal} window"
    if failed:
        raise OSError(f"the terminal program closed at once ({', '.join(failed)})")
    raise OSError("no terminal program found (x-terminal-emulator, gnome-terminal, konsole, xfce4-terminal, xterm)")
