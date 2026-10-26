"""Pinned Git objects, annotated diff maps, and bounded evidence-bearing context."""

import ast
import base64
import fnmatch
import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from reviewer.infra.config import Limits, Policy
from reviewer.infra.models import PricingInput
from reviewer.infra.safety import redact

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BUILTIN_IGNORES = (
    ".git",
    ".git/**",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pem",
    "*.key",
    "**/*.pem",
    "**/*.key",
    ".secrets/**",
    "**/.secrets/**",
    "**/node_modules/**",
    "node_modules/**",
    "vendor/**",
    "**/vendor/**",
    "dist/**",
    "**/dist/**",
    "*.lock",
    "**/*.lock",
    "package-lock.json",
    "**/package-lock.json",
    "*.min.js",
    "**/*.min.js",
)


def safe_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        bool(path)
        and not path.startswith("/")
        and ".." not in parts
        and ".git" not in parts
        and "\x00" not in path
        and "\\" not in path
    )


class WorkspaceError(RuntimeError):
    pass


class GitSnapshots:
    def __init__(self, base_sha, head_sha, *, repo=None, token=None, local_path=None):
        if not all(SHA_RE.fullmatch(sha) for sha in (base_sha, head_sha)):
            raise ValueError("Snapshots must be pinned full commit SHAs.")
        self.base_sha, self.head_sha = base_sha, head_sha
        self.repo, self.token, self.path = repo, token, Path(local_path) if local_path else None
        self.temp = None

    def __enter__(self):
        if self.path is None:
            from reviewer.infra.github import GitHub

            GitHub.validate_repo(self.repo or "")
            self.temp = tempfile.TemporaryDirectory(prefix="infrastructure-agent-")
            self.path = Path(self.temp.name)
            try:
                self.git("init", "--quiet")
                for sha in dict.fromkeys((self.base_sha, self.head_sha)):
                    self.git(
                        "fetch",
                        "--quiet",
                        "--depth=1",
                        "--no-tags",
                        "--no-recurse-submodules",
                        f"https://github.com/{self.repo}.git",
                        sha,
                        authenticated=True,
                    )
            except BaseException:
                self.temp.cleanup()
                raise
        return self

    def __exit__(self, *_):
        if self.temp:
            self.temp.cleanup()

    def git(self, *args, authenticated=False):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
        if authenticated:
            header = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.extraHeader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Basic {header}",
                }
            )
        command = [
            "git",
            "--literal-pathspecs",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "credential.helper=",
            "-c",
            "protocol.file.allow=never",
            *args,
        ]
        try:
            result = subprocess.run(
                command, cwd=self.path, env=env, capture_output=True, timeout=60, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError(
                "Pinned repository fetch/read failed or timed out. No PR code was executed."
            ) from exc
        if result.returncode:
            raise WorkspaceError(
                "Pinned Git objects are unavailable. The PR may have been force-pushed; rerun the review."
            )
        return result.stdout

    def sha(self, snapshot):
        return self.base_sha if snapshot == "base" else self.head_sha

    def paths(self, snapshot):
        raw = self.git("ls-tree", "-rz", "--name-only", self.sha(snapshot))
        return [path.decode("utf-8", errors="replace") for path in raw.split(b"\x00") if path]

    def read(self, snapshot, path, limits):
        if not safe_path(path):
            return None, "unsafe path"
        sha = self.sha(snapshot)
        row = self.git("ls-tree", "-z", sha, "--", path).split(b"\x00")[0]
        if not row:
            return None, "absent at this snapshot"
        if not row.startswith(b"100644 ") and not row.startswith(b"100755 "):
            return None, "symlink, submodule, or non-regular object"
        size = int(self.git("cat-file", "-s", f"{sha}:{path}"))
        if size > limits.max_blob_bytes:
            return None, "file exceeds byte budget"
        raw = self.git("show", f"{sha}:{path}")
        if b"\x00" in raw:
            return None, "binary file"
        try:
            return redact(raw.decode("utf-8")), None
        except UnicodeDecodeError:
            return None, "non-UTF-8 file"

    def changes(self):
        fields = self.git(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "-z",
            "--find-renames",
            self.base_sha,
            self.head_sha,
        ).split(b"\x00")
        index, changes = 0, []
        while index < len(fields) and fields[index]:
            status = fields[index].decode()
            old = fields[index + 1].decode("utf-8", errors="replace")
            index += 2
            new = old
            if status.startswith(("R", "C")):
                new = fields[index].decode("utf-8", errors="replace")
                index += 1
            changes.append((status[0], old, new))
        return changes

    def patch(self, old, new):
        return redact(
            self.git(
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--unified=3",
                "--find-renames",
                self.base_sha,
                self.head_sha,
                "--",
                *dict.fromkeys((old, new)),
            ).decode("utf-8", errors="replace")
        )


@dataclass
class DiffFile:
    file: str
    old_file: str
    status: str
    annotated: str
    head_lines: set[int] = field(default_factory=set)
    base_lines: set[int] = field(default_factory=set)
    changed_head: set[int] = field(default_factory=set)
    changed_base: set[int] = field(default_factory=set)


def annotate_patch(path, old_path, status, patch):
    output, old_line, new_line, in_hunk = [], 0, 0, False
    diff = DiffFile(path, old_path, status, "")
    for row in patch.splitlines():
        match = HUNK.match(row)
        if match:
            old_line, new_line = map(int, match.groups())
            in_hunk = True
            output.append(row)
        elif in_hunk and row.startswith("+"):
            output.append(f"       {new_line:6d} + {row[1:]}")
            diff.head_lines.add(new_line)
            diff.changed_head.add(new_line)
            new_line += 1
        elif in_hunk and row.startswith("-"):
            output.append(f"{old_line:6d}        - {row[1:]}")
            diff.base_lines.add(old_line)
            diff.changed_base.add(old_line)
            old_line += 1
        elif in_hunk and row.startswith(" "):
            output.append(f"{old_line:6d} {new_line:6d}   {row[1:]}")
            diff.base_lines.add(old_line)
            diff.head_lines.add(new_line)
            old_line, new_line = old_line + 1, new_line + 1
        elif in_hunk and row.startswith("\\"):
            continue
    diff.annotated = (
        f"FILE {json.dumps(path)}; base path {json.dumps(old_path)}; status {status}\nBASE   HEAD\n"
        + "\n".join(output)
    )
    return diff


@dataclass
class Excerpt:
    snapshot: str
    file: str
    start_line: int
    end_line: int
    content: str


@dataclass
class PreparedContext:
    repo: str
    pr: int
    target_base_sha: str
    base_sha: str
    head_sha: str
    title: str = ""
    description: str = ""
    diffs: list[DiffFile] = field(default_factory=list)
    excerpts: list[Excerpt] = field(default_factory=list)
    omissions: list[str] = field(default_factory=list)
    candidate_hints: list[dict] = field(default_factory=list)
    policy: Policy = field(default_factory=Policy)
    pricing: PricingInput | None = None

    def ranges(self, snapshot, path):
        ranges = [
            (e.start_line, e.end_line)
            for e in self.excerpts
            if e.snapshot == snapshot and e.file == path
        ]
        for diff in self.diffs:
            if path == (diff.file if snapshot == "head" else diff.old_file):
                ranges.extend(
                    (line, line)
                    for line in (diff.head_lines if snapshot == "head" else diff.base_lines)
                )
        return ranges

    def supplied(self, snapshot, path, start, end):
        ranges = sorted(self.ranges(snapshot, path))
        cursor = start
        for left, right in ranges:
            if left > cursor:
                break
            if right >= cursor:
                cursor = right + 1
            if cursor > end:
                return True
        return False

    def changed(self, snapshot, path, start, end):
        for diff in self.diffs:
            if path == (diff.file if snapshot == "head" else diff.old_file):
                lines = diff.changed_head if snapshot == "head" else diff.changed_base
                if any(start <= line <= end for line in lines):
                    return True
        return False

    def model_input(self):
        pricing = []
        if self.pricing:
            for record in self.pricing.records:
                pricing.append(
                    {
                        "id": record.id,
                        "file": record.file,
                        "resource": record.resource,
                        "region": record.region,
                        "assumptions": record.assumptions,
                        "available": record.available,
                        "comparable": record.comparable,
                        "unsupported_resources": record.unsupported_resources,
                        "source": record.source,
                        "generated_at": record.generated_at.isoformat(),
                        "threshold_exceeded": record.delta is not None
                        and record.delta >= self.policy.cost_threshold_usd_month,
                    }
                )
        return {
            "review_metadata": {
                "repo": self.repo,
                "pr": self.pr,
                "target_base_sha": self.target_base_sha,
                "base_sha": self.base_sha,
                "head_sha": self.head_sha,
                "pr_title": redact(self.title)[:2000],
                "pr_description": redact(self.description)[:8000],
            },
            "review_policy": self.policy.model_dump(mode="json"),
            "diffs": [
                {
                    "file": d.file,
                    "old_file": d.old_file,
                    "status": d.status,
                    "annotated": d.annotated,
                    "commentable_head_lines": sorted(d.head_lines),
                }
                for d in self.diffs
            ],
            "file_context": [asdict(e) for e in self.excerpts],
            "candidate_hints": self.candidate_hints,
            "workload_evidence": None,
            "pricing_evidence": pricing,
            "context_limits": {
                "omitted": self.omissions[:200],
                "additional_omissions": max(0, len(self.omissions) - 200),
                "available_tools": [],
                "note": "Static bounded context; external or unresolved dynamic calls are not proven. All omissions are retained in the run artifact.",
            },
        }


def _ignored(path, policy):
    return not safe_path(path) or any(
        fnmatch.fnmatchcase(path, pattern) for pattern in (*BUILTIN_IGNORES, *policy.ignore_paths)
    )


def _spans(path, text, centers):
    lines = text.splitlines()
    spans = [(1, min(40, len(lines)) if centers else len(lines))] if lines else []
    if centers:
        spans = [(max(1, min(centers) - 15), min(len(lines), max(centers) + 15)), *spans]
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
            functions = [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            touched = [
                node
                for node in functions
                if centers and any(node.lineno <= line <= node.end_lineno for line in centers)
            ]
            names = {
                node.id for fn in touched for node in ast.walk(fn) if isinstance(node, ast.Name)
            }
            selected = [*touched, *[node for node in functions if node.name in names]]
            spans = [
                (
                    min([node.lineno, *[item.lineno for item in node.decorator_list]]),
                    node.end_lineno,
                )
                for node in selected
            ] + spans
        except (SyntaxError, ValueError, RecursionError):
            pass
    return spans


def _excerpt(snapshot, path, text, centers, char_budget):
    lines, taken, used = text.splitlines(), set(), 0
    for start, end in _spans(path, text, centers):
        for line in range(start, end + 1):
            if line in taken:
                continue
            row = lines[line - 1] + "\n"
            if used + len(row) > char_budget:
                break
            taken.add(line)
            used += len(row)
    # Prioritize selection by relevance, then present selected lines in source
    # order. Joining adjacent ranges keeps decorators attached to their helpers.
    output, block_start, previous = [], None, None
    for line in sorted(taken):
        if previous is not None and line != previous + 1:
            output.append(
                Excerpt(
                    snapshot,
                    path,
                    block_start,
                    previous,
                    "\n".join(lines[block_start - 1 : previous]) + "\n",
                )
            )
            block_start = None
        if block_start is None:
            block_start = line
        previous = line
    if block_start is not None:
        output.append(
            Excerpt(
                snapshot,
                path,
                block_start,
                previous,
                "\n".join(lines[block_start - 1 : previous]) + "\n",
            )
        )
    return output, used, len(taken) < len(lines)


def collect_context(
    source: GitSnapshots,
    *,
    repo,
    pr,
    target_base_sha,
    title="",
    description="",
    policy=None,
    limits=None,
    pricing=None,
):
    policy, limits = policy or Policy(), limits or Limits()
    ctx = PreparedContext(
        repo,
        pr,
        target_base_sha,
        source.base_sha,
        source.head_sha,
        title,
        description,
        policy=policy,
        pricing=pricing,
    )
    raw_sources, diff_used, changed_count = {}, 0, 0
    for status, old, new in source.changes():
        if _ignored(new, policy) or _ignored(old, policy):
            ctx.omissions.append(f"{new}: ignored or unsafe path")
            continue
        if len(ctx.diffs) >= limits.max_files:
            ctx.omissions.append(f"{new}: changed-file limit")
            continue
        patch = source.patch(old, new)
        diff = annotate_patch(new, old, status, patch)
        count = len(diff.changed_head) + len(diff.changed_base)
        if not diff.head_lines and not diff.base_lines:
            ctx.omissions.append(f"{new}: no text hunks (binary, pure rename, or mode-only change)")
            continue
        if (
            diff_used + len(diff.annotated) > limits.diff_chars
            or changed_count + count > limits.max_changed_lines
        ):
            ctx.omissions.append(f"{new}: diff/changed-line budget")
            continue
        # A symlink's textual target must not be interpreted as source code.
        snapshots = (
            [("base", old)]
            if status == "D"
            else [("head", new)]
            if status == "A"
            else [("base", old), ("head", new)]
        )
        readable, unsafe_object, pending_sources = False, False, {}
        for snapshot, path in snapshots:
            text, reason = source.read(snapshot, path, limits)
            if text is None:
                ctx.omissions.append(f"{snapshot}:{path}: {reason}")
                unsafe_object |= reason != "absent at this snapshot"
                continue
            readable = True
            pending_sources[snapshot, path] = text
        if not readable or unsafe_object:
            continue
        raw_sources.update(pending_sources)
        ctx.diffs.append(diff)
        diff_used += len(diff.annotated)
        changed_count += count

    used = 0
    for (snapshot, path), text in raw_sources.items():
        centers = set()
        for diff in ctx.diffs:
            if path == (diff.file if snapshot == "head" else diff.old_file):
                centers |= diff.changed_head if snapshot == "head" else diff.changed_base
        excerpts, size, partial = _excerpt(
            snapshot,
            path,
            text,
            centers,
            min(limits.file_chars, max(0, limits.context_chars - used)),
        )
        ctx.excerpts.extend(excerpts)
        used += size
        if partial:
            ctx.omissions.append(
                f"{snapshot}:{path}: supporting context is partial; exact supplied ranges recorded"
            )
        if path.endswith(".py"):
            try:
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)) and any(
                        node.lineno <= line <= node.end_lineno for line in centers
                    ):
                        calls = [
                            ast.unparse(item.func)
                            for item in ast.walk(node)
                            if isinstance(item, ast.Call)
                        ]
                        if calls and len(ctx.candidate_hints) < 100:
                            ctx.candidate_hints.append(
                                {
                                    "snapshot": snapshot,
                                    "file": path,
                                    "line": node.lineno,
                                    "kind": "calls-in-changed-loop",
                                    "calls": calls[:20],
                                    "verified_remote": False,
                                }
                            )
            except (SyntaxError, ValueError, RecursionError):
                ctx.omissions.append(f"{snapshot}:{path}: Python AST unavailable; text review only")

    # Match explicit imports and nearby client/schema/config files. This is evidence
    # selection, not a completeness claim or a cost classification gate.
    candidates, snapshot_text = [], {}
    for snapshot in ("head", "base"):
        paths = source.paths(snapshot)
        changed_text = "\n".join(
            text for (which, _), text in raw_sources.items() if which == snapshot
        )
        snapshot_text[snapshot] = changed_text
        for path in paths:
            if (snapshot, path) in raw_sources or _ignored(path, policy):
                continue
            stem = PurePosixPath(path).stem
            module = str(PurePosixPath(path).with_suffix("")).replace("/", ".")
            imported = (
                len(stem) > 2 and re.search(rf"\b{re.escape(stem)}\b", changed_text)
            ) or module in changed_text
            shared = any(
                word in PurePosixPath(path).name.lower()
                for word in (
                    "client",
                    "schema",
                    "model",
                    "lifecycle",
                    "schedule",
                    "config",
                    "setting",
                    "logger",
                    "telemetry",
                    "migration",
                )
            )
            if imported or shared:
                candidates.append((not bool(imported), snapshot, path))
    included = 0
    for _, snapshot, path in sorted(candidates):
        if included >= limits.related_files or used >= limits.context_chars:
            ctx.omissions.append(f"{snapshot}:{path}: related-file/context budget")
            continue
        text, reason = source.read(snapshot, path, limits)
        if text is None:
            ctx.omissions.append(f"{snapshot}:{path}: {reason}")
            continue
        anchors = set()
        if path.endswith(".py"):
            try:
                anchors = {
                    node.lineno
                    for node in ast.walk(ast.parse(text))
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and re.search(rf"\b{re.escape(node.name)}\b", snapshot_text[snapshot])
                }
            except (SyntaxError, ValueError, RecursionError):
                pass
        excerpts, size, partial = _excerpt(
            snapshot, path, text, anchors, min(limits.file_chars, limits.context_chars - used)
        )
        if not excerpts and text:
            ctx.omissions.append(f"{snapshot}:{path}: no complete lines fit the context budget")
            continue
        ctx.excerpts.extend(excerpts)
        used += size
        included += 1
        if partial:
            ctx.omissions.append(f"{snapshot}:{path}: related file context is partial")
    return ctx
