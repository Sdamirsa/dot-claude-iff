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
    python3 _lib.py --record-root | --project-root | --paths-json
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
    "task",           # task state                          {id, title, status, deps, note}
    "milestone",      # something shipped                   {id, title, note}
    "decision",       # a choice and its reason             {text, why}
    "loop",           # an open/closed thread               {id, text, status}
    "note",           # free narration                      {text}
    "intent",         # write-ahead bracket for composite ops {state, intent_id, op, files}
    "config",         # a config value changed              {changes, via}
    "gate",           # a human gate was asked/answered     {question, answer, kind}
    "tooling",        # the .claude system itself changed   {change_type, what, evidence}
)

TASK_STATUSES = ("todo", "doing", "done", "blocked")
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
        return "~" + text[len(home):]
    return text


def rel(path: Path, base: Path | None = None) -> str:
    """Repo-relative display path; falls back to the absolute path when outside."""
    b = (base or project_root()).resolve()
    try:
        return str(Path(path).resolve().relative_to(b))
    except ValueError:
        return str(path)


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


def system_version() -> str:
    return str(load_config("registry").get("system_version", "0.0.0"))


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
