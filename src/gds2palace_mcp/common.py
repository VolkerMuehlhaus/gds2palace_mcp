"""Helpers shared by the tools."""

import contextlib
import io
import os


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
