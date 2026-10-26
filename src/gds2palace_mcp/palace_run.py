"""Run Palace on a configured target: list_targets, submit_simulation, simulation_status, fetch_results.

Targets come from the user's targets.toml (targets.py): a simulation server over ssh, or the local machine (WSL
on Windows, Linux). A run is described by its job record <run folder>/mcp_palace_job.json, so status and fetch
work after a restart of the MCP server. A target used by one person refuses to start while Palace runs there. A
simulation server shared by several users needs gds2palace's run_palace_shared installed as its run_palace: it queues every run (also
those started from setupEM or a terminal) until one of its slots is free. This MCP server never stops a run.
"""

import datetime
import getpass
import io
import json
import ntpath
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

from . import results
from .common import model_dir, open_log_window
from .model_run import _msh_counts, estimate_resources
from .targets import DOCS, get_target, load_targets

JOB_FILE = "mcp_palace_job.json"
WRAPPER = "mcp_run.sh"
LOG = "palace.log"
EXIT_FILE = "palace_exit_code"
SESSION_FILE = "palace_session"
RUN_FILES = (WRAPPER, LOG, EXIT_FILE, SESSION_FILE)
PALACE_PROCESS = "palace-x86_64"     # one process per MPI rank; ps shows it as palace-x86_64.b
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
SSH_DOCS = DOCS.replace("#running-palace", "#login-to-the-simulation-server-without-a-password")
TAIL_LINES = 15
NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
# what gds2palace's run_palace_shared writes to the log while it waits for a slot, and when it got one
SHARED_MARK = "run_palace_shared"
QUEUE_WAITING = "[run_palace] waiting for a free Palace slot"
QUEUE_GOT = "[run_palace] got Palace slot"

# Runs Palace in its run folder on the target, with the log in palace.log and the exit code in
# palace_exit_code. Status finds this run's processes, and only these (other users' Palace processes on the
# same machine are not ours), by the wrapper's process session and by the job id in the environment, which
# all processes started from here inherit: MPI starts each Palace process in a session of its own.
JOB_ENV = "GDS2PALACE_MCP_JOB"
WRAPPER_SCRIPT = """\
#!/bin/bash
# Written by gds2palace_mcp (submit_simulation): runs Palace for the model in this folder.
cd "$(dirname "$0")" || exit 1
umask 077
exec > {log} 2>&1 < /dev/null
ps -o sid= -p $$ | tr -d ' ' > {session}
export {job_env}={job_id}
export OMP_NUM_THREADS=1
echo "[gds2palace_mcp] Palace run {job_id} started $(date '+%Y-%m-%d %H:%M:%S') on $(hostname). Closing a window that shows this log does not stop the run."
time {command}
code=$?
echo "[gds2palace_mcp] Palace run {job_id} finished $(date '+%Y-%m-%d %H:%M:%S'), exit code $code"
echo $code > {exit_file}
"""

# Palace log lines that show progress (the same lines setupEM's status bar reads), and the queue lines
PROGRESS_GREP = ("excitation index|It [0-9]+/[0-9]+:|adaptive mesh refinement|Adaptive mesh refinement|"
                 r"Proceeding with solve/estimate iteration|Greedy iteration|^\[run_palace\]")
MPI_RE = re.compile(r"Running with (\d+) MPI processes")
EXCITATION_RE = re.compile(r"(Sweeping|Adding) excitation index \d+ \((\d+)/(\d+)\):")
FREQUENCY_STEP_RE = re.compile(r"It (\d+)/(\d+):")
FREQUENCY_RE = re.compile(r"(?:ω/2π|ω\*) = ([0-9.eE+-]+) GHz")
AMR_RE = re.compile(r"Completed (\d+) iteration.*adaptive mesh refinement \(AMR\)|"
                    r"Adaptive mesh refinement \(AMR\) iteration (\d+):|"
                    r"Proceeding with solve/estimate iteration (\d+)")
GREEDY_RE = re.compile(r"Greedy iteration (\d+) \(n = (\d+)\):")


# ---------- the model ----------

def _model(path):
    """Run folder, config, mesh and output folder of a generated Palace model."""
    run = model_dir(path)
    if not os.path.isdir(run):
        raise FileNotFoundError(f"{run} does not exist: run generate_model first.")
    configs = sorted(name for name in os.listdir(run) if re.fullmatch(r"config.*\.json", name)
                     and name not in ("config_resolved.json", "config_original.json"))
    if "config.json" in configs:
        config = "config.json"
    elif len(configs) == 1:
        config = configs[0]
    elif not configs:
        raise FileNotFoundError(f"No config.json in {run}: run generate_model first. Elmer models (no config.json) "
                                "can't be run with these tools.")
    else:
        raise ValueError(f"Several Palace configs in {run} ({', '.join(configs)}) and no config.json.")
    with open(os.path.join(run, config), encoding="utf-8") as f:
        settings = json.load(f)
    mesh = settings.get("Model", {}).get("Mesh", "")
    output = settings.get("Problem", {}).get("Output", "output").rstrip("/")
    for name, value in (("Model.Mesh", mesh), ("Problem.Output", output)):
        if not value or os.path.isabs(value) or ".." in re.split(r"[\\/]", value):
            raise ValueError(f"{config}: {name} = '{value}' must be a path inside the run folder, as gds2palace "
                             "writes it.")
    if not os.path.isfile(os.path.join(run, mesh)):
        raise FileNotFoundError(f"The mesh {mesh} is missing in {run}: run generate_model first.")
    suffix = config[len("config"):-len(".json")]
    port_information = f"port_information{suffix}.json"
    return {"run_dir": run, "model": os.path.basename(run).removesuffix("_data"), "config": config, "mesh": mesh,
            "output": output,
            "port_information": port_information if os.path.isfile(os.path.join(run, port_information)) else None}


def _estimate(model):
    try:
        counts = _msh_counts(os.path.join(model["run_dir"], model["mesh"]))
        return estimate_resources(counts["tetrahedra"], os.path.join(model["run_dir"], model["config"]))
    except (OSError, ValueError, IndexError, KeyError):
        return None


def _job_file(run):
    return os.path.join(run, JOB_FILE)


def _read_job(path):
    run = model_dir(path)
    try:
        with open(_job_file(run), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"No Palace run was submitted for {run} (no {JOB_FILE}): submit_simulation "
                                "first.") from None


def _write_job(job):
    with open(_job_file(job["run_dir"]), "w", encoding="utf-8") as f:
        json.dump(job, f, indent=1)


# ---------- commands on the target ----------

def _wsl_path(path):
    """/mnt/<drive>/... path of a Windows path, as WSL sees it."""
    if sys.platform == "win32":
        path = os.path.abspath(path)
    drive, rest = ntpath.splitdrive(path)
    if not re.fullmatch(r"[A-Za-z]:", drive):
        raise ValueError(f"{path} is not on a local drive: WSL runs need the model on a local drive (C:, D:, ...), "
                         "not on a network path.")
    return f"/mnt/{drive[0].lower()}" + rest.replace("\\", "/")


def _local_bash(target, args, cwd=None):
    """Command line that runs bash on a local target: in WSL on Windows, directly on Linux."""
    if sys.platform == "win32":
        command = ["wsl.exe"] + (["-d", target["wsl_distro"]] if target["wsl_distro"] else [])
        return command + (["--cd", _wsl_path(cwd)] if cwd else []) + ["--", "bash", *args]
    if sys.platform == "darwin":
        raise OSError("Local targets need Linux, or WSL on Windows: Palace doesn't run on macOS. Use an ssh target.")
    return ["bash", *args]


def _ssh(host, remote_command, tty=False):
    return ["ssh", *SSH_OPTIONS, *(["-t"] if tty else []), host, remote_command]


def _target_dir(job):
    """The run folder as the target's bash sees it."""
    if job["target"]["type"] == "ssh":
        return job["remote_dir"]
    return _wsl_path(job["run_dir"]) if sys.platform == "win32" else job["run_dir"]


def _run_script(target, script, timeout=120):
    """Run a bash script on the target as a login shell (PATH from ~/.profile, as setupEM and the installers
    use it). The script goes through stdin, so nothing is quoted through ssh or wsl.exe. Returns stdout."""
    if target["type"] == "local":
        command = _local_bash(target, ["-l", "-s"])
    else:
        command = _ssh(target["host"], "bash -l -s")
    env = dict(os.environ, WSL_UTF8="1") if command[0] == "wsl.exe" else None
    try:
        result = subprocess.run(command, input=script.encode("utf-8"), capture_output=True, timeout=timeout,
                                env=env)
    except FileNotFoundError:
        raise OSError(f"{command[0]} was not found on this computer"
                      + (" (WSL is not installed)." if command[0] == "wsl.exe" else ".")) from None
    except subprocess.TimeoutExpired:
        raise OSError(f"Target '{target['name']}' did not answer within {timeout} s.") from None
    stderr = result.stderr.decode("utf-8", "replace").strip()
    if target["type"] == "ssh" and result.returncode == 255:
        raise OSError(f"ssh to {target['host']} failed: {stderr}. This MCP server uses passwordless ssh (BatchMode): "
                      f"'ssh -o BatchMode=yes {target['host']} true' must work without any question (ssh key; the "
                      f"server's fingerprint confirmed once in a terminal). How to set it up: {SSH_DOCS}")
    if command[0] == "wsl.exe" and result.returncode and not result.stdout:
        raise OSError(f"WSL failed (exit code {result.returncode}): {stderr}")
    return result.stdout.decode("utf-8", "replace")


def _sections(text):
    """Output of a target script, split at its '@@name' marker lines. Text before the first marker (e.g.
    printed by the user's ~/.profile) is ignored."""
    sections, current = {}, None
    for line in text.splitlines():
        if re.fullmatch(r"@@\w+", line.strip()):
            current = line.strip()[2:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line.rstrip("\r"))
    return sections


PS_COMMAND = "ps -eo pid=,user=,rss=,sid=,comm= 2>/dev/null"


def _processes(lines):
    """Lines of PS_COMMAND as (pid, user, rss in kB, session, name)."""
    processes = []
    for line in lines:
        parts = line.split(None, 4)
        if len(parts) == 5 and parts[0].isdigit():
            processes.append((parts[0], parts[1], int(parts[2]) if parts[2].isdigit() else 0, parts[3], parts[4]))
    return processes


def _palace_processes(processes, exclude_pids=()):
    """Palace processes: count, RAM and count per user. Only counts and user names: other users' folders and
    model names never leave the target."""
    by_user, count, rss_kb = {}, 0, 0
    for pid, user, rss, _, name in processes:
        if name.startswith(PALACE_PROCESS) and pid not in exclude_pids:
            count += 1
            rss_kb += rss
            by_user[user] = by_user.get(user, 0) + 1
    return {"processes": count, "ram_gb": round(rss_kb / 1e6, 1), "processes_by_user": by_user}


def _probe(target):
    """Is the target reachable, is its run command there (and the shared queue, for a shared target), how much
    RAM is free, and which Palace processes run there now?"""
    program = target["run_command"].split()[0]
    script = (f'echo "@@program"\ncommand -v {program} || true\n'
              f'echo "@@shared"\ngrep -l {SHARED_MARK} "$(command -v {program})" 2>/dev/null || true\n'
              # /proc/meminfo, not free: its labels are translated (de: "Speicher:")
              'echo "@@memory"\nawk \'/^MemTotal:/ {t = $2} /^MemAvailable:/ {a = $2} END {print t * 1024, a * 1024}\' '
              '/proc/meminfo\n'
              f'echo "@@processes"\n{PS_COMMAND}\n')
    sections = _sections(_run_script(target, script))
    probe = {"reachable": True, "run_command": program,
             "run_command_found": any(l.strip() for l in sections.get("program", [])),
             "shared_queue_found": any(l.strip() for l in sections.get("shared", []))}
    memory = (sections.get("memory") or [""])[0].split()
    if len(memory) == 2 and all(m.isdigit() for m in memory):
        probe["ram_total_gb"], probe["ram_available_gb"] = (round(int(m) / 1e9, 1) for m in memory)
    probe["palace_running"] = _palace_processes(_processes(sections.get("processes", [])))
    return probe


# ---------- tools ----------

def list_targets(check=False):
    path, targets = load_targets()
    entries = []
    for target in targets.values():
        entry = dict(target)
        if check:
            try:
                entry["check"] = _probe(target)
            except (OSError, ValueError) as e:
                entry["check"] = {"reachable": False, "error": str(e)}
        entries.append(entry)
    return {"targets_file": path, "targets": entries,
            "notes": ["A target with shared = false is for one user: it refuses to start while Palace runs there. "
                      "A shared simulation server needs gds2palace's run_palace_shared as its run_palace (shared = true): it "
                      "queues the runs of all users.",
                      "Palace's process count (-np) and container are set in the target's run_palace, not here."]}


def _ram_check(estimate, probe):
    if not estimate:
        return ["No RAM estimate (the mesh could not be read)."]
    available = probe.get("ram_available_gb")
    if available is None:
        return []
    peak = estimate["peak_ram_gb"]
    if peak["typical"] > available:
        return [f"The typical RAM estimate ({peak['typical']} GB) is above the RAM available on the target now "
                f"({available:g} GB): the run will likely fail or swap."]
    warnings = []
    if peak["worst_case"] > available:
        warnings.append(f"The worst-case RAM estimate ({peak['worst_case']} GB) is above the RAM available on the "
                        f"target now ({available:g} GB).")
    amr = estimate.get("amr", {}).get("peak_ram_gb_at_max_unknowns")
    if amr and amr["typical"] > available:
        warnings.append(f"With AMR, the mesh may grow up to max_unknowns ({estimate['amr']['max_unknowns']:,}); "
                        f"at that size the run would need about {amr['typical']} GB, more than the {available:g} GB "
                        "available. It only gets there if AMR runs until amr_max_dof.")
    return warnings


def _clear_local(model):
    run = model["run_dir"]
    for path in [os.path.join(run, model["output"])] + [os.path.join(run, name) for name in RUN_FILES]:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)


def _start_local(job):
    run = job["run_dir"]
    options = {"cwd": run, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    command = _local_bash(job["target"], ["-l", WRAPPER], cwd=run)
    if sys.platform != "win32":
        return subprocess.Popen(command, start_new_session=True, **options).pid
    # wsl.exe stays alive while the run lasts (that keeps WSL from stopping); detached from this MCP server, and
    # out of its job object where Windows allows that, so that it survives the MCP server.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        return subprocess.Popen(command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options).pid
    except OSError:
        return subprocess.Popen(command, creationflags=flags, **options).pid


def _private(info):
    info.mode = 0o700 if info.name == WRAPPER else 0o600
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _upload(job, model, wrapper):
    """Copy config, mesh, port information and the wrapper to the target's run folder (tar over ssh, private:
    umask 077)."""
    remote = shlex.quote(job["remote_dir"])
    command = _ssh(job["target"]["host"], f"umask 077 && mkdir -p {remote} && cd {remote} && tar xf -")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdin, mode="w|") as tar:
            for name in filter(None, (model["config"], model["mesh"], model["port_information"])):
                tar.add(os.path.join(model["run_dir"], name), arcname=name.replace("\\", "/"), filter=_private)
            data = wrapper.encode("utf-8")
            info = _private(tarfile.TarInfo(WRAPPER))
            info.size, info.mtime = len(data), time.time()
            tar.addfile(info, io.BytesIO(data))
        process.stdin.close()
    except OSError:          # ssh ended early (e.g. login failed): its message is on stderr
        pass
    stderr = process.stderr.read().decode("utf-8", "replace").strip()
    if process.wait() != 0:
        raise OSError(f"Copying the model to {job['target']['host']} failed: {stderr}")


def submit_simulation(path, target_name="", overwrite=False, show_log=True):
    target = get_target(target_name)
    model = _model(path)
    run = model["run_dir"]

    previous = None
    if os.path.isfile(_job_file(run)):
        previous = _read_job(run)
        try:
            state = _status(previous)["state"]
        except (OSError, ValueError) as e:
            state = f"unknown ({e})"
        if state in ("queued", "running"):
            raise ValueError(f"The previous run of this model ({previous['job_id']} on {previous['target']['name']}) "
                             f"is still {state}: see simulation_status. This MCP server never stops a run.")
        if not previous.get("fetched") and previous["target"]["type"] == "ssh" and not overwrite:
            raise ValueError(f"The previous run of this model ({previous['job_id']} on {previous['target']['name']}, "
                             f"{state}) was not fetched yet: fetch_results first, or ask the user whether to "
                             "submit anyway (overwrite=True).")
    existing = [name for name in [model["output"]] + list(RUN_FILES) if os.path.exists(os.path.join(run, name))]
    if existing and not overwrite:
        raise ValueError(f"{run} has results of an earlier run ({', '.join(existing)}). Ask the user whether to "
                         "replace them, then submit with overwrite=True.")

    probe = _probe(target)
    if not probe["run_command_found"]:
        raise OSError(f"'{probe['run_command']}' was not found on target '{target['name']}' (login shell PATH). "
                      "Check run_command in the targets file, or install Palace there (gds2palace installers).")
    running = probe["palace_running"]
    if target["shared"] and not probe["shared_queue_found"]:
        raise ValueError(f"Target '{target['name']}' is marked shared, but its {probe['run_command']} is not "
                         "gds2palace's run_palace_shared, which queues the runs of all users. Ask the simulation server's "
                         "administrator to install it; until then, runs could overload the machine.")
    if not target["shared"] and running["processes"]:
        raise ValueError(f"Palace is already running on target '{target['name']}' ({running['processes']} "
                         f"processes, {running['ram_gb']} GB; by user: {running['processes_by_user']}). This "
                         "target is for one run at a time: wait until it finished, or use another target. (A simulation server "
                         "shared by several users needs run_palace_shared and shared = true.)")
    estimate = _estimate(model)
    warnings = _ram_check(estimate, probe)

    _clear_local(model)
    job_id = uuid.uuid4().hex[:8]
    job = {"job_id": job_id, "model": model["model"], "run_dir": run, "config": model["config"],
           "output": model["output"], "target": target,
           "submitted": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    command = target["run_command"].replace("{config}", shlex.quote(model["config"]))
    wrapper = WRAPPER_SCRIPT.format(log=LOG, session=SESSION_FILE, exit_file=EXIT_FILE, job_env=JOB_ENV,
                                    job_id=job_id, command=command)
    if target["type"] == "local":
        with open(os.path.join(run, WRAPPER), "w", encoding="utf-8", newline="\n") as f:
            f.write(wrapper)
        open(os.path.join(run, LOG), "w").close()      # exists at once, for the log window
        _write_job(job)
        job["pid"] = _start_local(job)
    else:
        user = NAME_RE.sub("_", getpass.getuser()) or "user"
        project = NAME_RE.sub("_", os.path.basename(os.path.dirname(os.path.dirname(run)))) or "project"
        job["remote_dir"] = f"{target['remote_dir']}/{user}/{project}/{model['model']}_{job_id}"
        _upload(job, model, wrapper)
        # setsid -f: with 'setsid ... &' instead, ssh only returns when the run has ended
        _run_script(target, f"cd {shlex.quote(job['remote_dir'])} && "
                            f"setsid -f nohup bash -l {WRAPPER} > /dev/null 2>&1 < /dev/null\n")
    _write_job(job)

    log_window = None
    if show_log:
        title = f"Palace: {model['model']} on {target['name']}"
        try:
            if target["type"] == "local":
                log_window = open_log_window(os.path.join(run, LOG), title)
            else:
                remote_log = shlex.quote(f"{job['remote_dir']}/{LOG}")
                log_window = open_log_window(None, title, _ssh(target["host"], f"tail -n +1 -F {remote_log}",
                                                               tty=True))
        except OSError as e:
            log_window = f"none ({e})"

    where = f"in {run}" if target["type"] == "local" else f"in {job['remote_dir']} on {target['host']}"
    notes = (" It waits in the simulation server's queue until a slot is free; simulation_status shows 'queued' meanwhile."
             if target["shared"] else "")
    if previous and previous["target"]["type"] == "ssh" and not previous.get("fetched"):
        notes += (f" The earlier run's folder {previous['remote_dir']} on {previous['target']['host']} was not "
                  "fetched and is not tracked any more; it stays there until the user deletes it.")
    return {"submitted": True, "job_id": job_id, "target": target["name"], "type": target["type"],
            "shared": target["shared"], "run_dir": run, "remote_dir": job.get("remote_dir"),
            "log_window": log_window, "estimate": estimate, "warnings": warnings,
            "palace_running_on_target": running if target["shared"] else None,
            "note": (f"Palace was started {where}.{notes} Check it with simulation_status (it can take minutes to "
                     "hours); when it finished, fetch_results copies the results back and makes the Touchstone "
                     "files. This MCP server never stops the run; to stop it, the user ends its processes "
                     "themselves.")}


def _progress(mpi_lines, lines):
    progress = {}
    for line in mpi_lines:
        if match := MPI_RE.search(line):
            progress["mpi_processes"] = int(match[1])
    for line in lines:      # a new AMR iteration or port starts its steps from the beginning
        if match := AMR_RE.search(line):
            progress = {key: progress[key] for key in ("mpi_processes",) if key in progress}
            progress["amr_iteration"] = int(next(g for g in match.groups() if g))
        elif match := EXCITATION_RE.search(line):
            for key in ("frequency_step", "frequency_ghz", "greedy_iteration"):
                progress.pop(key, None)
            progress["phase"] = ("frequency sweep" if match[1] == "Sweeping"
                                 else "building the reduced-order model (adaptive sweep)")
            progress["port"] = f"{match[2]}/{match[3]}"
        elif match := FREQUENCY_STEP_RE.search(line):
            progress["frequency_step"] = f"{match[1]}/{match[2]}"
            if frequency := FREQUENCY_RE.search(line):
                progress["frequency_ghz"] = float(frequency[1])
        elif match := GREEDY_RE.search(line):
            progress["greedy_iteration"] = int(match[1])
            if frequency := FREQUENCY_RE.search(line):
                progress["frequency_ghz"] = float(frequency[1])
    return progress


def _queue_line(lines):
    """The last queue line of run_palace_shared, or None."""
    queue = [line for line in lines if line.startswith((QUEUE_WAITING, QUEUE_GOT))]
    return queue[-1] if queue else None


def _status(job):
    target, folder = job["target"], shlex.quote(_target_dir(job))
    script = (f'cd {folder} 2>/dev/null || {{ echo "@@missing"; exit 0; }}\n'
              f'echo "@@exit"\ncat {EXIT_FILE} 2>/dev/null\n'
              f'echo "@@session"\ncat {SESSION_FILE} 2>/dev/null\n'
              f'echo "@@mpi"\ngrep -m 1 "Running with" {LOG} 2>/dev/null\n'
              f'echo "@@progress"\ngrep -E {shlex.quote(PROGRESS_GREP)} {LOG} 2>/dev/null | tail -n 40\n'
              f'echo "@@notconverged"\ngrep -c "did NOT converge" {LOG} 2>/dev/null\n'
              f'echo "@@tail"\ntail -n {TAIL_LINES} {LOG} 2>/dev/null\n'
              f'echo "@@iterations"\nls -d {shlex.quote(job["output"])}/iteration* 2>/dev/null | wc -l\n'
              f'echo "@@marked"\nfor d in /proc/[0-9]*; do grep -qz "^{JOB_ENV}={job["job_id"]}$" "$d/environ" '
              '2>/dev/null && echo "${d#/proc/}"; done\n'
              f'echo "@@processes"\n{PS_COMMAND}\n')
    sections = _sections(_run_script(target, script))

    status = {"job_id": job["job_id"], "model": job["model"], "target": target["name"], "type": target["type"],
              "run_dir": job["run_dir"], "remote_dir": job.get("remote_dir"), "submitted": job["submitted"]}
    if "missing" in sections:
        status["state"] = "missing"
        status["note"] = f"The run folder {_target_dir(job)} is not on the target any more (deleted?)."
        return status
    exit_code = "".join(sections.get("exit", [])).strip()
    session = "".join(sections.get("session", [])).strip()
    processes = _processes(sections.get("processes", []))
    marked = {line.strip() for line in sections.get("marked", []) if line.strip().isdigit()}
    own = [p for p in processes if p[0] in marked or (session and p[3] == session)]
    queue_line = _queue_line(sections.get("progress", []))
    if exit_code:
        state = "finished" if exit_code == "0" else "failed"
        status["exit_code"] = int(exit_code) if exit_code.lstrip("-").isdigit() else exit_code
    elif own:
        state = "queued" if queue_line and queue_line.startswith(QUEUE_WAITING) else "running"
    else:
        state = "stopped"
    status["state"] = state
    if queue_line:
        status["queue"] = queue_line
    if own:
        palace = _palace_processes(own)
        status["palace_processes"], status["palace_ram_gb"] = palace["processes"], palace["ram_gb"]
    status["other_palace_processes"] = _palace_processes(processes, exclude_pids={p[0] for p in own})
    submitted = datetime.datetime.fromisoformat(job["submitted"])
    status["minutes_since_submit"] = round((datetime.datetime.now().astimezone() - submitted).total_seconds() / 60, 1)
    status["progress"] = _progress(sections.get("mpi", []), sections.get("progress", []))
    iterations = "".join(sections.get("iterations", [])).strip()
    if iterations.isdigit() and int(iterations):
        status["amr_iterations_done"] = int(iterations)
    not_converged = "".join(sections.get("notconverged", [])).strip()
    status["did_not_converge_lines"] = int(not_converged) if not_converged.isdigit() else 0
    status["log_tail"] = sections.get("tail", [])
    status["fetched"] = bool(job.get("fetched"))
    status["note"] = {
        "queued": "Waiting in the shared simulation server's queue for a free slot (see queue). Check again later with "
                  "simulation_status.",
        "running": "Palace is running. Check again later with simulation_status. This MCP server never stops a run.",
        "finished": "Palace finished: fetch_results " + ("makes the Touchstone files (the results are already here)."
                                                         if target["type"] == "local" else
                                                         "copies the results back and makes the Touchstone files."),
        "failed": "Palace ended with an error: see log_tail. fetch_results copies the full log back (palace.log).",
        "stopped": "The run ended without an exit code: it was killed, or the machine restarted.",
    }[state]
    if status["did_not_converge_lines"]:
        status["note"] += (f" {status['did_not_converge_lines']} 'did NOT converge' lines in the log: the results at "
                           "those frequencies are not reliable.")
    return status


def simulation_status(path):
    return _status(_read_job(path))


def _extract(stream, destination, include_fields):
    """Extract a tar stream: regular files and folders only, inside destination (no links, no absolute or ..
    paths), without paraview field dumps unless include_fields."""
    names = []
    with tarfile.open(fileobj=stream, mode="r|") as tar:
        for member in tar:
            parts = member.name.split("/")
            if (member.name.startswith("/") or ".." in parts or not (member.isfile() or member.isdir())
                    or (not include_fields and "paraview" in parts)):
                continue
            if hasattr(tarfile, "data_filter"):
                tar.extract(member, destination, filter="data")
            else:
                tar.extract(member, destination)
            if member.isfile():
                names.append(member.name)
    return names


def _download(job, include_fields):
    remote = shlex.quote(job["remote_dir"])
    names = " ".join(shlex.quote(n) for n in (LOG, EXIT_FILE, job["output"]))
    exclude = "" if include_fields else " --exclude=paraview"
    script = (f'cd {remote} || exit 1; set --; for f in {names}; do [ -e "$f" ] && set -- "$@" "$f"; done; '
              f'tar cf -{exclude} "$@"')
    # bash -c without login: the tar stream is stdout, nothing else may print there
    process = subprocess.Popen(_ssh(job["target"]["host"], f"bash -c {shlex.quote(script)}"),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        names = _extract(process.stdout, job["run_dir"], include_fields)
    except tarfile.TarError as e:
        process.wait()
        raise OSError(f"Copying the results back failed ({e}): "
                      f"{process.stderr.read().decode('utf-8', 'replace').strip()}") from None
    stderr = process.stderr.read().decode("utf-8", "replace").strip()
    if process.wait() != 0:
        raise OSError(f"Copying the results back from {job['target']['host']} failed: {stderr}")
    return names


def _combine(output_dir):
    """combine_extend_snp (gds2palace's combine_snp) in the output folder: Touchstone files from port-S.csv."""
    from gds2palace import utilities
    scripts_dir = utilities.get_scripts_dir() if hasattr(utilities, "get_scripts_dir") else None
    if scripts_dir is None:
        raise FileNotFoundError("combine_extend_snp.py was not found: fetch_results needs gds2palace 0.9.2 or newer.")
    result = subprocess.run([sys.executable, os.path.join(scripts_dir, "combine_extend_snp.py")], cwd=output_dir,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=900,
                            env=dict(os.environ, MPLBACKEND="Agg", PYTHONIOENCODING="utf-8"))
    lines = (result.stdout + result.stderr).splitlines()
    return result.returncode, [l for l in lines if "rror" in l or "WARNING" in l][:20]


def fetch_results(path, include_fields=False, partial=False, delete_remote=False):
    job = _read_job(path)
    status = _status(job)
    state = status["state"]
    if state == "missing":
        raise FileNotFoundError(status["note"])
    if state in ("queued", "running") and not partial:
        raise ValueError(f"The run is still {state}: wait until it finished (simulation_status), or fetch what is "
                         "there so far with partial=True (e.g. finished AMR iterations).")
    if delete_remote and state in ("queued", "running"):
        raise ValueError("The remote copy of a run that is still going can't be deleted.")

    result = {"job_id": job["job_id"], "target": job["target"]["name"], "state": state, "run_dir": job["run_dir"]}
    if job["target"]["type"] == "ssh":
        result["files_copied"] = len(_download(job, include_fields))
    output_dir = os.path.join(job["run_dir"], job["output"])
    if os.path.isdir(output_dir):
        returncode, messages = _combine(output_dir)
        result["combine_snp"] = {"returncode": returncode, "messages": messages}
    else:
        result["combine_snp"] = {"skipped": f"no output folder {job['output']} (Palace wrote no results)"}
    summary, _ = results.summarize_results(job["run_dir"])
    result["summary"] = summary["runs"]
    result["touchstone_files"] = sorted({f for run in summary["runs"] for f in run["touchstone_files"]})
    result["notes"] = summary["notes"]

    if state not in ("queued", "running"):
        job["fetched"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        _write_job(job)
    if job["target"]["type"] == "ssh":
        if delete_remote:
            _run_script(job["target"], f"rm -rf -- {shlex.quote(job['remote_dir'])}\n")
            result["remote_deleted"] = job["remote_dir"]
        else:
            result["remote_dir"] = job["remote_dir"]
            result["notes"].append(f"The run folder stays on {job['target']['host']} ({job['remote_dir']}); "
                                   "delete_remote=True removes it, only when the user asks for it.")
    return result
