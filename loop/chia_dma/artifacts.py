"""Immutable source artifacts and atomic stage records; search state stays native."""
from pathlib import Path
import difflib
import fcntl
import json
import os
import shutil
import stat
import subprocess
import uuid
from .config import digest, ALLOWLIST


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    with temp.open('w') as out:
        json.dump(value, out, indent=2, sort_keys=True, allow_nan=False)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_json(path):
    return json.loads(Path(path).read_text())


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def files(root):
    """Hash regular source files and symlink targets, never follow directory links."""
    result = {}
    for folder, dirs, names in os.walk(root):
        for name in list(dirs):
            p = Path(folder) / name
            if p.is_symlink():
                dirs.remove(name)
                names.append(name)
        for name in names:
            p = Path(folder) / name
            key = str(p.relative_to(root))
            result[key] = {'link': os.readlink(p)} if p.is_symlink() else {
                'sha256': digest(p.read_bytes()), 'executable': bool(p.stat().st_mode & 0o111)}
    return result



def sync_tree(root):
    """Make immutable artifact publication durable across a host reboot."""
    for folder, dirs, names in os.walk(root, topdown=False):
        for name in names:
            path = Path(folder) / name
            if not path.is_symlink():
                with path.open('rb') as stream:
                    os.fsync(stream.fileno())
        fd = os.open(folder, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def readonly(root):
    for folder, dirs, names in os.walk(root):
        for name in names:
            p = Path(folder) / name
            if not p.is_symlink():
                p.chmod(0o555 if p.stat().st_mode & 0o111 else 0o444)
        Path(folder).chmod(0o555)


def copy_tree(source, target, excludes=(), excluded_paths=()):
    source, target = Path(source), Path(target)
    def ignore(folder, names):
        rel = Path(folder).relative_to(source)
        return [n for n in names if n in excludes or str(rel / n) in excluded_paths]
    shutil.copytree(source, target, symlinks=True, ignore=ignore)
    # Resolve internal absolute links into the snapshot, prohibit escaping ones.
    for folder, dirs, names in os.walk(target):
        for name in dirs + names:
            p = Path(folder) / name
            if p.is_symlink():
                link = os.readlink(p)
                origin = source / p.relative_to(target)
                resolved = (origin.parent / link).resolve()
                if not resolved.is_relative_to(source.resolve()):
                    # External tool symlinks are recorded, never followed/mounted.
                    p.unlink()
                elif os.path.isabs(link):
                    p.unlink()
                    p.symlink_to(os.path.relpath(target / resolved.relative_to(source), p.parent))


def snapshot(cfg):
    base = Path(cfg['storage']) / 'seeds'
    base.mkdir(parents=True, exist_ok=True)
    temp = base / ('.capture-' + uuid.uuid4().hex)
    tree = temp / 'tree'
    source = Path(cfg['source'])
    copy_tree(source, tree, cfg['snapshot']['exclude_dirs'], cfg['snapshot']['exclude_paths'])
    subpaths = [line.split(' ', 1)[1] for line in git(source, 'config', '--file', '.gitmodules', '--get-regexp', 'path').splitlines()]
    submodules = git(source, 'submodule', 'status', '--recursive', '--', *subpaths)
    # Chipyard's withInitCheck uses .git.exists. Preserve that initialization
    # evidence, replacing external gitdir pointers with inert revision markers.
    # Git itself is not used by this fixed build flow.
    for line in submodules.splitlines():
        revision, rel, *_ = line.strip().split()
        folder = tree / rel
        if folder.is_dir() and not line.startswith('-'):
            (folder / '.git').write_text('# Immutable CHIA snapshot: ' + revision + '\n')
    manifest = files(tree)
    # A second pass detects edits by the other hardware agent during capture.
    for rel, record in manifest.items():
        if Path(rel).name != '.git' and 'sha256' in record and digest((source / rel).read_bytes()) != record['sha256']:
            raise RuntimeError(f'Source changed during seed capture: {rel}; retry when stable')
    provenance = {'revision': git(source, 'rev-parse', 'HEAD'),
                  'submodules': submodules,
                  'dirty_patch': git(source, 'diff', 'HEAD', '--', '.', ':!loop'),
                  'status': git(source, 'status', '--porcelain'),
                  'files': manifest}
    key = digest(manifest)
    write_json(temp / 'manifest.json', provenance)
    write_json(temp / 'seed.json', {'hash': key, 'source_revision': provenance['revision']})
    target = base / key
    if target.exists():
        shutil.rmtree(temp)
    else:
        sync_tree(temp)
        temp.rename(target)
        readonly(target)
    return target


def source_bundle(tree):
    return {name: (Path(tree) / name).read_text() for name in ALLOWLIST}


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) != set(ALLOWLIST):
        raise ValueError('Candidate must contain exactly the two allowlisted implementation files')
    if any(not isinstance(s, str) or not s.strip() or len(s) > 1_000_000 for s in bundle.values()):
        raise ValueError('Empty or oversized accelerator source')
    return bundle



def validate_integration(bundle, seed_bundle):
    """The allowlisted adapter also contains a fixed SoC integration mixin."""
    name = ALLOWLIST[1]
    marker = '\nclass WithRoCCDMA('
    if marker not in bundle[name] or bundle[name].split(marker, 1)[1] != seed_bundle[name].split(marker, 1)[1]:
        raise ValueError('The fixed WithRoCCDMA integration configuration changed')
    def l1_assignments(source):
        return [line.strip() for line in source.splitlines() if 'io.mem.' in line]
    if l1_assignments(bundle[name]) != l1_assignments(seed_bundle[name]):
        raise ValueError('The unused L1 interface must remain disabled')


def archive_proposal(run, candidate_id, bundle, parent_bundle, metadata):
    validate_bundle(bundle)
    root = Path(run) / 'candidates'
    root.mkdir(parents=True, exist_ok=True)
    target = root / candidate_id
    if (target / 'proposal.json').exists():
        if read_json(target / 'proposal.json')['source'] != bundle:
            raise ValueError('Immutable candidate ID reused for different source')
        return target
    temp = root / ('.' + candidate_id + '-' + uuid.uuid4().hex)
    temp.mkdir()
    patch = []
    for name, content in bundle.items():
        dest = temp / 'rtl' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        patch.extend(difflib.unified_diff(parent_bundle[name].splitlines(True), content.splitlines(True),
                                          fromfile='a/' + name, tofile='b/' + name))
    (temp / 'changes.patch').write_text(''.join(patch))
    write_json(temp / 'proposal.json', {'source': bundle, 'source_hash': digest(bundle), **metadata})
    sync_tree(temp)
    readonly(temp)
    # Rename is the proposal commit point; generation is reusable from here on.
    temp.rename(target)
    return target


class RunLock:
    """One population writer per run; separate runs can share immutable artifacts."""
    def __init__(self, run):
        self.path = Path(run) / '.writer.lock'
    def __enter__(self):
        self.file = self.path.open('a')
        fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self
    def __exit__(self, *args):
        self.file.close()
