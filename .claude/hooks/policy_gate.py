#!/usr/bin/env python3
"""policy_gate.py - the PreToolUse gate's logic. Invoked by policy-gate.sh with ONE argument:
the path to a file containing the hook payload.

The payload arrives as a FILE, not an environment variable. That is not a style choice: a single
env string is capped at MAX_ARG_STRLEN (128 KB on Linux), and a Write payload carries the file's
whole content, so passing it through the environment meant that writing a large enough file to a
protected path made execve fail with E2BIG, python never ran, and the gate emitted no decision,
which the harness reads as ALLOW. The gate was disabled by nothing more exotic than a big file.

Exit codes are the contract with the wrapper:
    0  decision emitted (or explicitly allowed)
    3  this script could not do its job; the wrapper must fail closed on protected paths

After the decision is out, and only then, the activity pulse refreshes state/heartbeat.json
(throttled, telemetry, fails open): this gate already runs on every write and shell call, so a
multi-hour turn keeps a true "last activity" without a new process per call. The pulse cannot
reach the decision: same stdout, same exit code, every failure swallowed (activity_pulse below).

Three rings, enforced on EVERY write lane (Write/Edit/MultiEdit/NotebookEdit by the target path,
Bash and PowerShell by reading the command):
  1. The record (RECORD_ROOT and .claude-iff/) is write-denied to EVERY identity, main session
     included. It is written only by the capture hook's append and by obsctl.
  2. The ritual ticket (.claude/state/ritual-ticket.json) is write-denied to EVERY identity too.
     Only the prompt hook mints it, and a hook is its own process that never passes through
     this gate: a ticket the main session could write would prove nothing about the user.
  3. The protected tree is main-session only, less any per-agent write_paths grant in
     policy.json. It includes every directory whose contents this gate itself executes or
     trusts: hooks, tools, config, agents, protocols, skills, console, settings. A sub-agent
     that can write .claude/tools/_lib.py owns the gate on the next tool call, because that is
     the module this file imports. Agent worktrees (.claude/worktrees/<name>/) are copies, not
     the tree: their paths do not match, which is what lets a builder work in one.

The shell lanes
---------------
A command is judged by the paths it would WRITE, never by the words it merely contains. The
text is lexed the way a shell would (quotes, escapes, substitutions, heredocs; under both the
POSIX and the PowerShell quoting rules, and a write either reading finds counts), each simple
command is followed through its wrappers (sudo, env, xargs, timeout...), and three things are
writes: a redirect target; a path operand of a known writer (rm, cp, mv, tee, sed -i, dd,
Set-Content, Out-File, Copy-Item... see the tables below); and code handed to an interpreter
(bash -c, eval, pwsh -Command, a heredoc fed to a shell or to python), which is read again as
shell, or, when it is not shell, judged opaquely: it names a guarded path AND contains a write
call. Paths resolve the way the OS would: cwd followed through cd and subshells, `..`, `~`,
symlinks, variables assigned earlier in the same command, braces, globs, and on Windows case,
both slash forms and MSYS drive spellings; containment is compared segment by segment.
Destroying or filling a directory that CONTAINS a guarded path (rm -r, mv, rsync, cp -r of a
tree onto it, tar -x -C) is a write to that path.

Fail closed: text that does not lex is judged opaquely; a write whose path cannot be resolved
(a variable set by a substitution, operands that arrive on stdin through xargs or a pipeline,
a cd the gate cannot follow) is denied when the same command names a guarded path anywhere.

Prose is not a write. A heredoc read as data (by cat, git, a script; nothing in the same text
reads stdin as code), a quoted commit message, `echo "rm -rf x"` mention a path without writing
it. The check this replaced denied any line that named the record next to a mutator SUBSTRING,
so "confirm" (rm), "address" (dd), "committee" (tee) and an arrow (>) in a main-session note
were all writes.

Residual, stated rather than hidden: reading a shell statically is not sound against a
determined adversary. A script file written first and then run (or a program that runs its
stdin without being a shell or an interpreter), a base64 payload decoded at run time, a program
missing from the writer tables that writes the path it is handed, a path assembled at run time
inside interpreter code, and an archive or patch applied in the main session's own root all
pass. The Write lane, the git denylist and review of the diff are the other layers.
"""

from __future__ import annotations

import base64
import fnmatch
import glob as globmod
import json
import os
import re
import sys
from pathlib import Path, PurePath

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
# PowerShell is the Windows shell lane: same judgment as Bash, over the same command string.
# Leaving it unmatched left every ring open to one tool on one platform.
SHELL_TOOLS = ("Bash", "PowerShell")

FALLBACK_PROTECTED = (
    ".claude/hooks/",
    ".claude/tools/",
    ".claude/config/",
    ".claude/agents/",
    ".claude/protocols/",
    ".claude/skills/",
    ".claude/console/",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude-iff/",
)

# Written by no identity in any lane, the main session included (ring 2). Built in rather than
# configured, so a broken or edited policy.json cannot drop it.
EVERY_IDENTITY_DENY = (".claude/state/ritual-ticket.json",)

# A single, simple, read-only git invocation. Anchored and flag-tolerant, but it refuses
# anything with a shell operator in it, so `git log && git push` can never match.
# symbolic-ref is deliberately absent (here AND in policy.json's read_only_subcommands,
# which must stay in step): its two-argument form writes the ref.
READONLY_GIT = re.compile(
    r"^\s*git\s+(?:-[-\w]+(?:[= ]\S+)?\s+)*"
    r"(?:diff|log|show|status|ls-files|rev-parse|blame|describe|shortlog|cat-file|grep|"
    r"show-ref|for-each-ref|count-objects|var)\b[^;&|`$]*$"
)


def _names(text: str) -> frozenset:
    return frozenset(text.split())


# ---- what the shell lanes know about programs ----------------------------------------------
# Names are matched lowercased, path and .exe stripped, so /bin/rm, RM and rm.exe are rm; the
# PowerShell aliases (ri, cpi, sc, ni...) sit next to the cmdlets they stand for.

# Every path operand is written. "tree": a directory operand is written all the way down, so
# naming a directory that CONTAINS a guarded path counts. "flag": only with -R/-r/-Recurse.
WRITERS = {
    **dict.fromkeys(_names("rm rmdir unlink shred srm del erase rd ri remove-item "
                           "dos2unix unix2dos rustfmt"), "tree"),
    **dict.fromkeys(_names("touch mkdir md mkfifo mknod truncate tee tee-object set-content sc "
                           "add-content ac clear-content clc out-file new-item ni set-item si "
                           "mktemp split csplit dd wget mklink attrib zip compress-archive "
                           "export-csv export-clixml set-acl"), "file"),
    **dict.fromkeys(_names("chmod chown chgrp chattr setfacl icacls gzip gunzip bzip2 bunzip2 "
                           "xz unxz zstd unzstd"), "flag"),
}
# Writers only when a flag says so: name -> (test over the joined args, kind).
_FIX = re.compile(r"(?:^|\s)(?:--write|-w|--fix|-i|--in-place|--apply)(?:[\s=]|$)")
_UNLESS_CHECK = re.compile(r"^(?!.*--(?:check|diff)\b)", re.S)
GATED = {
    "curl": (re.compile(r"(?:^|\s)(?:-[A-Za-z]*[oOD]|--output|--output-dir|--remote-name|"
                        r"--dump-header|--cookie-jar|-c)(?:[\s=]|$)"), "file"),
    **dict.fromkeys(_names("invoke-webrequest iwr invoke-restmethod irm"),
                    (re.compile(r"(?i)(?:^|\s)-o"), "file")),
    "sort": (re.compile(r"(?:^|\s)(?:-o|--output)"), "file"),
    "ruff": (re.compile(r"(?:^|\s)(?:format|--fix)(?:\s|$)"), "tree"),
    **dict.fromkeys(_names("black isort"), (_UNLESS_CHECK, "tree")),
    **dict.fromkeys(_names("prettier eslint biome clang-format gofmt autopep8 yapf"),
                    (_FIX, "tree")),
}
COPIERS = _names("cp copy cpi copy-item scp ln install")
MOVERS = _names("mv move mi move-item rename ren rni rename-item")
TREE_COPIERS = _names("rsync robocopy xcopy")
PS_ITEM = _names("copy cpi copy-item move mi move-item ren rni rename-item")
EXTRACTORS = _names("tar bsdtar unzip 7z 7za expand-archive cpio patch")

SHELLS = _names("bash sh zsh dash ksh mksh ash fish pwsh cmd")
INTERPRETERS = _names("python node nodejs deno bun perl ruby php lua rscript osascript")
AWKS = _names("awk gawk mawk nawk")
EVALS = _names("eval iex invoke-expression")
SOURCES = frozenset({"source", "."})
CDS = _names("cd chdir pushd set-location sl push-location")
POPS = _names("popd pop-location")
WRAPPERS = _names("sudo doas env nohup nice ionice timeout stdbuf command builtin exec xargs "
                  "parallel watch strace ltrace chronic unbuffer setsid flock busybox uv uvx "
                  "poetry pipenv conda npx pnpx start start-process noglob nocorrect")
# Programs that only read, listed so a wrapper finds them (`sudo -u bob cat x` runs cat).
READERS = _names("cat head tail less more wc grep egrep fgrep rg cut tr column jq yq diff cmp "
                 "ls dir stat file du echo printf get-content gc type get-childitem gci")
KEYWORDS = _names("{ } ! if then else elif fi do done while until time coproc esac")
INLINE = {
    "python": re.compile(r"-[A-Za-z]*c"),
    **dict.fromkeys(_names("node nodejs bun"), re.compile(r"-e|--eval|-p|--print")),
    "deno": re.compile(r"-e|--eval|eval"),
    "perl": re.compile(r"-[A-Za-z]*[eE]"),
    "ruby": re.compile(r"-[A-Za-z]*e"),
    "php": re.compile(r"-r"),
    **dict.fromkeys(_names("lua rscript osascript"), re.compile(r"-e")),
}
MUTATING = (frozenset(WRITERS) | frozenset(GATED) | COPIERS | MOVERS | TREE_COPIERS
            | EXTRACTORS | SHELLS | INTERPRETERS | AWKS | EVALS)
KNOWN = MUTATING | SOURCES | CDS | POPS | WRAPPERS | READERS | {"find", "git", "sed"}

# ---- lexing --------------------------------------------------------------------------------
SUB = "\x00"  # stands in a word for a command substitution, read separately
GLOB_CHARS = frozenset("*?[")
NULL_SINKS = _names("/dev/null /dev/stdout /dev/stderr /dev/tty /dev/fd/1 /dev/fd/2 $null "
                    "nul nul: con con:")
OPERATORS = ("<<<", "&>>", "<<", ">>", "&>", ">|", ">&", "<&", "<>", "&&", "||", "|&", ";;",
             ">", "<", "|", "&", ";", "(", ")", "\n")
REDIRECT = {">": "target", ">>": "target", ">|": "target", "&>": "target", "&>>": "target",
            "<>": "target", ">&": "dup", "<": "skip", "<&": "skip", "<<": "skip",
            "<<<": "here"}
HEREDOC = re.compile(r"(?<!<)<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z_][\w.-]*))")
ASSIGN = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.S)
PS_ASSIGN = re.compile(r"^\$(?:env:)?([A-Za-z_]\w*)=(.*)$", re.S | re.I)
VAR = re.compile(r"\$(?:env:([A-Za-z_]\w*)|\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))", re.I)
PCT_VAR = re.compile(r"%([A-Za-z_]\w*)%")
UNRESOLVED = re.compile(r"[$`\x00]|%[A-Za-z_]\w*%")
MSYS = re.compile(r"^/(?:cygdrive/)?([A-Za-z])(?:/(.*))?$", re.S)
BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")
RECURSE = re.compile(r"(?i)-[a-z]*r[a-z]*|--recursive|/[ts]")
COPY_RECURSE = re.compile(r"(?i)-[a-z]*[ra][a-z]*|--recursive|--archive")
SED_WRITE = re.compile(r"(?:^|[;\n}/])[0-9gpIiMme]*[wW]\s+(\S+)")
# Opaque code: a guarded path named next to any of these is a write.
WRITE_HINT = re.compile(
    r"(?i)>"
    r"|(?<![\w-])(?:rm|mv|cp|ln|dd|tee|del|erase|rd|md|ren|ni|sc|ac|ri|mi|cpi|rni|clc|si|"
    r"install|rsync|shred|set-content|add-content|clear-content|out-file|new-item|set-item)"
    r"(?![\w-])"
    r"|write|append|remove|unlink|rmtree|rmdir|rename|replace|move|copy|mkdir|makedirs|touch|"
    r"chmod|chown|truncate|symlink|hardlink|delete|dump\s*\(|system\s*\(|popen|subprocess|"
    r"spawn|exec|os\.open"
    r"|['\"][rbt]*[wax][rbt+]*['\"]|['\"]r\+['\"]"
)
PATHISH = re.compile(r"[^\s'\"`(),;|&<>=\[\]{}+]+")
JOINED = re.compile(r"""['"]\s*(?:,|\+|\)?\s*/)\s*['"]""")
# .NET calls that write, which PowerShell reaches without any cmdlet.
DOTNET = re.compile(
    r"(?i)(?:\]::|\)\s*\.|\$[\w:]+\.)\s*(?:delete|moveto|copyto|create\w*|appendtext|openwrite|"
    r"write\w*|append\w*|replace|move|copy|open|encrypt|decrypt|setaccesscontrol)\s*\("
    r"|new-object\s+(?:system\.)?io\."
)


class _Open(Exception):
    """A quote or substitution that never closes."""


def _close(text: str, i: int, dialect: str) -> int:
    """Index just past the ')' that closes the '(' at text[i], skipping quoted spans."""
    esc = "\\" if dialect == "sh" else "`"
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == esc:
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise _Open
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == esc else 1
            if j >= n:
                raise _Open
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise _Open


def _substitution(text: str, i: int, dialect: str, quoted: bool):
    """(inner text, end index) when a command or process substitution starts at text[i]."""
    c, nxt = text[i], text[i + 1:i + 2]
    if nxt == "(" and (c == "$" or (dialect == "ps" and c == "@")
                       or (dialect == "sh" and not quoted and c in "<>")):
        end = _close(text, i + 1, dialect)
        return text[i + 2:end - 1], end
    if dialect == "sh" and c == "`":
        j = i + 1
        while j < len(text) and text[j] != "`":
            j += 2 if text[j] == "\\" else 1
        if j >= len(text):
            raise _Open
        return text[i + 1:j], j + 1
    return None


def lex(text: str, dialect: str):
    """Words (quotes removed) and operators, plus the inner texts of substitutions, under
    POSIX ("sh": backslash escapes, backticks substitute) or PowerShell ("ps": backtick
    escapes, backslash is a path character) rules. None when quoting does not balance."""
    toks, subs, word = [], [], []
    started = False
    esc = "\\" if dialect == "sh" else "`"
    i, n = 0, len(text)

    def flush() -> None:
        nonlocal started
        if started:
            toks.append(("w", "".join(word)))
        word.clear()
        started = False

    try:
        while i < n:
            c = text[i]
            if c == esc:
                if text[i + 1:i + 2] == "\n":
                    i += 2
                    continue
                word.append(text[i + 1:i + 2] or c)
                started, i = True, i + 2
                continue
            if c == "'":
                j = text.find("'", i + 1)
                if j < 0:
                    return None
                word.append(text[i + 1:j])
                started, i = True, j + 1
                continue
            if c == '"':
                started, i = True, i + 1
                while True:
                    if i >= n:
                        return None
                    d = text[i]
                    if d == '"':
                        i += 1
                        break
                    if d == esc and i + 1 < n:
                        if dialect == "sh" and text[i + 1] not in '"\\$`\n':
                            word.append(d)
                            i += 1
                        else:
                            word.append(text[i + 1])
                            i += 2
                        continue
                    found = _substitution(text, i, dialect, True)
                    if found:
                        subs.append(found[0])
                        word.append(SUB)
                        i = found[1]
                        continue
                    word.append(d)
                    i += 1
                continue
            found = _substitution(text, i, dialect, False)
            if found:
                subs.append(found[0])
                word.append(SUB)
                started, i = True, found[1]
                continue
            if c in " \t\r":
                flush()
                i += 1
                continue
            if c in ";&|()<>\n":
                flush()
                op = next(o for o in OPERATORS if text.startswith(o, i))
                toks.append(("op", op))
                i += len(op)
                continue
            word.append(c)
            started, i = True, i + 1
    except _Open:
        return None
    flush()
    return toks, subs


def split_heredocs(text: str):
    """(text with heredoc bodies removed, the bodies). A body is read separately: as data when
    it feeds cat or git, as code when it feeds a shell or an interpreter."""
    lines = text.split("\n")
    kept, bodies, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        i += 1
        kept.append(line)
        for m in HEREDOC.finditer(line):
            strip = m.group(1) == "-"
            delim = next(g for g in m.groups()[1:] if g is not None)
            body = []
            while i < len(lines):
                cand = lines[i]
                i += 1
                if (cand.lstrip("\t") if strip else cand).rstrip("\r") == delim:
                    break
                body.append(cand)
            bodies.append("\n".join(body))
    return "\n".join(kept), bodies


class Cmd:
    """One simple command: its words, redirect targets, heredoc count, herestrings and the
    substitutions its words contain."""

    __slots__ = ("words", "targets", "heredocs", "herestrings", "subs")

    def __init__(self, words=None) -> None:
        self.words = list(words or [])
        self.targets, self.herestrings, self.subs = [], [], []
        self.heredocs = 0

    def __bool__(self) -> bool:
        return bool(self.words or self.targets or self.heredocs or self.herestrings or self.subs)


def parse(toks: list, subs: list) -> list:
    """Simple commands in order, with "(" / ")" markers for subshells."""
    queue = list(subs)
    out, cmd, expect = [], Cmd(), None
    for kind, val in toks:
        if kind == "w":
            n = val.count(SUB)
            cmd.subs.extend(queue[:n])
            del queue[:n]
            if expect == "target" or (expect == "dup" and not re.fullmatch(r"\d+-?|-", val)):
                cmd.targets.append(val)
            elif expect == "here":
                cmd.herestrings.append(val)
            elif expect is None:
                cmd.words.append(val)
            expect = None
            continue
        expect = REDIRECT.get(val)
        if val == "<<":
            cmd.heredocs += 1
        if expect is None:
            if cmd:
                out.append(cmd)
            cmd = Cmd()
            if val in ("(", ")"):
                out.append(val)
    cmd.subs.extend(queue)
    if cmd:
        out.append(cmd)
    return out


def prog_name(word: str) -> str:
    base = re.split(r"[/\\]", word)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if base.endswith(ext):
            base = base[:-len(ext)]
            break
    if re.fullmatch(r"python[\d.]*w?|py", base):
        return "python"
    if re.fullmatch(r"powershell(?:_ise)?|pwsh", base):
        return "pwsh"
    return base


def unwrap(words: list):
    """The real program behind sudo/env/xargs/timeout...: (its words onward, operands-on-stdin)."""
    i, fed = 0, False
    while i < len(words) and prog_name(words[i]) in WRAPPERS:
        if prog_name(words[i]) in ("xargs", "parallel"):
            fed = True
        j, first = i + 1, None
        while j < len(words):
            w = words[j]
            if not w.startswith("-") and not ASSIGN.match(w):
                if prog_name(w) in KNOWN:
                    break
                if first is None:
                    first = j
            j += 1
        i = j if j < len(words) else (first if first is not None else len(words))
    return words[i:], fed


def operands(args: list) -> list:
    """Every word that may name a path: non-option words, the value half of --opt=value,
    -Opt:value and dd's of=value, and each item of a PowerShell comma list."""
    out = []
    for a in args:
        if a.startswith("-") and len(a) > 1:
            m = re.match(r"-[^=:]+[=:](.+)", a, re.S)
            if m:
                out.append(m.group(1))
            continue
        out.append(a)
        if "=" in a:
            out.append(a.split("=", 1)[1])
    return out + [part for o in out if "," in o for part in o.split(",") if part]


def _opt_value(args: list, k: int):
    """(value, next index) of the option at args[k], attached (=, :) or as the next word."""
    m = re.match(r"-[^=:]+[=:](.+)", args[k], re.S)
    if m:
        return m.group(1), k + 1
    return (args[k + 1] if k + 1 < len(args) else None), k + 2


# ---- paths ---------------------------------------------------------------------------------
def _fold(path: str) -> tuple:
    """Path segments as the filesystem compares them. Windows: case-insensitive; Win32 drops a
    segment's trailing dot (and the path's trailing spaces), and name:stream is the file
    name. Spaces are stripped from every segment here, which can only over-match."""
    parts = PurePath(path).parts
    if os.name != "nt" or not parts:
        return parts
    out = [parts[0].lower()]
    for part in parts[1:]:
        low = part.lower()
        out.append(low.split(":", 1)[0].rstrip(" .") or low)
    return tuple(out)


def _seg(a: str, b: str) -> bool:
    if a == b:
        return True
    if GLOB_CHARS.intersection(a):
        return fnmatch.fnmatchcase(b, a)
    if GLOB_CHARS.intersection(b):
        return fnmatch.fnmatchcase(a, b)
    return False


def _within(inner: tuple, outer: tuple, globs: bool = True) -> bool:
    return len(inner) >= len(outer) and all(
        x == y or (globs and _seg(x, y)) for x, y in zip(inner, outer))


def _braces(text: str, limit: int = 32) -> list:
    out, todo = [], [text]
    while todo and len(out) < limit:
        t = todo.pop()
        m = BRACES.search(t)
        if not m:
            out.append(t)
            continue
        todo.extend(t[:m.start()] + alt + t[m.end():] for alt in m.group(1).split(","))
    return out + todo


def path_forms(text: str, cwd: str, deep: bool = True, expand: bool = True) -> list:
    """Absolute spellings a path operand may name. Backslashes also read as separators (that
    is how Windows reads them; elsewhere it can only add a false positive, never a miss) and
    MSYS drive paths (/c/x) as drive paths. deep adds symlink resolution and glob matches;
    expand adds brace alternatives and globs, which a shell expands and a Write never does."""
    out = []
    for t in (_braces(text) if expand else [text]):
        variants = [t, t.replace("\\", "/")] if "\\" in t else [t]
        if os.name == "nt":
            variants += [f"{m.group(1)}:/{m.group(2) or ''}"
                         for m in map(MSYS.match, list(variants)) if m]
        for v in variants:
            if v.startswith("~"):
                v = os.path.expanduser(v)
            try:
                joined = os.path.join(cwd, v)
                out.append(os.path.abspath(joined))
            except (OSError, ValueError):
                continue
            if not deep:
                continue
            try:
                out.append(os.path.realpath(joined))
            except (OSError, ValueError):
                pass
            if expand and GLOB_CHARS.intersection(v):
                try:
                    out.extend(os.path.realpath(g) for g in globmod.glob(joined)[:64])
                except (OSError, ValueError, re.error):
                    pass
    return out


def _rooted(text: str) -> bool:
    return text.startswith(("/", "\\", "~")) or bool(re.match(r"[A-Za-z]:[\\/]", text))


def _literal_tail(text: str) -> str:
    """The part of a path after its last segment the gate cannot expand: `$D/x/y` -> `x/y`."""
    segs = re.split(r"[/\\]+", text)
    for k in range(len(segs) - 1, -1, -1):
        if UNRESOLVED.search(segs[k]):
            return "/".join(segs[k + 1:])
    return text


def _literal_head(text: str) -> str:
    """The part of a path before its first segment the gate cannot expand: `.claude/$X/y` ->
    `.claude`. Empty when that part names nothing below cwd (`./$X`, `$X/y`)."""
    segs = re.split(r"[/\\]+", text)
    for k, seg in enumerate(segs):
        if UNRESOLVED.search(seg):
            head = segs[:k]
            return "/".join(head) if any(s not in ("", ".", "..") for s in head) else ""
    return ""


def _basename(text: str) -> str:
    return re.split(r"[/\\]", text.rstrip("/\\"))[-1] if text.rstrip("/\\") else ""


class Zone:
    __slots__ = ("kind", "parts", "label")

    def __init__(self, kind: str, path, label: str) -> None:
        self.kind, self.label = kind, label
        self.parts = _fold(os.path.realpath(str(path)))


class Rings:
    """The guarded paths for one identity, and the grants that carve into the protected tree."""

    def __init__(self, root: Path, zones: list, grants: list) -> None:
        self.root, self.zones, self.grants = str(root), zones, grants

    def hit(self, forms: list, tree: bool = False, kinds=None):
        """(zone, form) for the first guarded path a write to any of forms would touch. tree:
        the write reaches everything below it, so an ancestor of a guarded path counts too."""
        folded = [(f, _fold(f)) for f in forms]
        for z in self.zones:
            if kinds and z.kind not in kinds:
                continue
            for form, parts in folded:
                if not (_within(parts, z.parts) or (tree and _within(z.parts, parts))):
                    continue
                if z.kind == "protected" and any(_within(parts, g, globs=False)
                                                 for g in self.grants):
                    continue
                return z, form
        return None


def build_rings(root: Path, record_root: Path, protected, write_paths, is_main: bool) -> Rings:
    zones = [Zone("record", record_root, str(record_root)),
             Zone("record", root / ".claude-iff", ".claude-iff/")]
    zones += [Zone("ticket", root / rel, rel) for rel in EVERY_IDENTITY_DENY]
    grants = []
    if not is_main:
        zones += [Zone("protected", root / p, p) for p in protected]
        grants = [_fold(os.path.realpath(str(root / g))) for g in write_paths or ()]
    return Rings(root, zones, grants)


# ---- reading a command ---------------------------------------------------------------------
class Level:
    """Heredoc bookkeeping for one piece of shell text: where each `<<` ran (its cwd), and
    whether anything here reads stdin as code ("shell": a shell, source, xargs; "other": an
    interpreter with no script)."""

    __slots__ = ("owners", "code")

    def __init__(self) -> None:
        self.owners, self.code = [], set()


class State:
    __slots__ = ("cwd", "vars")

    def __init__(self, cwd, vars_) -> None:
        self.cwd, self.vars = cwd, vars_


class ShellReader:
    """Reads one command for the paths it would write. Collects; main() decides."""

    MAX_DEPTH = 6

    def __init__(self, rings: Rings, native: str) -> None:
        self.rings, self.native = rings, native
        self.hits = []        # (zone, path form, how)
        self.unresolved = []  # writes whose path the gate could not resolve
        self.named = None     # the first guarded zone the command names anywhere
        self._forms = {}

    def forms(self, text, cwd, deep=True) -> list:
        key = (text, cwd, deep)
        if key not in self._forms:
            self._forms[key] = path_forms(text, cwd, deep)
        return self._forms[key]

    # -- what counts -----------------------------------------------------------------------
    def write(self, text, cwd, tree=False, kinds=None) -> None:
        if not text or text.strip().lower() in NULL_SINKS:
            return
        if UNRESOLVED.search(text) or (cwd is None and not _rooted(text)):
            # Read three ways: as written (a single-quoted $ is literal, e.g. an NTFS
            # name::$DATA stream), its literal head (whatever follows lands below it), and its
            # literal tail against both cwd and root (whatever precedes it is unknown).
            self.unresolved.append(text)
            here = cwd or self.rings.root
            self._check(text, here, tree, kinds)
            head = _literal_head(text)
            if head:
                self._check(head, here, True, kinds)
            tail = _literal_tail(text)
            for base in {here, self.rings.root}:
                if tail:
                    self._check(tail, base, tree, kinds)
            return
        self._check(text, cwd or self.rings.root, tree, kinds)  # cwd is None only when rooted

    def _check(self, text, cwd, tree, kinds) -> None:
        hit = self.rings.hit(self.forms(text, cwd), tree, kinds)
        if hit:
            self.hits.append((hit[0], hit[1], "write"))

    def mention(self, text, cwd) -> None:
        if self.named is not None or not text or UNRESOLVED.search(text):
            return
        deep = bool(re.search(r"[/\\.~:]", text))
        hit = self.rings.hit(self.forms(text, cwd or self.rings.root, deep))
        if hit:
            self.named = hit[0]

    def opaque(self, text, cwd) -> None:
        """Code the gate does not parse as shell: a guarded path named next to a write call."""
        base = cwd or self.rings.root
        writes = WRITE_HINT.search(text) is not None
        # As written first, then with string joins (os.path.join('.claude', 'tools'),
        # '.claude/' + 'tools', Path('.claude') / 'tools') glued back into one path.
        tokens = dict.fromkeys(PATHISH.findall(text) + PATHISH.findall(JOINED.sub("/", text)))
        for tok in tokens:
            if UNRESOLVED.search(tok):
                continue
            for cand in {tok, tok.lstrip("/\\")} - {""}:
                hit = self.rings.hit(self.forms(cand, base, False))
                if not hit:
                    continue
                if self.named is None:
                    self.named = hit[0]
                if writes:
                    self.hits.append((hit[0], hit[1], "opaque"))
                    return

    def verdict(self):
        if self.hits:
            order = {"record": 0, "ticket": 1, "protected": 2}
            return min(self.hits, key=lambda h: order[h[0].kind])
        if self.unresolved and self.named is not None:
            return self.named, self.unresolved[0], "unresolved"
        return None

    # -- structure -------------------------------------------------------------------------
    def level(self, text, cwd, depth, vars_=None, native=None):
        """One piece of shell text: the command itself, or code it hands to a shell. Returns
        the cwd it ends in (None when unknown), for eval and source."""
        native = native or self.native
        if depth > self.MAX_DEPTH:
            self.opaque(text, cwd)
            return None
        stripped, bodies = split_heredocs(text)
        if native == "ps":
            for line in stripped.split("\n"):
                if DOTNET.search(line):
                    self.opaque(line, cwd)
        runs, end = [], None
        for dialect in (native, "ps" if native == "sh" else "sh"):
            lexed = lex(stripped, dialect)
            if lexed is None:
                if dialect == native:
                    self.opaque(stripped, cwd)
                continue
            lvl, st = Level(), State(cwd, dict(vars_ or {}))
            self.walk(lexed, st, depth, lvl, dialect)
            if not runs:
                end = st.cwd
            runs.append(lvl)
        if bodies:
            self.bodies(bodies, runs, depth, native)
        return end

    def bodies(self, bodies, runs, depth, native) -> None:
        # Every `<<` the regex saw must be one the lexer saw unquoted; otherwise a quoted
        # "<<EOF" is hiding real commands on the lines below it.
        counted = bool(runs) and all(len(r.owners) == len(bodies) for r in runs)
        code = set().union(*(r.code for r in runs)) if runs else {"other"}
        if counted and not code:
            return  # prose for cat, git, a script: data, never commands
        for k, body in enumerate(bodies):
            where = runs[0].owners[k] if counted else None
            self.level(body, where, depth + 1, native=native)
            if not counted or "other" in code:
                self.opaque(body, where)

    def walk(self, lexed, st, depth, lvl, dialect) -> None:
        toks, subs = lexed
        stack = []
        for item in parse(toks, subs):
            if item == "(":
                stack.append(st.cwd)
            elif item == ")":
                if stack:
                    st.cwd = stack.pop()
            else:
                self.command(item, st, depth, lvl, dialect)

    def subst(self, text, st) -> str:
        if "$" not in text and "%" not in text:
            return text

        def var(m):
            env_name, braced, plain = m.groups()
            name = env_name or braced or plain
            if not env_name:
                if name in st.vars:
                    return st.vars[name]
                if name == "PWD" and st.cwd:
                    return st.cwd
                if name.lower() in ("null", "_", "psitem", "args", "input"):
                    return m.group(0)
            value = os.environ.get(name)
            return value if value is not None else m.group(0)

        return PCT_VAR.sub(lambda m: os.environ.get(m.group(1), m.group(0)), VAR.sub(var, text))

    def command(self, cmd, st, depth, lvl, dialect) -> None:
        for inner in cmd.subs:
            lexed = lex(inner, dialect) if depth < self.MAX_DEPTH else None
            if lexed is None:
                self.opaque(inner, st.cwd)
            else:  # a subshell: it shares this text's heredocs but not its cwd
                self.walk(lexed, State(st.cwd, dict(st.vars)), depth + 1, lvl, dialect)
        if "{" in cmd.words[1:] or "}" in cmd.words[1:]:
            # A script block (ForEach-Object { Remove-Item $_ }): each part is a command.
            parts, cur = [], []
            for w in cmd.words:
                if w in ("{", "}"):
                    parts.append(cur)
                    cur = []
                else:
                    cur.append(w)
            parts.append(cur)
            cmd.words = parts[0]
            for extra in parts[1:]:
                if extra:
                    self.command(Cmd(extra), st, depth, lvl, dialect)
        words = list(cmd.words)
        while words:
            m = ASSIGN.match(words[0]) or PS_ASSIGN.match(words[0])
            if m:
                st.vars[m.group(1)] = self.subst(m.group(2), st)
                words.pop(0)
            elif len(words) >= 2 and words[1] == "=" and words[0].startswith("$"):
                if len(words) == 3:  # PowerShell: $d = '.claude/tools'
                    st.vars[words[0][1:].split(":")[-1]] = self.subst(words[2], st)
                words = words[2:]
            elif words[0] in KEYWORDS:
                words.pop(0)
            else:
                break
        for w in words + cmd.targets:
            self.mention(self.subst(w, st), st.cwd)
        for t in cmd.targets:
            self.write(self.subst(t, st), st.cwd)
        lvl.owners.extend([st.cwd] * cmd.heredocs)
        chain, fed = unwrap(words)
        if not chain:
            return
        name = prog_name(chain[0])
        args = [self.subst(a, st) for a in chain[1:]]
        role = self.dispatch(name, args, st, depth, fed, cmd.herestrings, dialect)
        if role or fed:
            lvl.code.add(role or "shell")

    def dispatch(self, name, args, st, depth, fed, herestrings, dialect):
        """Judge one program. Returns how it reads stdin when that is as code."""
        cwd = st.cwd
        if name in CDS:
            st.cwd = self.chdir(args, st)
        elif name in POPS:
            st.cwd = None
        elif name in SHELLS:
            self.shell(name, args, st, depth, herestrings)
            return "shell"
        elif name in EVALS:
            st.cwd = self.level(" ".join(args), cwd, depth + 1, st.vars, dialect)
        elif name in SOURCES:
            st.cwd = None  # a sourced file may cd anywhere
            return "shell"
        elif name in INTERPRETERS or name in AWKS:
            return self.interpreter(name, args, st, herestrings)
        elif name == "sed":
            self.sed(args, cwd)
        elif name == "find":
            self.find(args, st, depth, dialect)
        elif name == "git":
            self.git(args, cwd)
        elif name in EXTRACTORS:
            self.extract(name, args, cwd)
        elif name in COPIERS or name in MOVERS or name in TREE_COPIERS:
            if fed or not operands(args):
                self.unresolved.append(f"{name} <operands from stdin>")
            self.copy(name, args, cwd)
        else:
            kind = WRITERS.get(name)
            if kind is None and name in GATED and GATED[name][0].search(" ".join(args)):
                kind = GATED[name][1]
            if kind is None:
                return
            ops = operands(args)
            if fed or not ops:
                self.unresolved.append(f"{name} <operands from stdin>")
            tree = kind == "tree" or (kind == "flag" and any(RECURSE.fullmatch(a) for a in args))
            for p in ops:
                self.write(p, cwd, tree)
        return None

    # -- programs --------------------------------------------------------------------------
    def chdir(self, args, st):
        ops = [a for a in args if not a.startswith("-") or a == "-"]
        if not args:
            return os.path.expanduser("~")
        if len(ops) != 1 or ops[0] == "-" or UNRESOLVED.search(ops[0]):
            return None
        if st.cwd is None and not _rooted(ops[0]):
            return None
        return self.forms(ops[0], st.cwd or self.rings.root, False)[0]

    def shell(self, name, args, st, depth, herestrings) -> None:
        codes, dialect = [], ("ps" if name in ("pwsh", "cmd") else "sh")
        if name == "pwsh":
            for k, a in enumerate(args):
                la = a.lower().split(":", 1)[0]
                if la.startswith("-c") and "-command".startswith(la):
                    codes.append(" ".join(args[k + 1:]))
                    break
                if la in ("-e", "-ec") or (la.startswith("-en") and "-encodedcommand".startswith(la)):
                    if k + 1 < len(args):
                        try:
                            codes.append(base64.b64decode(args[k + 1] + "==").decode("utf-16-le"))
                        except Exception:
                            codes.append(args[k + 1])
                    break
                if la.startswith("-f") and "-file".startswith(la):
                    break
            else:
                # Windows PowerShell reads bare arguments as a command, pwsh as a file: read both.
                if any(not a.startswith("-") for a in args):
                    codes.append(" ".join(args))
        elif name == "cmd":
            for k, a in enumerate(args):
                if a.lower() in ("/c", "/k", "/r"):
                    codes.append(" ".join(args[k + 1:]))
                    break
        else:
            # Anywhere, not just first: `bash -o pipefail -c '...'` puts an option value first.
            for k, a in enumerate(args):
                if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a) or a == "--command":
                    codes += args[k + 1:k + 2]
                    break
        for code in codes + list(herestrings):
            self.level(code, st.cwd, depth + 1, st.vars, dialect)

    def interpreter(self, name, args, st, herestrings):
        codes, rest, inplace = [], [], False
        if name in AWKS:
            k, given = 0, False
            while k < len(args):
                a = args[k]
                if a in ("-f", "--file"):
                    given, k = True, k + 2
                elif a in ("-i", "--include") and args[k + 1:k + 2] == ["inplace"]:
                    inplace, k = True, k + 2
                elif a in ("-v", "-F", "--assign", "--field-separator"):
                    k += 2
                elif a.startswith("-") and a != "-":
                    k += 1
                else:
                    if given:
                        rest.append(a)
                    else:
                        codes.append(a)
                        given = True
                    k += 1
        else:
            flag, skip = INLINE.get(name), set()
            for k, a in enumerate(args):
                m = re.match(r"--(?:eval|print)=(.*)", a, re.S)
                if m:
                    codes.append(m.group(1))
                elif flag and flag.fullmatch(a) and k + 1 < len(args):
                    codes.append(args[k + 1])
                    skip.add(k + 1)
            rest = [a for k, a in enumerate(args) if k not in skip and not a.startswith("-")]
            inplace = name in ("perl", "ruby") and any(
                re.fullmatch(r"-[A-Za-z]*i\S*", a) for a in args)
        for code in codes + list(herestrings):
            self.opaque(code, st.cwd)
        if inplace:
            for p in rest:
                self.write(p, st.cwd)
        # With a script file, stdin is that script's data; with none, or with inline code
        # that may read it (exec(input())), stdin is code.
        return "other" if name not in AWKS and (codes or not rest or rest[0] == "-") else None

    def sed(self, args, cwd) -> None:
        inplace = any(re.fullmatch(r"-[A-Za-z]*i\S*", a) or a.startswith("--in-place")
                      for a in args)
        scripts, files, explicit, k = [], [], False, 0
        while k < len(args):
            a = args[k]
            if a in ("-e", "--expression"):
                scripts += args[k + 1:k + 2]
                explicit, k = True, k + 2
            elif a.startswith("--expression="):
                scripts.append(a.split("=", 1)[1])
                explicit, k = True, k + 1
            elif a in ("-f", "--file"):
                explicit, k = True, k + 2
            elif a in ("-l", "--line-length"):
                k += 2
            elif a.startswith("-") and a != "-":
                k += 1
            else:
                files.append(a)
                k += 1
        if not explicit and files:
            scripts.append(files.pop(0))
        for script in scripts:
            for m in SED_WRITE.finditer(script):
                self.write(m.group(1), cwd)
        if inplace:
            for f in files:
                self.write(f, cwd)

    def find(self, args, st, depth, dialect) -> None:
        k = 0
        while k < len(args) and (args[k] in ("-H", "-L", "-P") or re.fullmatch(r"-O\d", args[k])):
            k += 1
        starts = []
        while k < len(args) and not args[k].startswith(("-", "(", "!")):
            starts.append(args[k])
            k += 1
        rest, j = args[k:], 0
        tree = "-delete" in rest
        while j < len(rest):
            a = rest[j]
            if a in ("-fprint", "-fprint0", "-fprintf", "-fls") and j + 1 < len(rest):
                self.write(rest[j + 1], st.cwd)
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                inner, j = [], j + 1
                while j < len(rest) and rest[j] not in (";", "+", "\\;"):
                    inner.append(rest[j])
                    j += 1
                chain, _ = unwrap(inner)
                iname = prog_name(chain[0]) if chain else ""
                if iname in MUTATING or (iname == "sed" and any(
                        re.fullmatch(r"-[A-Za-z]*i\S*", w) for w in chain)):
                    tree = True
                if inner:  # run per file by find, with find's cwd
                    self.command(Cmd(inner), State(st.cwd, dict(st.vars)), depth, Level(), dialect)
            j += 1
        if tree:
            for s in starts or ["."]:
                self.write(s, st.cwd, tree=True)

    def git(self, args, cwd) -> None:
        k = 0
        while k < len(args) and args[k].startswith("-"):
            if args[k] == "-C" and k + 1 < len(args):
                nxt = args[k + 1]
                cwd = (None if UNRESOLVED.search(nxt) or (cwd is None and not _rooted(nxt))
                       else self.forms(nxt, cwd or self.rings.root, False)[0])
                k += 2
            elif args[k] in ("-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
                k += 2
            else:
                k += 1
        # Only explicit path operands: `git apply` and friends write what a patch names, and
        # the main session must keep its own merge flow (residual in the docstring).
        if k < len(args) and args[k] in ("rm", "mv", "checkout", "restore", "clean"):
            for p in operands(args[k + 1:]):
                self.write(p, cwd)

    def extract(self, name, args, cwd) -> None:
        dest, unpacking, ops = None, name in ("unzip", "expand-archive", "patch"), []
        k = 0
        while k < len(args):
            a, la = args[k], args[k].lower()
            if (name in ("tar", "bsdtar") and a == "-C") or (name == "cpio" and a == "-D") \
                    or (name in ("unzip", "patch") and a == "-d") \
                    or la.startswith("--directory") \
                    or (name == "expand-archive" and la.startswith("-d")):
                dest, k = _opt_value(args, k)
                continue
            if name in ("7z", "7za") and a.startswith("-o"):
                dest, k = a[2:], k + 1
                continue
            if name == "patch" and (a == "-o" or la.startswith("--output")):
                val, k = _opt_value(args, k)
                if val:
                    self.write(val, cwd)
                continue
            if name in ("tar", "bsdtar") and (la in ("-x", "--extract", "--get")
                                              or re.fullmatch(r"-[A-Za-z]*x[A-Za-z]*", a)
                                              or (k == 0 and not a.startswith("-") and "x" in a)):
                unpacking = True
            if name == "cpio" and (la in ("-i", "--extract") or re.fullmatch(r"-[A-Za-z]*i[A-Za-z]*", a)):
                unpacking = True
            if name in ("7z", "7za") and k == 0 and la in ("x", "e"):
                unpacking = True
            if not a.startswith("-"):
                ops.append(a)
            k += 1
        for p in ops:
            self.write(p, cwd)
        if name == "expand-archive" and dest is None and len(ops) >= 2:
            dest = ops[1]  # Expand-Archive <zip> <destination>
        if unpacking:
            if dest:
                self.write(dest, cwd, tree=True)
            else:
                # No destination given: the archive or patch decides what lands under cwd.
                # Judged for the protected tree only, so the main session can still
                # `git apply` / `patch` / `unzip` in its own root (see the docstring).
                self.write(cwd or self.rings.root, cwd, tree=True, kinds=("protected",))

    def copy(self, name, args, cwd) -> None:
        if name in TREE_COPIERS:
            for p in operands(args):
                self.write(p, cwd, tree=True)
            return
        move, ps = name in MOVERS, name in PS_ITEM
        tree = move or any(COPY_RECURSE.fullmatch(a) for a in args)
        contents = any(re.fullmatch(r"-[A-Za-z]*T[A-Za-z]*", a) for a in args) \
            or "--no-target-directory" in args
        dests, srcs, ops, k = [], [], [], 0
        while k < len(args):
            a, la = args[k], args[k].lower()
            if a == "--":
                ops += args[k + 1:]
                break
            if (ps and la.startswith("-des")) or la.startswith("--target-directory") \
                    or (not ps and re.fullmatch(r"-[A-Za-z]*t", a)):
                val, k = _opt_value(args, k)
                dests += [val] if val else []
                continue
            if ps and (la.startswith("-p") or la.startswith("-li")) \
                    and ("-path".startswith(la.split(":")[0]) or la.startswith("-li")):
                val, k = _opt_value(args, k)
                srcs += [val] if val else []
                continue
            if not (a.startswith("-") and len(a) > 1):
                ops.append(a)
            k += 1
        if dests:
            srcs += ops
        elif len(ops) >= 2:
            dests, srcs = [ops[-1]], srcs + ops[:-1]
        elif ps:  # a cmdlet with one path copies into the current location
            dests, srcs = ["."], srcs + ops
        else:
            dests = ops
        for d in dests:
            is_dir = cwd is not None and not UNRESOLVED.search(d) and os.path.isdir(
                self.forms(d, cwd, False)[0])
            self.write(d, cwd, tree=tree and (contents or not is_dir))
            for s in srcs:
                base = _basename(s)
                landing = d if base in ("", ".") else d.rstrip("/\\") + "/" + base
                self.write(landing, cwd, tree=tree)
        if move:
            for s in srcs:
                self.write(s, cwd, tree=True)


# ---- the decision --------------------------------------------------------------------------
def emit_deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def allow() -> None:
    sys.exit(0)


def load_payload(path: str) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        sys.exit(3)
    if not text.strip():
        return {}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


RING_NAME = {"record": "the append-only record", "ticket": "the ritual ticket",
             "protected": "the protected tree"}
RING_WHY = {
    "record": "Nothing writes there by hand: the capture hook appends and obsctl seals. Read it "
              "freely (ls, cat, du, grep); to analyse it run "
              "`python3 .claude/tools/obsctl.py analyze`.",
    "ticket": "Only the prompt hook mints it, when the user types /project-memory or /adopt; no "
              "identity writes it, the main session included. Ask the user to type the command.",
    "protected": "The main session owns the gate, the tools it executes, the configs, the agents "
                 "and the protocols: a sub-agent must not be able to edit what governs it. Return "
                 "the change as a proposal in your Structured Return.",
}


def explain(zone: Zone, form: str, root: Path, identity: str, how: str, degraded: bool) -> str:
    ring = f"{RING_NAME[zone.kind]} ({zone.label})"
    if how == "unresolved":
        head = (f"that command writes {form.replace(SUB, '$(...)')}, a path the gate cannot "
                f"resolve, and also names {ring}; spell the write target out literally.")
    else:
        try:
            shown = Path(form).relative_to(root).as_posix()
        except ValueError:
            shown = Path(form).as_posix()
        parts = _fold(form)
        place = ("is" if parts == zone.parts else
                 "is inside" if _within(parts, zone.parts) else "would reach into")
        head = f"{shown} {place} {ring}."
        if how == "opaque":
            head += (" It is named next to a write call in code handed to an interpreter, which "
                     "the gate cannot read, so it refuses rather than guess.")
    if zone.kind == "protected":
        head += f" Sub-agent '{identity}' has no write grant for it."
    reason = f"{head} {RING_WHY[zone.kind]}"
    if degraded and zone.kind == "protected":
        reason += " (policy.json unreadable: failing closed on the fallback tree.)"
    return reason


# What the activity pulse needs once the decision is out: the project root and the tool NAME
# (never its input). Filled by main(), read only by activity_pulse().
_ACTIVITY: dict = {}


def activity_pulse() -> None:
    """Telemetry, run strictly AFTER the decision is computed and emitted: refresh
    state/heartbeat.json (throttled by progress.pulse_seconds, _lib.activity_pulse) so "last
    activity" stays true through a multi-hour turn, for the main session and sub-agents alike.
    It can never change the decision: stdout is flushed first, anything it prints is swallowed,
    every exception is swallowed, and the caller exits with the code main() produced."""
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
    try:
        root = _ACTIVITY.get("root")
        if not root:
            return
        import contextlib
        import io
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            tools = str(Path(root) / ".claude" / "tools")
            if tools not in sys.path:
                sys.path.insert(0, tools)
            import _lib  # noqa: E402
            _lib.activity_pulse(_ACTIVITY.get("tool") or "tool")
    except BaseException:  # noqa: BLE001 - a broken pulse loses a beat, never a decision
        pass


def main(argv: list) -> int:
    if len(argv) < 1:
        return 3
    payload = load_payload(argv[0])

    root_env = payload.get("_project_root") or ""
    root = Path(root_env).resolve() if root_env else Path.cwd().resolve()
    tool = str(payload.get("tool_name") or "")
    _ACTIVITY.update(root=str(root), tool=tool)
    raw_input = payload.get("tool_input")
    cwd = os.path.realpath(str(payload.get("cwd") or root))

    # Identity: ONLY an absent agent_type/agent_name means the main session. A sub-agent that
    # happens to be named "orchestrator" must not inherit main-session privileges by its name.
    agent_type = str(payload.get("agent_type") or "").strip()
    agent_name = str(payload.get("agent_name") or "").strip()
    identity = agent_type or agent_name or "orchestrator"
    is_main = not (agent_type or agent_name)

    if tool not in WRITE_TOOLS + SHELL_TOOLS:
        allow()

    if not isinstance(raw_input, dict):
        # A shape we cannot parse is a shape we cannot judge. Deny rather than wave it through.
        emit_deny(
            f"the gate could not read this {tool} call's tool_input (expected an object, got "
            f"{type(raw_input).__name__}). Refusing rather than guessing."
        )
    tool_input = raw_input

    policy = None
    record_root = None
    try:
        sys.path.insert(0, str(root / ".claude" / "tools"))
        import _lib  # noqa: E402

        policy = _lib.load_config("policy")
        record_root = _lib.record_root()
    except Exception:
        policy, record_root = None, None

    degraded = not isinstance(policy, dict) or not policy
    if degraded:
        protected = FALLBACK_PROTECTED
        grants: dict = {}
        deny_bash = () if is_main else ("git",)
        read_only: dict = {}
    else:
        protected = tuple(policy.get("protected") or FALLBACK_PROTECTED)
        agents = policy.get("agents") or {}
        entry = agents.get(identity) if isinstance(agents, dict) else None
        default = policy.get("default") or {}
        grants = entry if isinstance(entry, dict) else {}
        if is_main:
            deny_bash = tuple(grants.get("deny_bash") or ())
            read_only = {}
        else:
            source = grants if "deny_bash" in grants else default
            deny_bash = tuple(source.get("deny_bash") or ())
            ro = source.get("read_only_subcommands")
            if not isinstance(ro, dict):
                ro = default.get("read_only_subcommands") or {}
            read_only = ro if isinstance(ro, dict) else {}

    if record_root is None:
        record_root = root.parent / f"{root.name}_claude_iff"
    rings = build_rings(root, record_root, protected, grants.get("write_paths"), is_main)

    if tool in WRITE_TOOLS:
        target = (tool_input.get("file_path") or tool_input.get("path")
                  or tool_input.get("notebook_path"))
        if not target:
            allow()
        hit = rings.hit(path_forms(str(target), cwd, deep=True, expand=False))
        if hit:
            emit_deny(explain(hit[0], hit[1], root, identity, "write", degraded))
        allow()

    # ---- Bash / PowerShell ----------------------------------------------------------------
    command = str(tool_input.get("command") or "")
    if not command.strip():
        allow()
    lowered = command.lower()

    for denied in deny_bash:
        # Word-boundary match anywhere in the command, not just the leading token. Lexing shell
        # in python is not sound (bash -c, eval, quoting, $(), backticks, variable indirection
        # all defeat a token walk), so this errs toward false positives and says so when it
        # fires. The single exception is one simple read-only invocation of the denied command.
        #
        # `/` and `.` join the excluded neighbors: a PATH SEGMENT spelled like the command is a
        # mention, not an invocation. Without this, any repo living under a folder named `git`
        # or `GIT` (a common convention, including this machine's ~/Documents/GIT/) made every
        # `ls`, `cp` and `python3` that named a path in it read as running git - the adoption
        # dry-run hit that constantly. The shipped tests covered the adjacent class (gitleaks,
        # mygit) and missed this one. Executing `./git` slips the net as a consequence; the
        # docstring already says this arm is advisory against a determined adversary, and the
        # rings below are what actually guard the data.
        if not re.search(rf"(?<![\w./-]){re.escape(denied)}(?![\w./-])", lowered):
            continue
        if denied == "git" and READONLY_GIT.match(command.strip()):
            sub = next((t for t in command.split()[1:] if not t.startswith("-")), "")
            allowed_subs = read_only.get("git") or ()
            if sub in allowed_subs:
                continue
        emit_deny(
            f"sub-agent '{identity}' may not run `{denied}`. Only a single, simple, read-only "
            f"invocation is allowed ({', '.join(sorted(read_only.get(denied) or ())[:5])}); "
            f"anything that mutates, and anything wrapped in a shell, an eval or a substitution, "
            f"is the main session's job. This check matches the word anywhere in the command, so "
            f"it can fire on a harmless mention: if that happened, ask the main session to run it."
        )

    reader = ShellReader(rings, "ps" if tool == "PowerShell" else "sh")
    try:
        reader.level(command, cwd, 0)
    except Exception:
        # A reader bug must not open the rings: fall back to the opaque reading of the whole
        # command (a guarded path named next to a write call). If that fails too, exit 3.
        reader.opaque(command, cwd)
    verdict = reader.verdict()
    if verdict:
        zone, where, how = verdict
        emit_deny(explain(zone, where, root, identity, how, degraded))
    allow()
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except SystemExit as exc:  # allow() and emit_deny() exit here, the decision already printed
        code = exc.code
    except Exception:
        # Any unhandled failure means this gate did not judge the call. Tell the wrapper.
        code = 3
    activity_pulse()
    sys.exit(code)
