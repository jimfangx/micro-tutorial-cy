"""Seed restoration is independent of the original Git checkout."""

import shutil
from chia_dma.config import ALLOWLIST, load
from chia_dma.artifacts import git, snapshot, read_json, source_bundle


def test_preserved_seed_includes_uncommitted_inputs_and_restores_after_source_removal(
    tmp_path,
):
    cfg = load()
    original = tmp_path / "original"
    original.mkdir()
    cfg["source"] = str(original)
    cfg["storage"] = str(tmp_path / "storage")
    git(original, "init", "-q")
    for name in (*ALLOWLIST, "software/test.c", "run.sh"):
        p = original / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("baseline\n")
    (original / "run.sh").chmod(0o755)
    git(original, "add", "-A")
    git(
        original,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@local",
        "commit",
        "-qm",
        "baseline",
    )
    (original / "software/test.c").write_text("uncommitted benchmark\n")
    (original / ALLOWLIST[0]).write_text("uncommitted controller\n")
    (original / "new-source.txt").write_text("untracked source")
    (original / "loop").mkdir()
    (original / "loop/not-source").write_text("exclude")
    seed = snapshot(cfg)
    assert (seed / "tree/software/test.c").read_text() == "uncommitted benchmark\n"
    assert (seed / "tree/new-source.txt").read_text() == "untracked source"
    assert (seed / "tree/run.sh").stat().st_mode & 0o111
    assert not (seed / "tree/loop").exists() and (seed / "tree/.git").is_file()
    assert "repos" not in read_json(seed / "manifest.json")
    shutil.rmtree(original)
    restored = tmp_path / "restored"
    shutil.copytree(seed / "tree", restored, symlinks=True)
    parent = source_bundle(seed / "tree")
    candidate = {n: text + "// candidate\n" for n, text in parent.items()}
    # Application copies only these two complete files onto the fixed seed.
    for name, text in candidate.items():
        (restored / name).chmod(0o644)
        (restored / name).write_text(text)
    assert source_bundle(restored) == candidate
    assert (restored / "software/test.c").read_text() == "uncommitted benchmark\n"
