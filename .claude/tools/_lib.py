#!/usr/bin/env python3
"""_lib.py - shared foundation for the dot-claude-iff tools.

Stdlib only, forever. Every hook and core tool imports this module, and hooks run under
a hard timeout on every tool call, so nothing here may import outside the stdlib or do
network I/O.

Two write tiers (see atomic_write_text):
  durable=True   fsync file + directory. For sources of truth (journal, needs-human,
                 anything whose loss is unrecoverable).
  durable=False  atomic rename only. For derived views that a regenerator can rebuild.

CLI: this module exposes a few resolved paths so shell hooks can ask python for them
instead of re-deriving them (and drifting from) the logic here:
    python3 _lib.py --record-root | --project-root | --paths-json | --release-kind <tag>
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- vocabulary

# The journal's action vocabulary. ONE list, shared by every writer and by the projector
# in statectl.py - the crawler's writers and projector drifted apart and `config` events
# became invisible. A writer using an action not in this set is a bug, not a new feature.
JOURNAL_ACTIONS = (
    "session_start",  # a working session opened            {session, phase, note}
    "pointer",        # the next concrete action            {text}
    "task",           # task state                          {id, title, status, deps, milestone, note, no_envelope}
    "milestone",      # something shipped                   {id, title, note}
    "decision",       # a choice and its reason             {text, why}
    "loop",           # an open/closed thread               {id, text, status}
    "note",           # free narration                      {text}
    "intent",         # write-ahead bracket for composite ops {state, intent_id, op, files}
    "config",         # a config value changed              {changes, via}
    "gate",           # a human gate was asked/answered     {question, answer, kind}
    "tooling",        # the .claude system itself changed   {change_type, what, evidence}
    "mode",           # how organised the work is           {value}
    "phase",          # what work is allowed (lifecycle)    {value, from, override, signoff}
)

TASK_STATUSES = ("todo", "doing", "done", "blocked")
# A task file's status line, `_Created YYYY-MM-DD · Status: todo_`. ONE pattern for the console's
# task reader and checkctl's phase exits. The status class has no underscore: the line's closing
# italic `_` used to be captured into the status ("doing_").
TASK_STATUS_LINE_RE = re.compile(r"^_Created.*?·\s*Status:\s*([A-Za-z0-9-]+)_?\s*$", re.MULTILINE)

# The three dials (milestone contract): phase = what work is allowed, mode = how organised the
# work is. Stored and displayed values are the full names; the aliases are accepted on input
# only. An unset mode IS freestyle: record, gates and ritual, no phase contract, no exit checks.
MODES = ("freestyle", "guided-solo", "fableous-orchestrated")
DEFAULT_MODE = "freestyle"
ORGANISED_MODES = ("guided-solo", "fableous-orchestrated")
# The one mode with lead-and-team routing (protocols/orchestration.md): the handoff guard, the
# builder stop check and the delegation nudge act in it and in no other.
ORCHESTRATED_MODE = "fableous-orchestrated"
MODE_ALIASES = {"guided": "guided-solo", "solo": "guided-solo",
                "fableous": "fableous-orchestrated", "orchestrated": "fableous-orchestrated"}
MODE_LABELS = {"freestyle": "Freestyle", "guided-solo": "Guided Solo",
               "fableous-orchestrated": "Fableous Orchestrated"}
LIFECYCLE_PHASES = ("plan", "build", "review", "deploy")
PHASE_CONTRACT_MAX_LINES = 5

# The proposal box (state/proposals.jsonl): ideas that are out of scope right now.
PROPOSAL_KINDS = ("feature", "fix", "evolve")
PROPOSAL_RESOLUTIONS = ("planned", "rejected")
PROPOSAL_SOURCE_RE = re.compile(r"^(?:issue#\d+|human|agent:[A-Za-z0-9._-]+)$")
LOOP_STATUSES = ("open", "closed")
SEV_BANDS = ("SEV0", "SEV1", "SEV2", "SEV3")

# needs-human categories (queue-as-view; band-first triage)
NEEDS_HUMAN_CATEGORIES = (
    "provide-input",
    "decide",
    "review",
    "unblock-env",
    "approve-release",
    "system-blocker",
)

DEFAULT_BAND_BY_CATEGORY = {
    "system-blocker": "SEV0",
    "approve-release": "SEV1",
    "decide": "SEV1",
    "review": "SEV2",
    "provide-input": "SEV2",
    "unblock-env": "SEV2",
}


class LibError(RuntimeError):
    """Raised for unrecoverable, caller-visible problems (bad action, missing root)."""


# --------------------------------------------------------------------------- time / text

def utc_now() -> str:
    """ISO-8601 UTC, seconds precision, Z-suffixed. The one timestamp format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_ts(ts: str):
    """Parse our timestamp format back to an aware datetime, or None if unparseable."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:  # tolerate other ISO shapes that may arrive from transcripts
        cleaned = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def age_seconds(ts: str):
    dt = parse_ts(ts)
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def slugify(text: str, max_len: int = 64) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-")
    return (s or "unknown")[:max_len]


def human_bytes(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step or unit == "TB":
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} TB"


# --------------------------------------------------------------------------- paths

def project_root(start: Path | None = None) -> Path:
    """The repo root: $CLAUDE_PROJECT_DIR when set (hooks), else the nearest ancestor
    holding a .claude/ directory, else the git toplevel, else cwd."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
    here = (start or Path(__file__).resolve().parent).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".claude").is_dir():
            return candidate
    cwd = Path.cwd().resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".claude").is_dir():
            return candidate
    return cwd


def claude_dir() -> Path:
    return project_root() / ".claude"


def iff_dir() -> Path:
    """The in-repo, committed, agent-write-denied record surface."""
    return project_root() / ".claude-iff"


def config_dir() -> Path:
    return claude_dir() / "config"


def state_dir() -> Path:
    return claude_dir() / "state"


def tools_dir() -> Path:
    return claude_dir() / "tools"


def console_dir() -> Path:
    return claude_dir() / "console"


def map_dir() -> Path:
    return claude_dir() / "system-map"


def default_record_root(root: Path | None = None) -> Path:
    """Sibling folder: <parent>/<repo-name>_claude_iff/ - out of the repo, out of git,
    but visible next to the project it belongs to."""
    r = (root or project_root()).resolve()
    return r.parent / f"{r.name}_claude_iff"


def dotenv_get(key: str):
    """Read one CLAUDE_IFF_* key from the repo-root .env file, if present.

    Machine-specific absolute paths (like a record-root override) must never be baked into a
    committed file; the sanctioned channels are the process environment and this gitignored
    .env. Read-only, values only for our own prefix, no interpolation, silent on any parse
    problem: a broken .env must degrade to the defaults, never crash a hook.
    """
    if not key.startswith("CLAUDE_IFF_"):
        return None
    try:
        env_path = project_root() / ".env"
        if not env_path.is_file():
            return None
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == key:
                return value.strip().strip("'\"") or None
    except OSError:
        return None
    return None


def record_root() -> Path:
    """RECORD_ROOT: raw capture, sealed raw, segments, transcripts, analysis, vault.

    Resolution order: $CLAUDE_IFF_RECORD_ROOT (env) -> .env at the repo root (gitignored)
    -> policy.json record_root (committed, so relative values only belong there)
    -> the sibling default. The policy gate resolves the same value through this
    function, so the deny rule can never disagree with where we actually write.
    """
    env = os.environ.get("CLAUDE_IFF_RECORD_ROOT") or dotenv_get("CLAUDE_IFF_RECORD_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    configured = (load_config("policy") or {}).get("record_root")
    if configured:
        p = Path(str(configured)).expanduser()
        if not p.is_absolute():
            p = project_root() / p
        return p.resolve()
    return default_record_root()


def record_paths() -> dict:
    rr = record_root()
    return {
        "root": rr,
        "spool": rr / "spool",
        "sealed_raw": rr / "sealed-raw",
        "segments": rr / "segments",
        "transcripts": rr / "raw" / "transcripts",
        "analysis": rr / "analysis",
        "vault": rr / "vault",
        "cursors": rr / "cursors.json",
    }


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def tilde(path) -> str:
    """Home-relative display form (~/...). For any string that may land in a COMMITTED file:
    an absolute path under a home directory names the machine and its user, and neither
    belongs in a repo. The no_machine_paths check enforces this mechanically."""
    text = str(path)
    home = str(Path.home())
    if home and text.startswith(home):
        tail = text[len(home):]
        if os.name == "nt":
            # Display form is posix everywhere: these strings land in committed files and
            # console payloads, where a backslash form would name the platform.
            tail = tail.replace("\\", "/")
        return "~" + tail
    return text


def rel(path: Path, base: Path | None = None) -> str:
    """Repo-relative display path, posix-form on every platform (these strings land in
    committed surfaces - cards, messages); falls back to the absolute path when outside."""
    b = (base or project_root()).resolve()
    try:
        return Path(path).resolve().relative_to(b).as_posix()
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- console endpoint

def console_port(cfg: dict | None = None) -> int:
    """The console's port. An explicit integer in console.json is a decided-once override;
    the shipped default "auto" (or an absent key) derives a stable port from the repo FOLDER
    NAME - stable across machines and clones, unlike the absolute path - spreading projects
    across 7100-7899 so the second adoption on one machine stops colliding with the first."""
    if cfg is None:
        cfg = load_config("console")
    port = cfg.get("port", "auto")
    try:
        return int(port)
    except (TypeError, ValueError):
        import hashlib
        digest = hashlib.sha256(project_root().name.encode("utf-8")).hexdigest()
        return 7100 + int(digest, 16) % 800


def console_hostname() -> str:
    """Folder-name host for display URLs: browsers resolve every *.localhost name to
    loopback with zero configuration (RFC 6761), so http://<folder>.localhost:<port>/ names
    the project in the browser tab. Sanitized to hostname-legal characters; the server still
    binds the loopback IP - this name is identity, not reachability."""
    name = re.sub(r"[^a-z0-9-]+", "-", project_root().name.lower()).strip("-")
    return (name or "project") + ".localhost"


def console_url(cfg: dict | None = None) -> str:
    return f"http://{console_hostname()}:{console_port(cfg)}/console.html"


# --------------------------------------------------------------------------- machine identity

def machine_state_path() -> Path:
    return state_dir() / "machine.json"


def machine_fingerprint() -> str:
    """12 hex chars of sha256(user|host): enough to tell two machines apart in a committed
    file without committing the username or the hostname themselves."""
    import getpass
    import hashlib
    import socket
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return hashlib.sha256(f"{user}|{host}".encode("utf-8")).hexdigest()[:12]


def machine_snapshot(alias: str | None) -> dict:
    import platform
    return {
        "fingerprint": machine_fingerprint(),
        "os": sys.platform,
        "arch": platform.machine(),
        "python": platform.python_version(),
        "device_alias": alias,
    }


def machine_check() -> str | None:
    """Compare the running machine to state/machine.json and return a line for the human
    when something needs saying: an unnamed machine, or a device change (the repo traveled
    - a clone, a sync, a copied disk). READ-ONLY on purpose: the session-start hook prints
    this, and that hook's contract is to never write project state - so the line repeats,
    loudly, every session until `statectl device` names the machine, and that command is
    what writes the file and journals the transition. Telemetry: fails open."""
    try:
        stored = read_json(machine_state_path(), None)
        current = machine_fingerprint()
        if not isinstance(stored, dict) or not stored.get("fingerprint"):
            return (f"DEVICE: this machine is not named yet ({sys.platform}, fp {current}). "
                    f'Name it: python3 .claude/tools/statectl.py device "<alias>"')
        if stored.get("fingerprint") == current:
            return None
        was = stored.get("device_alias") or f"fp {stored.get('fingerprint')}"
        return (f"DEVICE CHANGE: this repo last ran on '{was}' ({stored.get('os', '?')}), "
                f"now on a different {sys.platform} machine (fp {current}). Paths, GPUs and "
                f"installed tools may differ - re-check machine-specific assumptions and "
                f"update any memory that assumed the old device, then name this one: "
                f'python3 .claude/tools/statectl.py device "<alias>"')
    except Exception:
        return None


# --------------------------------------------------------------------------- atomic io

def _current_umask() -> int:
    """Read the umask without leaving it changed. os.umask both sets and returns, so the only
    way to read it is to set it and set it straight back."""
    mask = os.umask(0o022)
    os.umask(mask)
    return mask


def _fsync_dir(path: Path) -> None:
    """Fsync a directory so a just-renamed entry survives power loss.

    POSIX only: Windows cannot os.open() a directory (it raises PermissionError; the
    needed FILE_FLAG_BACKUP_SEMANTICS cannot be passed through os.open) and has no
    directory fsync at all, so there the file fsync is the strongest guarantee
    available and this is deliberately a no-op.
    """
    if os.name != "posix":
        return
    dir_fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write_text(path: Path, text: str, durable: bool = False) -> Path:
    """Write via temp-file + rename, so a reader sees old or new content, never torn.

    durable=True additionally fsyncs the file and (on POSIX) its directory (survives
    power loss); use it for sources of truth only - derived views are cheap to rebuild
    and paying two fsyncs per regenerated console is waste.
    """
    path = Path(path)
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        # mkstemp creates 0600. Derived files land in a shared checkout and are read by the
        # console, by git and by whoever else opens the repo, so normalise to the usual 0644
        # (respecting umask) rather than leaving every generated file owner-only.
        os.chmod(tmp, 0o666 & ~_current_umask())
        os.replace(tmp, path)
        if durable:
            _fsync_dir(path.parent)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def atomic_write_json(path: Path, obj, durable: bool = False, indent: int = 2) -> Path:
    return atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent) + "\n", durable)


def append_jsonl(path: Path, obj: dict, durable: bool = False) -> None:
    """One O_APPEND write of one complete line: atomic under concurrent appenders.

    A kill mid-write can at worst leave a torn LAST line, which every reader here
    tolerates by design (see read_jsonl).
    """
    path = Path(path)
    ensure_dir(path.parent)
    line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
        if durable:
            os.fsync(fd)
    finally:
        os.close(fd)


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return default


def read_jsonl(path: Path, tolerant: bool = True) -> list:
    """Read a JSONL file, skipping malformed lines when tolerant.

    Tolerance is load-bearing: the journal backs the SessionStart resume path, and a
    single torn last line from a crash must never break a fresh session's orientation.
    """
    # errors="replace", not strict: a single non-UTF-8 byte anywhere in the journal used to
    # raise UnicodeDecodeError, which is a ValueError and so was caught by neither the
    # JSONDecodeError nor the OSError arm below. That took out `resume` (the SessionStart
    # orientation) AND `need open` (the human-escalation path) at once, silently. Tolerance is
    # only load-bearing if it actually covers the ways a file goes bad.
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    if tolerant:
                        continue
                    raise
                if isinstance(obj, dict):
                    out.append(obj)
                elif not tolerant:
                    raise LibError(f"non-object JSONL line in {path}")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError):
        return []
    return out


def tail_jsonl(path: Path, n: int = 8, max_bytes: int = 65536) -> list:
    """Last n parseable objects, read from at most the final max_bytes.

    The live console polls this; it must never pay for the whole file.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return []
    try:
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the partial first line
            chunk = fh.read()
    except OSError:
        return []
    out = []
    for raw in chunk.decode("utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out[-n:] if n else out


# --------------------------------------------------------------------------- config

_CONFIG_CACHE: dict = {}


def load_config(name: str, default=None, use_cache: bool = True):
    """Load .claude/config/<name>.json. Missing or malformed returns default ({}).

    Config files carry `_comment` keys as inline rationale; callers ignore them.

    The cache is keyed by (config dir, name), not name alone: one process can legitimately
    look at more than one project root - tests do it constantly - and a name-only cache
    would serve one project's policy while resolving another project's paths.
    """
    cfg_dir = config_dir()
    key = (str(cfg_dir), name)
    if use_cache and key in _CONFIG_CACHE:
        return _CONFIG_CACHE[key]
    data = read_json(cfg_dir / f"{name}.json", default if default is not None else {})
    if use_cache:
        _CONFIG_CACHE[key] = data
    return data


def clear_config_cache() -> None:
    _CONFIG_CACHE.clear()


def config_get(name: str, dotted_key: str, default=None):
    node = load_config(name)
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# --------------------------------------------------------------------------- hashing

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(131072), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def sha256_paths(paths) -> str:
    """Stable content hash over a set of files (missing files contribute a marker).

    Freshness is decided by CONTENT, never mtime: git does not preserve mtimes, so an
    mtime rule fires randomly on every fresh clone and on every branch switch.
    """
    h = hashlib.sha256()
    for p in sorted({str(Path(x)) for x in paths}):
        h.update(p.encode("utf-8"))
        h.update(b"\0")
        if Path(p).is_dir():
            for f in sorted(Path(p).rglob("*")):
                if f.is_file():
                    h.update(str(f).encode("utf-8"))
                    h.update((sha256_file(f) or "missing").encode("utf-8"))
        else:
            h.update((sha256_file(Path(p)) or "missing").encode("utf-8"))
    return h.hexdigest()


# --------------------------------------------------------------------------- journal

def journal_path() -> Path:
    return state_dir() / "journal.jsonl"


def journal_append(action: str, **fields) -> dict:
    """Append one journal event. The journal is the source of truth; session.json,
    HANDOFF.md and the graph are projections of it and are never hand-edited."""
    if action not in JOURNAL_ACTIONS:
        raise LibError(
            f"unknown journal action {action!r}; add it to JOURNAL_ACTIONS in _lib.py "
            f"and teach statectl's projector about it in the same change"
        )
    event = {"ts": utc_now(), "action": action}
    for key, value in fields.items():
        if value is not None:
            event[key] = value
    append_jsonl(journal_path(), event, durable=True)
    return event


def journal_read(tolerant: bool = True) -> list:
    return read_jsonl(journal_path(), tolerant=tolerant)


# --------------------------------------------------------------------------- lifecycle: mode + phase
#
# ONE reader of "which mode, which phase", folded from the journal (the truth), shared by the
# projector, the SessionStart hook, checkctl and the console. A second reader is how the hook
# and the console would come to disagree about the phase you are in.

def normalize_mode(value) -> str | None:
    """Full mode name for a stored value or an input alias; None when it is neither."""
    text = str(value or "").strip().lower()
    text = MODE_ALIASES.get(text, text)
    return text if text in MODES else None


def normalize_phase(value) -> str | None:
    """A lifecycle phase, or None. Legacy free text (session_start's old `phase`, such as
    "implementation") reads as unset rather than as a phase nobody can leave."""
    text = str(value or "").strip().lower()
    return text if text in LIFECYCLE_PHASES else None


def mode_label(mode) -> str:
    return MODE_LABELS.get(normalize_mode(mode) or DEFAULT_MODE, "Freestyle")


def fold_lifecycle(events: list) -> dict:
    """{mode, mode_set, phase, phase_since} from journal events, latest wins.

    `phase` events are the source of the phase. A journal that predates them keeps its last
    session_start phase only when that is a lifecycle phase; once any `phase` event exists,
    session_start no longer moves the phase (it would be a way round the exit checks)."""
    mode = None
    phase = phase_since = None
    legacy = legacy_since = None
    for ev in events:
        action = ev.get("action")
        if action == "mode":
            mode = normalize_mode(ev.get("value")) or mode
        elif action == "phase":
            value = normalize_phase(ev.get("value"))
            if value:
                phase, phase_since = value, ev.get("ts") or None
        elif action == "session_start":
            value = normalize_phase(ev.get("phase"))
            if value:
                legacy, legacy_since = value, ev.get("ts") or None
    if phase is None and legacy is not None:
        phase, phase_since = legacy, legacy_since
    return {"mode": mode or DEFAULT_MODE, "mode_set": mode is not None,
            "phase": phase, "phase_since": phase_since}


def lifecycle_state() -> dict:
    return fold_lifecycle(journal_read(tolerant=True))


def current_mode() -> str:
    return lifecycle_state()["mode"]


def current_phase() -> str | None:
    return lifecycle_state()["phase"]


def phase_spec(phase) -> dict:
    """phases.json's entry for one phase ({label, contract, exit}), {} when absent or malformed."""
    cfg = load_config("phases")
    phases = cfg.get("phases") if isinstance(cfg, dict) else None
    spec = phases.get(normalize_phase(phase) or "") if isinstance(phases, dict) else None
    return spec if isinstance(spec, dict) else {}


def phase_label(phase) -> str:
    value = normalize_phase(phase)
    if not value:
        return "unset"
    return str(phase_spec(value).get("label") or value.capitalize())


def phase_contract(phase) -> list:
    """The phase's contract lines, at most PHASE_CONTRACT_MAX_LINES, from phases.json: the one
    text the hook, the console and the docs all show."""
    lines = phase_spec(phase).get("contract") or []
    if not isinstance(lines, list):
        return []
    return [str(x) for x in lines if str(x).strip()][:PHASE_CONTRACT_MAX_LINES]


def lifecycle_banner(state: dict | None = None) -> list:
    """The SessionStart block: one MODE/PHASE line, plus the current phase's contract in the
    two organised modes. Freestyle gets the one line and nothing else."""
    state = state or lifecycle_state()
    mode, phase = state["mode"], state["phase"]
    head = f"MODE: {mode_label(mode)} · PHASE: {phase or 'unset'}"
    if mode not in ORGANISED_MODES:
        return [head]
    if not phase:
        return [head + " (set one: python3 .claude/tools/statectl.py phase plan)"]
    return [head] + [f"  - {line}" for line in phase_contract(phase)]


# --------------------------------------------------------------------------- proposal box

def proposals_path() -> Path:
    return state_dir() / "proposals.jsonl"


def proposal_records() -> list:
    """Fold state/proposals.jsonl ({op: add|resolve}) into one record per id, in the order
    they were added. Like needs-human it is its own append-only store, not a journal action:
    an idea parked for later must outlive any one session. A resolve for an unknown id is
    ignored; a later resolve overwrites an earlier one."""
    records: dict = {}
    for ev in read_jsonl(proposals_path(), tolerant=True):
        pid = ev.get("id")
        if not pid:
            continue
        op = ev.get("op")
        if op == "add" and pid not in records:
            records[pid] = {
                "id": str(pid), "text": str(ev.get("text", "")), "source": str(ev.get("source", "")),
                "kind": str(ev.get("kind", "")), "status": "open", "added": str(ev.get("ts", "")),
                "resolved": "", "note": "",
            }
        elif op == "resolve" and pid in records:
            rec = records[pid]
            rec["status"] = str(ev.get("resolution") or rec["status"])
            rec["resolved"] = str(ev.get("ts", ""))
            rec["note"] = str(ev.get("note", ""))
    return list(records.values())


# --------------------------------------------------------------------------- handoff envelopes
#
# ONE envelope contract (.claude/protocols/handshake.md), checked by ONE function. The
# post-write hook (every Write/Edit of an envelope), `checkctl handoff` (the lead's review
# before a merge) and the `statectl task --status done` guard all call validate_envelope():
# two validators is how the 0.3.0 builder brief and the hook came to disagree.

ENVELOPE_STATUSES = ("done", "partial", "blocked")
ENVELOPE_DONE = "done"
ENVELOPE_REQUIRED = ("agent_id", "task_id", "status")
# What a builder's handoff adds, so the lead can review and merge without re-reading the run.
BUILDER_ENVELOPE_REQUIRED = ("agent", "model", "files_changed", "tests", "needs_main")
# The 0.3.0 builder brief's uppercase STATUS (ok|partial|blocked). Optional; when present it
# must say what `status` says.
LEGACY_ENVELOPE_STATUS = {"ok": "done", "done": "done", "partial": "partial", "blocked": "blocked"}
# The report sections: prose inside `notes`, or top-level keys holding a string or a list.
REPORT_SECTIONS = ("RESULT", "EVIDENCE", "DEVIATIONS", "UNCERTAINTIES", "QUESTIONS", "SUGGESTIONS")
# A stub's dispatch time. `dispatched_at` is the contract; `since` and `ts` are still read for
# stubs written before the name was settled.
STUB_TIME_KEYS = ("dispatched_at", "since", "ts")


def handshakes_dir(root: Path | None = None) -> Path:
    return (Path(root) if root else project_root()) / ".claude" / "state" / "handshakes"


def envelope_path(task_id: str, root: Path | None = None) -> Path:
    return handshakes_dir(root) / f"{task_id}.json"


def stub_path(task_id: str, root: Path | None = None) -> Path:
    return handshakes_dir(root) / f"{task_id}.stub.json"


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _text_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def validate_envelope(obj, builder: bool | None = None) -> list:
    """Every way `obj` breaks the envelope contract, one message each; [] when it is valid.

    builder=None decides from the envelope itself (agent == "builder"); True demands the
    builder keys whatever the envelope says (checkctl handoff, the done guard). A builder that
    says `done` must have recorded at least one test, and every test it recorded exited 0."""
    if not isinstance(obj, dict):
        return ["the envelope is not a JSON object"]
    errors = []
    for key in ENVELOPE_REQUIRED:
        if not _text(obj.get(key)):
            errors.append(f"{key} is missing or empty")
    status = obj.get("status")
    if _text(status) and status not in ENVELOPE_STATUSES:
        errors.append(f"status must be one of {'|'.join(ENVELOPE_STATUSES)}, got {status!r}")
    if "STATUS" in obj:
        legacy = LEGACY_ENVELOPE_STATUS.get(str(obj["STATUS"]).strip().lower())
        if legacy is None:
            errors.append(f"STATUS must be ok|partial|blocked, got {obj['STATUS']!r}")
        elif status in ENVELOPE_STATUSES and legacy != status:
            errors.append(f"STATUS {obj['STATUS']!r} disagrees with status {status!r}")
    if "artifacts" in obj and not _text_list(obj["artifacts"]):
        errors.append("artifacts must be a list of paths")
    if "notes" in obj and not isinstance(obj["notes"], str):
        errors.append("notes must be a string")
    for key in REPORT_SECTIONS:
        if key in obj and not (isinstance(obj[key], str) or _text_list(obj[key])):
            errors.append(f"{key} must be a string or a list of strings")
    if builder is None:
        builder = obj.get("agent") == "builder"
    if not builder:
        return errors
    for key in ("agent", "model"):
        if not _text(obj.get(key)):
            errors.append(f"{key} is missing or empty (builder envelope)")
    if not _text_list(obj.get("files_changed")):
        errors.append("files_changed must be a list of repo-relative paths (builder envelope)")
    tests = obj.get("tests")
    if not isinstance(tests, list):
        errors.append("tests must be a list of {command, exit_code, summary} (builder envelope)")
        tests = []
    for i, test in enumerate(tests):
        if not isinstance(test, dict):
            errors.append(f"tests[{i}] must be an object {{command, exit_code, summary}}")
            continue
        if not _text(test.get("command")):
            errors.append(f"tests[{i}].command is missing or empty")
        code = test.get("exit_code")
        if not isinstance(code, int) or isinstance(code, bool):
            errors.append(f"tests[{i}].exit_code must be an integer, got {code!r}")
        if not isinstance(test.get("summary"), str):
            errors.append(f"tests[{i}].summary must be a string (the last line of the output)")
    needs = obj.get("needs_main")
    if not isinstance(needs, list) or any(not isinstance(x, (str, dict)) for x in needs):
        errors.append("needs_main must be a list (strings, or {path, diff} objects; [] when none)")
    if status == ENVELOPE_DONE:
        codes = [t.get("exit_code") for t in tests if isinstance(t, dict)]
        if not codes:
            errors.append("status is done but no test is recorded: run the task's Test and record it")
        elif any(c != 0 for c in codes):
            errors.append(f"status is done but a recorded test failed (exit codes {codes}): "
                          f"fix it, or say partial")
    return errors


def read_stub(path: Path) -> dict:
    """One dispatch stub as {task_id, agent, dispatched_at, worktree}; a missing field reads
    as "" and the task id falls back to the file name. Never raises."""
    data = read_json(path, {})
    data = data if isinstance(data, dict) else {}
    name = Path(path).name
    fallback = name[:-len(".stub.json")] if name.endswith(".stub.json") else Path(path).stem
    when = next((str(data[k]) for k in STUB_TIME_KEYS if data.get(k)), "")
    return {"task_id": str(data.get("task_id") or fallback),
            "agent": str(data.get("agent") or data.get("agent_id") or ""),
            "dispatched_at": when,
            "worktree": str(data.get("worktree") or "")}


def write_stub(task_id: str, agent: str, worktree: str | None = None) -> Path:
    """The dispatch stub, written by the lead before it dispatches (statectl dispatch): makes
    an in-flight agent visible, and dates the dispatch for the builder stop check."""
    stub = {"task_id": task_id, "agent": agent, "dispatched_at": utc_now()}
    if worktree:
        stub["worktree"] = Path(worktree).as_posix()
    return atomic_write_json(stub_path(task_id), stub)


# --------------------------------------------------------------------------- orchestration
#
# fableous-orchestrated's runtime state (state/orchestration.json): when each sub-agent began
# (the builder stop check's "since") and how many code edits the lead made since the last
# dispatch (the delegation nudge). Nothing here acts in freestyle or guided-solo, and the hooks
# that call it fail open: a broken counter costs a reminder, never a tool call. The same store
# keeps the periodic progress report's last time (`report`, both organised modes; see below).

ORCHESTRATION_DEFAULTS = {"nudge_after": 8, "handoff_test_timeout": 1800}
AGENT_STARTS_KEPT = 64
# Edits the delegation nudge does not count: bookkeeping and prose are the lead's own work.
NUDGE_EXEMPT_PREFIXES = (".claude/state/", ".claude/tasks/", "docs/")
NUDGE_EXEMPT_SUFFIXES = (".md", ".rst", ".txt")
_WORKTREE_PREFIX_RE = re.compile(r"^\.claude/worktrees/[^/]+/")
# Timestamps are second-precision; a builder runs for minutes.
STOP_SLACK_SECONDS = 2.0


def orchestration_knob(name: str) -> int:
    """An integer knob from config/orchestration.json, the shipped default when absent or bad."""
    default = ORCHESTRATION_DEFAULTS[name]
    cfg = load_config("orchestration")
    try:
        return int(cfg.get(name, default)) if isinstance(cfg, dict) else default
    except (TypeError, ValueError):
        return default


def orchestration_state_path() -> Path:
    return state_dir() / "orchestration.json"


def orchestration_state() -> dict:
    data = read_json(orchestration_state_path(), {})
    return data if isinstance(data, dict) else {}


def _epoch(ts):
    dt = parse_ts(ts) if isinstance(ts, str) else None
    return dt.timestamp() if dt else None


def _is_subagent(payload: dict) -> bool:
    """Same identity rule as the policy gate: only an ABSENT agent_type/agent_name is the lead."""
    return bool(str(payload.get("agent_type") or "").strip()
                or str(payload.get("agent_name") or "").strip())


def payload_rel_path(payload: dict):
    """The repo-relative POSIX path a Write/Edit payload targeted; None when it names no file
    or a file outside the project."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    target = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    if not target:
        return None
    root = str(project_root())
    cwd = str(payload.get("cwd") or root)
    full = os.path.realpath(os.path.join(cwd, os.path.expanduser(str(target))))
    prefix = root.rstrip("\\/") + os.sep
    if not os.path.normcase(full).startswith(os.path.normcase(prefix)):
        return None
    return full[len(prefix):].replace("\\", "/")


def nudge_counts(rel: str) -> bool:
    """Whether an edit to repo-relative `rel` is code the nudge counts. A worktree path is
    judged by its path inside the worktree."""
    inner = _WORKTREE_PREFIX_RE.sub("", rel)
    return not (inner.startswith(NUDGE_EXEMPT_PREFIXES)
                or inner.lower().endswith(NUDGE_EXEMPT_SUFFIXES))


def note_subagent_start(payload: dict) -> None:
    """SubagentStart, fableous-orchestrated only: remember when this agent began (the builder
    stop check's "since") and reset the lead's edit counter, since a dispatch is exactly what
    the nudge asks for."""
    if not isinstance(payload, dict) or current_mode() != ORCHESTRATED_MODE:
        return
    state = orchestration_state()
    now = utc_now()
    agents = state.get("agents") if isinstance(state.get("agents"), dict) else {}
    agent_id = str(payload.get("agent_id") or "").strip()
    if agent_id:
        agents[agent_id] = {"type": str(payload.get("agent_type") or ""), "started_at": now}
        if len(agents) > AGENT_STARTS_KEPT:
            ordered = sorted(agents.items(), key=lambda kv: str(
                kv[1].get("started_at", "") if isinstance(kv[1], dict) else ""))
            agents = dict(ordered[-AGENT_STARTS_KEPT:])
    state["agents"] = agents
    state["nudge"] = {"count": 0, "since": now}
    atomic_write_json(orchestration_state_path(), state)


def delegation_nudge(payload: dict):
    """PostToolUse on the lead's Write/Edit, fableous-orchestrated only: count code edits since
    the last sub-agent dispatch; when the count reaches orchestration.nudge_after (0 = off),
    return one reminder to delegate and start counting again. Returns text, decides nothing."""
    if not isinstance(payload, dict) or _is_subagent(payload):
        return None
    rel = payload_rel_path(payload)
    if rel is None or not nudge_counts(rel):
        return None
    limit = orchestration_knob("nudge_after")
    if limit <= 0 or current_mode() != ORCHESTRATED_MODE:
        return None
    state = orchestration_state()
    nudge = state.get("nudge") if isinstance(state.get("nudge"), dict) else {}
    try:
        count = int(nudge.get("count", 0)) + 1
    except (TypeError, ValueError):
        count = 1
    if count < limit:
        state["nudge"] = {**nudge, "count": count}
        atomic_write_json(orchestration_state_path(), state)
        return None
    state["nudge"] = {"count": 0, "since": nudge.get("since") or "", "nudged_at": utc_now()}
    atomic_write_json(orchestration_state_path(), state)
    return (f"DELEGATION NUDGE (advisory): {count} code edits by the lead since the last "
            f"sub-agent dispatch, the latest {rel}. In fableous-orchestrated mode the lead plans, "
            f"reviews and merges, and a builder implements in a worktree "
            f"(.claude/protocols/orchestration.md). If this task is small enough to do yourself, "
            f"carry on and close it with `statectl task <id> --status done --no-envelope "
            f"\"<why>\"`. Tune or silence it: nudge_after in .claude/config/orchestration.json "
            f"(0 = off).")


def agent_started_at(payload: dict):
    """When this sub-agent began, as epoch seconds: its SubagentStart record, else the first
    timestamp in its transcript, else None (unknown)."""
    agent_id = str(payload.get("agent_id") or "").strip()
    agents = orchestration_state().get("agents")
    rec = agents.get(agent_id) if agent_id and isinstance(agents, dict) else None
    if isinstance(rec, dict) and _epoch(rec.get("started_at")) is not None:
        return _epoch(rec.get("started_at"))
    transcript = payload.get("agent_transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return None
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as fh:
            for _ in range(20):
                line = fh.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                when = _epoch(obj.get("timestamp")) if isinstance(obj, dict) else None
                if when is not None:
                    return when
    except OSError:
        return None
    return None


def _delivered_at(task_id: str, worktree: str = "", builder: bool = True):
    """mtime of the newest VALID builder envelope for task_id, in the project or any of its
    worktrees (a builder writes its envelope inside its worktree); None when there is none.
    builder=False accepts any valid envelope (a scout's or a verifier's)."""
    root = project_root()
    candidates = [envelope_path(task_id, root)]
    if worktree:
        candidates.append(envelope_path(task_id, root / worktree))
    wt_dir = root / ".claude" / "worktrees"
    if wt_dir.is_dir():
        candidates += [envelope_path(task_id, d) for d in sorted(wt_dir.iterdir()) if d.is_dir()]
    best = None
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        obj = read_json(path, None)
        if obj is None or validate_envelope(obj, builder=True if builder else None):
            continue
        best = mtime if best is None else max(best, mtime)
    return best


def builder_stop_reason(payload: dict):
    """SubagentStop for a builder, fableous-orchestrated only: the reason to send it back once,
    when it is stopping with no valid envelope written since it started for any open builder
    stub; None to let it stop. A stub dispatched after this agent began belongs to another
    builder; one answered before it began is closed. Best effort by design: an unknown start
    time, a set stop_hook_active (already sent back once) or any other gap lets it stop."""
    if not isinstance(payload, dict) or payload.get("stop_hook_active"):
        return None
    if str(payload.get("agent_type") or "").strip() != "builder":
        return None
    if current_mode() != ORCHESTRATED_MODE:
        return None
    started = agent_started_at(payload)
    hs = handshakes_dir()
    if started is None or not hs.is_dir():
        return None
    pending = []
    for path in sorted(hs.glob("*.stub.json")):
        stub = read_stub(path)
        if stub["agent"] != "builder":
            continue
        dispatched = _epoch(stub["dispatched_at"])
        if dispatched is None:
            try:
                dispatched = path.stat().st_mtime
            except OSError:
                continue
        if dispatched > started + STOP_SLACK_SECONDS:
            continue
        delivered = _delivered_at(stub["task_id"], stub["worktree"])
        if delivered is not None and delivered >= dispatched - STOP_SLACK_SECONDS:
            if delivered >= started - STOP_SLACK_SECONDS:
                return None
            continue
        pending.append(stub["task_id"])
    if not pending:
        return None
    return (f"HANDOFF MISSING: you are a builder in fableous-orchestrated mode and no valid "
            f"handoff envelope was written since you started (open builder task: "
            f"{', '.join(pending)}). Before you stop, write .claude/state/handshakes/<task_id>.json "
            f"inside your worktree per .claude/protocols/handshake.md: agent_id, task_id, status "
            f"(done|partial|blocked), agent, model, files_changed[], tests[] {{command, "
            f"exit_code, summary}}, needs_main[]. Check it from your worktree with "
            f"`python3 .claude/tools/checkctl.py handoff <task_id>`. Unfinished or blocked is a "
            f"valid handoff: say so with status partial or blocked. This reminder comes once.")


# --------------------------------------------------------------------------- long-run progress
#
# Two telemetry halves of the progress model (tools/progress.py), both fail open.
#
# The ACTIVITY PULSE: heartbeat.json was written only by the Stop hook, so through a multi-hour
# turn "last activity" went stale. Hooks that already run on every tool call (the policy gate,
# after its decision) and on sub-agent start/stop (the capture hook) call activity_pulse(),
# which rewrites the heartbeat as {ts, note: "working", via} once it is older than
# progress.pulse_seconds. The Stop hook's "turn ended" note is superseded on the next call, so
# the two stay distinguishable: working, last activity 40s ago versus turn ended 13 min ago.
#
# The PERIODIC REPORT: no hook fires on a timer, so progress_report() rides the advisory
# channel below. In guided-solo and fableous-orchestrated, on the lead's hook call after
# progress.report_minutes have passed since the last one, it hands the lead the compact text
# block with one instruction: post it to the user as is. The last report time lives in the
# orchestration runtime store.

PROGRESS_DEFAULTS = {"pulse_seconds": 60, "report_minutes": 30}
PULSE_NOTE = "working"
TURN_ENDED_NOTE = "turn ended"
# Sub-agent events the capture hook pulses on (it already runs there; no new process).
PULSE_EVENTS = ("SubagentStart", "SubagentStop")


def heartbeat_path() -> Path:
    return state_dir() / "heartbeat.json"


def progress_knob(name: str) -> int:
    """An integer from orchestration.json's `progress` object, the shipped default when absent
    or bad. 0 (or less) turns the feature off."""
    default = PROGRESS_DEFAULTS[name]
    cfg = load_config("orchestration")
    node = cfg.get("progress") if isinstance(cfg, dict) else None
    if not isinstance(node, dict):
        return default
    try:
        return int(node.get(name, default))
    except (TypeError, ValueError):
        return default


def activity_pulse(via: str) -> bool:
    """Refresh heartbeat.json mid-turn: True when it wrote. Not due while the last working pulse
    is younger than progress.pulse_seconds (0 = off); a heartbeat that is missing, unreadable or
    a Stop hook's "turn ended" is due at once. `via` is a tool or event NAME, never its input.
    Callers swallow every exception: a broken pulse loses a beat, never a tool call."""
    seconds = progress_knob("pulse_seconds")
    if seconds <= 0 or not claude_dir().is_dir():
        return False
    path = heartbeat_path()
    beat = read_json(path, None)
    if isinstance(beat, dict) and beat.get("note") == PULSE_NOTE:
        age = age_seconds(beat.get("ts"))
        if age is not None and age < seconds:
            return False
    atomic_write_json(path, {"ts": utc_now(), "note": PULSE_NOTE,
                             "via": slugify(via or "tool", 48)})
    return True


def progress_report(payload: dict):
    """PostToolUse on the lead's call, guided-solo and fableous-orchestrated only: the compact
    progress block with an instruction to post it, when progress.report_minutes (0 = off) have
    passed since the last report (none yet counts as due); None otherwise. Never for a
    sub-agent, never in freestyle, never for an empty model (no milestone or no tasks)."""
    if not isinstance(payload, dict) or _is_subagent(payload):
        return None
    minutes = progress_knob("report_minutes")
    if minutes <= 0:
        return None
    mode = current_mode()
    if mode not in ORGANISED_MODES:
        return None
    report = orchestration_state().get("report")
    last = _epoch(report.get("last_at")) if isinstance(report, dict) else None
    import time  # local: only this check needs the wall clock as a float
    if last is not None and time.time() - last < minutes * 60:
        return None
    import progress  # local: only a due report pays for the model
    model = progress.compute()
    if model.get("empty"):
        return None
    block = progress.render_text(model, unicode=True)
    state = orchestration_state()
    state["report"] = {"last_at": utc_now()}
    atomic_write_json(orchestration_state_path(), state)
    return (f"PROGRESS REPORT (advisory, every {minutes} min in {mode_label(mode)}): post this "
            f"progress block to the user as is, then continue.\n```\n{block}\n```\n"
            f"(On demand: `python3 .claude/tools/statectl.py progress`. Tune or silence it: "
            f"progress.report_minutes in .claude/config/orchestration.json, 0 = off.)")


# --------------------------------------------------------------------------- advisory channel
#
# How a hook hands the lead a note WITHOUT deciding anything: the note rides
# hookSpecificOutput.additionalContext on an exit-0 run, so it can never block, deny or undo
# what a gate decided. lead_advisories() collects every note due on this hook call (the
# delegation nudge and the periodic progress report), each source isolated so a broken one
# costs only its own note.

def lead_advisories(payload: dict) -> list:
    notes = []
    for source in (delegation_nudge, progress_report):
        try:
            note = source(payload)
        except Exception:  # noqa: BLE001 - advisory: a broken source loses its note, nothing else
            note = None
        if note:
            notes.append(str(note))
    return notes


def advisory_output(event: str, notes):
    """The JSON a hook prints to hand the model its notes as additional context; None when
    there is nothing to say."""
    texts = [str(n).strip() for n in (notes or ()) if str(n or "").strip()]
    if not texts:
        return None
    return json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                              "additionalContext": "\n\n".join(texts)}})


# --------------------------------------------------------------------------- observability

def obs_enabled() -> bool:
    cfg = load_config("observe")
    return bool(cfg.get("enabled", True))


def obslog(event: str, **attrs) -> None:
    """Opt-in decorator lane: record a tool-level event into the raw spool.

    Telemetry fails open, always (law 2). Any failure here - unwritable record root,
    full disk, bad JSON - is swallowed: a broken observatory must never break the work.
    """
    try:
        if not obs_enabled():
            return
        session = os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_IFF_SESSION") or "tools"
        import time  # local: only the decorator lane needs it

        payload = {
            "_obs_ts": utc_now(),
            "_obs_source": "decorator",
            "_obs_uid": f"{time.time_ns()}-{os.getpid()}",
            "hook_event_name": event,
            "session_id": session,
        }
        payload.update(attrs)
        spool = record_paths()["spool"] / f"{slugify(session)}.jsonl"
        append_jsonl(spool, payload)
    except Exception:  # noqa: BLE001 - deliberate: telemetry never raises
        return


# --------------------------------------------------------------------------- misc

def git_output(args: list, root: Path | None = None) -> str:
    """Run a read-only git command, returning '' on any failure (git may be absent)."""
    import subprocess  # local import: hooks that never call git do not pay for it
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(root or project_root()),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return res.stdout.strip() if res.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def project_name() -> str:
    return project_root().name


def find_bash():
    """The bash that can run this system's hooks, as a full path, or None.

    On Windows a bare "bash" is not safe to launch: process creation searches System32 before
    PATH, and the bash.exe in System32 is the WSL launcher, which fails when no distro is installed
    (GitHub's Windows runners, many laptops). Claude Code runs hooks under Git Bash, so that is
    the one to find: a PATH hit outside the Windows folders first, then Git's install folders.
    """
    import shutil
    if os.name != "nt":
        return shutil.which("bash")
    windir = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))

    def usable(path):
        return bool(path) and os.path.isfile(path) and not os.path.normcase(
            os.path.abspath(path)).startswith(windir) and "windowsapps" not in path.lower()

    hit = shutil.which("bash")
    if usable(hit):
        return hit
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(folder.strip('"'), "bash.exe")
        if usable(cand):
            return cand
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        for rel in (("Git", "bin", "bash.exe"), ("Git", "usr", "bin", "bash.exe")):
            cand = os.path.join(base or "", *rel)
            if usable(cand):
                return cand
    return None


def system_version() -> str:
    return str(load_config("registry").get("system_version", "0.0.0"))


# memory.json `visibility`: is the agent system committed with the project (tracked) or kept
# local to this checkout (ignored)? One reader for distctl (the managed .gitignore block, the
# export) and checkctl (gitignore_shadowing), so the three can never disagree.
VISIBILITY_VALUES = ("tracked", "ignored")
DEFAULT_VISIBILITY = "tracked"


def visibility(root: Path | None = None) -> str:
    """The `visibility` knob. Absent reads as "tracked", the behaviour every install had before
    the knob existed. Any other value raises LibError: a tool that writes ignore rules or
    publishes files must not guess which of the two was meant."""
    cfg = read_json((root or project_root()) / ".claude" / "config" / "memory.json", {})
    value = cfg.get("visibility", DEFAULT_VISIBILITY) if isinstance(cfg, dict) else DEFAULT_VISIBILITY
    if value not in VISIBILITY_VALUES:
        raise LibError(f"memory.json visibility is {value!r}; expected one of "
                       f"{', '.join(VISIBILITY_VALUES)}")
    return value


# The release grammar: X.Y.Z (stable) or X.Y.Z-(alpha|beta|rc).N (pre-release), no leading
# zeros. A tag is "v" + version and a CHANGELOG heading is "## v<version> - YYYY-MM-DD". ONE
# parser for the stamp, the tag filter, the changelog parity check and release.yml, so the
# four can never disagree about what a version is.
_VERSION_RE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.(0|[1-9]\d*))?")


def parse_version(text, tag: bool = False) -> dict | None:
    """'0.3.0-alpha.1' -> {major, minor, patch, pre, pre_n, prerelease}; None when `text` is not
    the grammar. tag=True demands (and strips) the leading 'v'."""
    s = str(text or "")
    if tag:
        if not s.startswith("v"):
            return None
        s = s[1:]
    m = _VERSION_RE.fullmatch(s)
    if not m:
        return None
    major, minor, patch, pre, pre_n = m.groups()
    return {"major": int(major), "minor": int(minor), "patch": int(patch), "pre": pre,
            "pre_n": int(pre_n) if pre_n is not None else None, "prerelease": pre is not None}


def is_prerelease(text, tag: bool = False) -> bool:
    parsed = parse_version(text, tag=tag)
    return bool(parsed and parsed["prerelease"])


def changelog_section(text: str, tag: str) -> str | None:
    """The body under `## <tag>` in CHANGELOG text, or None when no heading names exactly that
    tag. Exact: the tag must end at whitespace or end of line, so v3.0 never matches
    '## v3.0.1' and v0.3.0 never matches '## v0.3.0-alpha.1' (a bare \\b allowed both)."""
    m = re.search(rf"^## {re.escape(tag)}(?:[ \t][^\n]*)?$\n?(.*?)(?=^## |\Z)",
                  text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else None


def print_verdict(tag: str, ok: bool, warn: bool = False) -> None:
    """Emit the verdict token convention: <TAG>_OK | <TAG>_WARN | <TAG>_FAIL.

    Callers and hooks grep for these; a missing token is treated as failure by the
    fail-closed validators, so always print exactly one.
    """
    state = "OK" if ok and not warn else ("WARN" if warn and ok else "FAIL")
    print(f"{tag}_{state}")


def main(argv: list) -> int:
    if "--record-root" in argv:
        print(record_root())
    elif "--project-root" in argv:
        print(project_root())
    elif "--release-kind" in argv:
        # For release.yml: prints prerelease|stable for a vX.Y.Z[-(alpha|beta|rc).N] tag, and
        # exits 1 on anything else, so a malformed tag fails the release rather than
        # publishing as stable and moving "latest".
        idx = argv.index("--release-kind")
        tag = argv[idx + 1] if idx + 1 < len(argv) else ""
        parsed = parse_version(tag, tag=True)
        if not parsed:
            print(f"invalid release tag {tag!r}: expected vX.Y.Z or vX.Y.Z-(alpha|beta|rc).N",
                  file=sys.stderr)
            return 1
        print("prerelease" if parsed["prerelease"] else "stable")
    elif "--paths-json" in argv:
        rp = {k: str(v) for k, v in record_paths().items()}
        print(json.dumps({
            "project_root": str(project_root()),
            "claude_dir": str(claude_dir()),
            "iff_dir": str(iff_dir()),
            "record": rp,
        }, indent=2))
    else:
        print(__doc__.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
