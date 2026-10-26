"""Running Palace on a target: targets file, commands, log parsing, and the full submit/status/fetch flow
with a fake run_palace (local target) and a fake ssh (ssh target) on Linux."""

import io
import json
import os
import sys
import tarfile
import time

import pytest

from gds2palace_mcp import palace_run, targets

LINUX = sys.platform.startswith("linux")
needs_linux = pytest.mark.skipif(not LINUX, reason="runs bash, ps and /proc like a Linux target")


def write_targets(tmp_path, monkeypatch, text):
    path = tmp_path / "targets.toml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv(targets.TARGETS_ENV, str(path))
    return path


# ---------- targets file ----------

def test_targets_file_with_defaults(tmp_path, monkeypatch):
    write_targets(tmp_path, monkeypatch, """
[targets.server]
type = "ssh"
host = "hpz2"
description = "16 cores"

[targets.wsl]
type = "local"
wsl_distro = "Ubuntu-24.04"
run_command = "~/scripts/run_palace {config}"
""")
    path, found = targets.load_targets()
    assert set(found) == {"server", "wsl"}
    assert found["server"] == {"name": "server", "type": "ssh", "description": "16 cores",
                               "run_command": "run_palace {config}", "shared": False, "host": "hpz2",
                               "remote_dir": "palace_runs/mcp"}
    assert found["wsl"]["wsl_distro"] == "Ubuntu-24.04" and found["wsl"]["run_command"].startswith("~/scripts")
    assert targets.get_target("wsl")["type"] == "local"
    with pytest.raises(ValueError, match="Several targets.*ask the user"):
        targets.get_target("")
    with pytest.raises(ValueError, match="No target 'x'.*server, wsl"):
        targets.get_target("x")


def test_the_only_target_needs_no_name(tmp_path, monkeypatch):
    write_targets(tmp_path, monkeypatch, '[targets.wsl]\ntype = "local"\n')
    assert targets.get_target("")["name"] == "wsl"


@pytest.mark.parametrize("text, message", [
    ('[targets.a]\ntype = "ssh"\n', "needs host"),
    ('[targets.a]\ntype = "slurm"\nhost = "h"\n', "type must be one of"),
    ('[targets.a]\ntype = "local"\nhost = "h"\n', "'host' is not an option of local"),
    ('[targets.a]\ntype = "ssh"\nhost = "h"\nnp = 16\n', "unknown option 'np'"),
    ('[targets.a]\ntype = "ssh"\nhost = "-oProxyCommand=evil"\n', "not a valid name"),
    ('[targets.a]\ntype = "ssh"\nhost = "a b"\n', "not a valid name"),
    ('[targets.a]\ntype = "ssh"\nhost = "h"\nremote_dir = "../x"\n', "relative to the remote home"),
    ('[targets.a]\ntype = "ssh"\nhost = "h"\nremote_dir = "~/x"\n', "relative to the remote home"),
    ('[targets.a]\ntype = "ssh"\nhost = "h"\nshared = "yes"\n', "shared must be a bool"),
    ('[targets.a]\ntype = "local"\nrun_command = " "\n', "run_command is empty"),
    ('[server]\ntype = "ssh"\n', r"\[targets.<name>\]"),
    ('[targets.a\n', "not valid TOML"),
])
def test_bad_targets_files_are_explained(tmp_path, monkeypatch, text, message):
    write_targets(tmp_path, monkeypatch, text)
    with pytest.raises(ValueError, match=message):
        targets.load_targets()


def test_without_targets_file_the_old_way_is_explained(tmp_path, monkeypatch):
    monkeypatch.setenv(targets.TARGETS_ENV, str(tmp_path / "none.toml"))
    with pytest.raises(FileNotFoundError, match="No Palace targets configured.*run_sim"):
        palace_run.list_targets()


# ---------- helpers ----------

def test_wsl_paths():
    assert palace_run._wsl_path(r"D:\github\proj\palace_model\m_data") == "/mnt/d/github/proj/palace_model/m_data"
    with pytest.raises(ValueError, match="local drive"):
        palace_run._wsl_path(r"\\server\share\m_data")


def test_sections_ignore_what_the_login_profile_prints():
    text = "Welcome to hpz2\n@@program\n/usr/bin/run_palace\n@@memory\n100 50\r\n@@empty\n"
    assert palace_run._sections(text) == {"program": ["/usr/bin/run_palace"], "memory": ["100 50"], "empty": []}


PS = """\
  101 anna   1000000 101 palace-x86_64.b
  102 anna   1000000 102 palace-x86_64.b
  201 volker  500000 201 palace-x86_64.b
  200 volker    3000 150 bash
  300 ben       2000 300 sshd
"""


def test_palace_processes_count_and_name_users_only():
    processes = palace_run._processes(PS.splitlines())
    assert palace_run._palace_processes(processes) == {
        "processes": 3, "ram_gb": 2.5, "processes_by_user": {"anna": 2, "volker": 1}}
    others = palace_run._palace_processes(processes, exclude_pids={"201", "200"})
    assert others == {"processes": 2, "ram_gb": 2.0, "processes_by_user": {"anna": 2}}


LOG = """\
Running with 8 MPI processes
Adding excitation index 1 (1/2):
Greedy iteration 1 (n = 4): ω* = 2.716e+01 GHz (4.986e-01), error = 7.573e-03, memory = 1/2
It 3/12: ω/2π = 3.000e+01 GHz (total elapsed time = 1.2e+01 s, solve 3/4)
Adaptive mesh refinement (AMR) iteration 1: Indicator norm = 1e-2, global unknowns = 73726
Adding excitation index 2 (2/2):
"""


def test_progress_starts_again_with_each_amr_iteration_and_port():
    lines = LOG.splitlines()
    progress = palace_run._progress(lines[:1], lines[1:])
    assert progress == {"mpi_processes": 8, "amr_iteration": 1, "port": "2/2",
                        "phase": "building the reduced-order model (adaptive sweep)"}
    sweep = palace_run._progress([], ["Sweeping excitation index 1 (1/2):",
                                      "It 7/50: ω/2π = 7.000e+00 GHz (total elapsed time = 1.60e+03 s)"])
    assert sweep == {"phase": "frequency sweep", "port": "1/2", "frequency_step": "7/50", "frequency_ghz": 7.0}


def test_queue_line_of_the_shared_run_palace():
    waiting = palace_run.QUEUE_WAITING + ": all 1 slot(s) in use, running: anna (16 processes)"
    assert palace_run._queue_line(["It 1/2:", waiting]) == waiting
    assert palace_run._queue_line([waiting, palace_run.QUEUE_GOT + " 1 of 1"]).startswith(palace_run.QUEUE_GOT)
    assert palace_run._queue_line(["It 1/2:"]) is None


def test_ram_check():
    estimate = {"peak_ram_gb": {"typical": 10, "worst_case": 14},
                "amr": {"max_unknowns": 2000000, "peak_ram_gb_at_max_unknowns": {"typical": 51, "worst_case": 63}}}
    assert "will likely fail" in palace_run._ram_check(estimate, {"ram_available_gb": 8})[0]
    warnings = palace_run._ram_check(estimate, {"ram_available_gb": 12})
    assert "worst-case" in warnings[0] and "max_unknowns (2,000,000)" in warnings[1]
    assert palace_run._ram_check(estimate, {"ram_available_gb": 100}) == []
    assert palace_run._ram_check(estimate, {}) == []


def test_extract_keeps_only_plain_files_inside_the_folder(tmp_path):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        for name in ("palace.log", "output/m/port-S.csv", "output/m/paraview/field.pvtu", "../evil.txt"):
            info = tarfile.TarInfo(name)
            info.size = 2
            tar.addfile(info, io.BytesIO(b"ok"))
        link = tarfile.TarInfo("output/m/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(link)
    data.seek(0)
    names = palace_run._extract(data, str(tmp_path / "run"), include_fields=False)
    assert names == ["palace.log", "output/m/port-S.csv"]
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "run/output/m/link").exists()
    data.seek(0)
    assert "output/m/paraview/field.pvtu" in palace_run._extract(data, str(tmp_path / "run2"), include_fields=True)


# ---------- a generated model ----------

MSH = """\
$MeshFormat
2.2 0 8
$EndMeshFormat
$Nodes
4
1 0 0 0
2 1 0 0
3 0 1 0
4 0 0 1
$EndNodes
$Elements
1
1 4 2 1 1 1 2 3 4
$EndElements
"""


def palace_model(folder, name="line"):
    """A palace_model/<name>_data folder as generate_model leaves it (config, mesh, port information)."""
    run = folder / "proj" / "palace_model" / f"{name}_data"
    run.mkdir(parents=True)
    (run / "config.json").write_text(json.dumps({
        "Problem": {"Type": "Driven", "Output": f"output/{name}"}, "Model": {"Mesh": f"{name}.msh"},
        "Solver": {"Order": 2, "Linear": {}}}), encoding="utf-8")
    (run / f"{name}.msh").write_text(MSH, encoding="ascii")
    (run / "port_information.json").write_text(json.dumps(
        {"name": name, "ports": [{"portnumber": 1, "Z0": 50}, {"portnumber": 2, "Z0": 50}]}), encoding="utf-8")
    return run


def test_model_files_and_errors(tmp_path):
    run = palace_model(tmp_path)
    model = palace_run._model(str(run))
    assert model["config"] == "config.json" and model["mesh"] == "line.msh" and model["output"] == "output/line"
    assert model["port_information"] == "port_information.json" and model["model"] == "line"
    script = tmp_path / "proj" / "line.py"
    assert palace_run._model(str(script))["run_dir"] == str(run)
    (run / "line.msh").unlink()
    with pytest.raises(FileNotFoundError, match="mesh line.msh is missing"):
        palace_run._model(str(run))
    (run / "config.json").unlink()
    with pytest.raises(FileNotFoundError, match="Elmer models"):
        palace_run._model(str(run))


# ---------- the full flow on Linux ----------

PORT_S = """\
        f (GHz),             |S[1][1]| (dB),        arg(S[1][1]) (deg.),             |S[2][1]| (dB),        arg(S[2][1]) (deg.),             |S[1][2]| (dB),        arg(S[1][2]) (deg.),             |S[2][2]| (dB),        arg(S[2][2]) (deg.)
 1.00000000e+00,        -3.000000000000e+01,        +1.000000000000e+01,        -1.000000000000e-01,        -1.000000000000e+01,        -1.000000000000e-01,        -1.000000000000e+01,        -3.000000000000e+01,        +1.000000000000e+01
 2.00000000e+00,        -2.900000000000e+01,        +2.000000000000e+01,        -1.100000000000e-01,        -2.000000000000e+01,        -1.100000000000e-01,        -2.000000000000e+01,        -2.900000000000e+01,        +2.000000000000e+01
"""


def fake_palace(folder, seconds=1, exit_code=0, shared=False):
    """A run_palace that behaves like Palace: a 'palace-x86_64.bin' process that writes port-S.csv and
    palace.json to Problem.Output. shared: queues like run_palace_shared (1 slot)."""
    bin_dir = folder / "fakebin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    palace = bin_dir / "palace-x86_64.bin"
    palace.write_text(f"""#!/bin/bash
out=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["Problem"]["Output"])' "$1")
mkdir -p "$out"
echo "Running with 2 MPI processes"
echo "Sweeping excitation index 1 (1/2):"
echo "It 1/2: ω/2π = 1.000e+00 GHz (total elapsed time = 1.0e+00 s)"
sleep {seconds}
cat > "$out/port-S.csv" <<'EOF'
{PORT_S}EOF
echo '{{"Problem": {{"DegreesOfFreedom": 1000, "MeshElements": 1}}, "ElapsedTime": {{"Durations": {{"Total": 1.0}}}}}}' > "$out/palace.json"
exit {exit_code}
""", encoding="utf-8", newline="\n")
    run_palace = bin_dir / "run_palace"
    queue = (f'echo "{palace_run.QUEUE_WAITING}: all 1 slot(s) in use, running: anna (16 processes)"\nsleep 2\n'
             f'echo "{palace_run.QUEUE_GOT} 1 of 1 (2 MPI processes)"\n') if shared else ""
    run_palace.write_text(f"#!/bin/bash\n# {palace_run.SHARED_MARK if shared else 'run_palace'}\n{queue}"
                          f'exec "{palace}" "$1"\n', encoding="utf-8", newline="\n")
    for path in (palace, run_palace):
        path.chmod(0o755)
    return run_palace


def fake_ssh(folder, monkeypatch):
    """An ssh on PATH that runs the remote command locally, in a fake remote home folder."""
    home = folder / "remote_home"
    home.mkdir()
    bin_dir = folder / "sshbin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(f"""#!/bin/bash
# ssh [-o option]... [-t] host command
while [ $# -gt 0 ]; do
  case "$1" in
    -o) shift 2 ;;
    -t|-n) shift ;;
    *) break ;;
  esac
done
host="$1"; shift
[ "$host" = "unreachable" ] && {{ echo "ssh: connect to host unreachable: Connection refused" >&2; exit 255; }}
cd "{home}" && exec bash -c "$*"
""", encoding="utf-8", newline="\n")
    ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return home


def wait_for(path, seconds=30):
    for _ in range(seconds * 10):
        if path.exists() and path.read_text().strip():
            return
        time.sleep(0.1)
    raise AssertionError(f"{path} was not written")


@needs_linux
def test_local_run_submit_status_fetch(tmp_path, monkeypatch):
    run_palace = fake_palace(tmp_path, seconds=3)
    write_targets(tmp_path, monkeypatch, f'[targets.here]\ntype = "local"\nrun_command = "{run_palace} {{config}}"\n')
    run = palace_model(tmp_path)

    r = palace_run.submit_simulation(str(run), "", False, True)
    assert r["submitted"] and r["type"] == "local" and r["log_window"] == "test window (not opened)"
    assert r["estimate"]["unknowns_range"][0] >= 0 and r["warnings"] == []
    wrapper = (run / palace_run.WRAPPER).read_bytes()
    assert b"\r\n" not in wrapper and f"export {palace_run.JOB_ENV}={r['job_id']}".encode() in wrapper

    time.sleep(1)
    status = palace_run.simulation_status(str(run))
    assert status["state"] == "running" and status["palace_processes"] == 1
    assert status["progress"]["mpi_processes"] == 2 and status["progress"]["frequency_step"] == "1/2"
    assert status["other_palace_processes"]["processes"] == 0       # our own process is not counted as other
    with pytest.raises(ValueError, match="still running.*partial=True"):
        palace_run.fetch_results(str(run))
    with pytest.raises(ValueError, match="still running"):
        palace_run.submit_simulation(str(run), "", True, False)

    wait_for(run / palace_run.EXIT_FILE)
    status = palace_run.simulation_status(str(run))
    assert status["state"] == "finished" and status["exit_code"] == 0 and "fetch_results" in status["note"]
    assert f"run {r['job_id']} finished" in status["log_tail"][-1] and status["log_tail"][-1].endswith("exit code 0")

    fetched = palace_run.fetch_results(str(run))
    assert fetched["combine_snp"]["returncode"] == 0
    assert [os.path.basename(f) for f in fetched["touchstone_files"]][0] == "line.s2p"
    assert fetched["summary"][0]["model"] == "line"
    assert json.loads((run / palace_run.JOB_FILE).read_text())["fetched"]

    with pytest.raises(ValueError, match="results of an earlier run.*overwrite=True"):
        palace_run.submit_simulation(str(run), "", False, False)
    again = palace_run.submit_simulation(str(run), "", True, False)
    assert again["job_id"] != r["job_id"] and not (run / "output" / "line" / "line.s2p").exists()
    wait_for(run / palace_run.EXIT_FILE)


@needs_linux
def test_failed_run_and_busy_target(tmp_path, monkeypatch):
    run_palace = fake_palace(tmp_path, seconds=2, exit_code=3)
    write_targets(tmp_path, monkeypatch, f'[targets.here]\ntype = "local"\nrun_command = "{run_palace} {{config}}"\n')
    first, second = palace_model(tmp_path / "a"), palace_model(tmp_path / "b")
    palace_run.submit_simulation(str(first), "here", False, False)
    time.sleep(0.5)
    with pytest.raises(ValueError, match="already running on target 'here'.*one run at a time"):
        palace_run.submit_simulation(str(second), "here", False, False)
    wait_for(first / palace_run.EXIT_FILE)
    status = palace_run.simulation_status(str(first))
    assert status["state"] == "failed" and status["exit_code"] == 3 and "log_tail" in status["note"]


@needs_linux
def test_shared_target_queues_and_checks_for_the_shared_run_palace(tmp_path, monkeypatch):
    plain = fake_palace(tmp_path / "plain", seconds=1)
    write_targets(tmp_path, monkeypatch, f'[targets.lab]\ntype = "local"\nshared = true\n'
                                         f'run_command = "{plain} {{config}}"\n')
    run = palace_model(tmp_path)
    with pytest.raises(ValueError, match="marked shared.*run_palace_shared"):
        palace_run.submit_simulation(str(run), "", False, False)

    shared = fake_palace(tmp_path / "shared", seconds=1, shared=True)
    write_targets(tmp_path, monkeypatch, f'[targets.lab]\ntype = "local"\nshared = true\n'
                                         f'run_command = "{shared} {{config}}"\n')
    r = palace_run.submit_simulation(str(run), "", False, False)
    assert "waits in the simulation server's queue" in r["note"]
    time.sleep(0.7)
    status = palace_run.simulation_status(str(run))
    assert status["state"] == "queued" and "running: anna" in status["queue"]
    wait_for(run / palace_run.EXIT_FILE)
    assert palace_run.simulation_status(str(run))["state"] == "finished"


@needs_linux
def test_ssh_run_is_private_and_fetched_back(tmp_path, monkeypatch):
    run_palace = fake_palace(tmp_path, seconds=1)
    home = fake_ssh(tmp_path, monkeypatch)
    write_targets(tmp_path, monkeypatch, f'[targets.server]\ntype = "ssh"\nhost = "sim"\n'
                                         f'run_command = "{run_palace} {{config}}"\n')
    run = palace_model(tmp_path)
    r = palace_run.submit_simulation(str(run), "server", False, True)
    remote = home / r["remote_dir"]
    assert r["remote_dir"].startswith("palace_runs/mcp/") and r["remote_dir"].endswith(f"/proj/line_{r['job_id']}")
    assert {"config.json", "line.msh", "port_information.json", "mcp_run.sh"} <= set(os.listdir(remote))
    assert oct(remote.stat().st_mode & 0o777) == "0o700" and oct((remote / "line.msh").stat().st_mode & 0o777) == "0o600"
    assert not (run / "output").exists()            # nothing local until fetch

    wait_for(remote / palace_run.EXIT_FILE)
    assert palace_run.simulation_status(str(run))["state"] == "finished"
    fetched = palace_run.fetch_results(str(run))
    assert fetched["files_copied"] >= 4 and (run / "palace.log").exists()
    assert [os.path.basename(f) for f in fetched["touchstone_files"]][0] == "line.s2p"
    assert remote.exists() and "delete_remote=True" in fetched["notes"][-1]
    palace_run.fetch_results(str(run), delete_remote=True)
    assert not remote.exists()
    assert palace_run.simulation_status(str(run))["state"] == "missing"


@needs_linux
def test_unreachable_ssh_target_is_reported(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch)
    write_targets(tmp_path, monkeypatch, '[targets.off]\ntype = "ssh"\nhost = "unreachable"\n')
    checked = palace_run.list_targets(check=True)["targets"][0]["check"]
    assert checked["reachable"] is False and "passwordless" in checked["error"]
    with pytest.raises(OSError, match="ssh to unreachable failed"):
        palace_run.submit_simulation(str(palace_model(tmp_path)), "", False, False)
