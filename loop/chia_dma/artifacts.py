"""One self-contained seed, source validation, and atomic run metadata."""

from pathlib import Path
import fcntl
import json
import os
import shutil
import subprocess
import uuid
from .config import ALLOWLIST, digest


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).rstrip(
        "\n"
    )


def readonly(root):
    for folder, dirs, files in os.walk(root):
        for name in files:
            p = Path(folder) / name
            if not p.is_symlink():
                p.chmod(0o555 if p.stat().st_mode & 0o111 else 0o444)
        Path(folder).chmod(0o555)


def snapshot(cfg):
    """Copy current local source once; Git is provenance, never reconstruction."""
    source = Path(cfg["source"])
    base = Path(cfg["storage"]) / "seeds"
    base.mkdir(parents=True, exist_ok=True)
    temp = base / (".capture-" + uuid.uuid4().hex)
    tree = temp / "tree"
    initialized = []

    def ignore(folder, names):
        rel = Path(folder).relative_to(source)
        if ".git" in names:
            initialized.append(rel)
        return [
            n
            for n in names
            if n in cfg["preserve"]["exclude_dirs"]
            or str(rel / n) in cfg["preserve"]["exclude_paths"]
        ]

    shutil.copytree(source, tree, symlinks=True, ignore=ignore)
    # Chipyard discovers initialized optional generators through .git.exists.
    # Inert markers preserve that fact without copying repository object stores.
    for rel in initialized:
        (tree / rel / ".git").write_text("# Preserved initialized checkout\n")
    files = {}
    for folder, dirs, names in os.walk(tree):
        for name in dirs + names:
            p = Path(folder) / name
            rel = p.relative_to(tree)
            if p.is_symlink():
                origin = source / rel
                target = (origin.parent / os.readlink(p)).resolve()
                if not target.is_relative_to(source):
                    p.unlink()
                    continue
                if os.path.isabs(os.readlink(p)):
                    p.unlink()
                    p.symlink_to(
                        os.path.relpath(tree / target.relative_to(source), p.parent)
                    )
                files[str(rel)] = {"link": os.readlink(p)}
            elif p.is_file():
                content = p.read_bytes()
                if p.name != ".git" and content != (source / rel).read_bytes():
                    raise RuntimeError("Source changed during capture: " + str(rel))
                files[str(rel)] = {
                    "sha256": digest(content),
                    "mode": p.stat().st_mode & 0o777,
                }
    manifest = {
        "revision": git(source, "rev-parse", "HEAD"),
        "dirty_patch": git(
            source, "diff", "--binary", "HEAD", "--", ".", ":!loop", ":!loop_context"
        ),
        "files": files,
    }
    target = base / digest(manifest)
    write_json(temp / "manifest.json", manifest)
    if target.exists():
        shutil.rmtree(temp)
    else:
        readonly(temp)
        temp.rename(target)
    return target


def source_bundle(tree):
    return {name: (Path(tree) / name).read_text() for name in ALLOWLIST}


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) != set(ALLOWLIST):
        raise ValueError(
            "Candidate must contain exactly the two allowlisted implementation files"
        )
    if any(
        not isinstance(s, str) or not s.strip() or len(s) > 1_000_000
        for s in bundle.values()
    ):
        raise ValueError("Empty or oversized accelerator source")
    return bundle


def validate_integration(bundle, seed_bundle):
    """The allowlisted adapter also contains a fixed SoC integration mixin."""
    name = ALLOWLIST[1]
    marker = "\nclass WithRoCCDMA("
    if (
        marker not in bundle[name]
        or bundle[name].split(marker, 1)[1] != seed_bundle[name].split(marker, 1)[1]
    ):
        raise ValueError("The fixed WithRoCCDMA integration configuration changed")

    def l1_assignments(source):
        return [line.strip() for line in source.splitlines() if "io.mem." in line]

    if l1_assignments(bundle[name]) != l1_assignments(seed_bundle[name]):
        raise ValueError("The unused L1 interface must remain disabled")


class RunLock:
    """One population writer per run; separate runs can share immutable artifacts."""

    def __init__(self, run):
        self.path = Path(run) / ".writer.lock"

    def __enter__(self):
        self.file = self.path.open("a")
        fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self

    def __exit__(self, *args):
        self.file.close()
