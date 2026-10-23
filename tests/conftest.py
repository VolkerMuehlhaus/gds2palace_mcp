import os
import shutil
from pathlib import Path

import pytest

import layouts

WORKSPACE = Path(__file__).resolve().parents[2]
GDS2PALACE_REPO = WORKSPACE / "gds2palace_ihp_sg13g2"
GDS_PREPARE_REPO = WORKSPACE / "gds_prepare_for_EM"
LUMPEDMODEL_REPO = WORKSPACE / "lumpedmodel"


@pytest.fixture(scope="session")
def stackup_name():
    """Name of the bundled IHP SG13G2 stackup (the tools resolve it like a path)."""
    return layouts.STACKUP


@pytest.fixture(scope="session")
def pcb_stackup(tmp_path_factory):
    path = tmp_path_factory.mktemp("stackups") / "pcb_ro4003.xml"
    path.write_text(layouts.PCB_STACKUP_XML, encoding="utf-8")
    return str(path)


@pytest.fixture
def work(tmp_path):
    """A fresh working folder per test: the tools write their outputs next to their inputs."""
    return tmp_path


@pytest.fixture(scope="session")
def line_gds(tmp_path_factory):
    return layouts.line_over_ground(tmp_path_factory.mktemp("line") / "line.gds")


@pytest.fixture(scope="session")
def via_array_gds(tmp_path_factory):
    return layouts.via_array(tmp_path_factory.mktemp("vias") / "vias.gds")


@pytest.fixture(scope="session")
def ground_gds(tmp_path_factory):
    return layouts.ground_plane_tiles(tmp_path_factory.mktemp("ground") / "ground.gds")


def _example(path, what):
    if not path.exists():
        pytest.skip(f"{what} not found next to this repository ({path})")
    return path


@pytest.fixture(scope="session")
def gds2palace_examples():
    """Layouts and model scripts of the gds2palace repository (workflow/ and more_examples/)."""
    return _example(GDS2PALACE_REPO, "the gds2palace repository")


@pytest.fixture(scope="session")
def t582_layouts():
    return _example(GDS_PREPARE_REPO / "testcases", "gds_prepare_for_EM testcases")


@pytest.fixture(scope="session")
def lumped_examples():
    return _example(LUMPEDMODEL_REPO, "the lumpedmodel repository")


def write_script(folder, name, body):
    path = Path(folder) / name
    path.write_text(body, encoding="utf-8")
    return str(path)


@pytest.fixture
def copy_to(work):
    """Copy a file into the per-test working folder and return the new path."""
    def copy(source, name=None):
        target = work / (name or os.path.basename(source))
        shutil.copy(source, target)
        return str(target)
    return copy
