"""The fixed image and bundled patch replace the host Chipyard checkout."""

from pathlib import Path
import shutil
import subprocess
from chia_dma.config import ALLOWLIST, digest, load
from chia_dma.experiment import preserve_source, read_json, read_accelerator_files


def test_image_seed_is_immutable_reusable_and_restores_candidates(
    tmp_path, base_tree, monkeypatch
):
    configuration = load()
    configuration["storage"] = str(tmp_path / "storage")
    assert "source" not in configuration
    calls = []
    run_command = subprocess.run

    def materialize_image(command, check):
        if command[0] != "docker":
            return run_command(command, check=check)
        calls.append(command)
        mount = next(
            value
            for value in command
            if value.startswith("type=bind,src=") and value.endswith("dst=/seed")
        )
        destination = Path(mount.split(",")[1].removeprefix("src="))
        shutil.copytree(base_tree, destination / "tree")

    monkeypatch.setattr("chia_dma.experiment.subprocess.run", materialize_image)
    seed = preserve_source(configuration)
    manifest = read_json(seed / "manifest.json")
    assert (
        manifest["image"]
        == configuration["cluster"]["available_node_types"]["build"]["docker"]["image"]
    )
    assert seed.name == digest(manifest)
    assert preserve_source(configuration) == seed and len(calls) == 1
    assert not (seed / "tree" / ALLOWLIST[0]).stat().st_mode & 0o222
    assert len(list((seed / "tree/tests/embench-iot/src").iterdir())) == 19
    restored = tmp_path / "restored"
    shutil.copytree(seed / "tree", restored)
    parent = read_accelerator_files(seed / "tree")
    candidate = {name: text + "// candidate\n" for name, text in parent.items()}
    for name, text in candidate.items():
        (restored / name).chmod(0o644)
        (restored / name).write_text(text)
    assert read_accelerator_files(restored) == candidate
    parser = configuration["software"]["metrics_parser"]
    assert (restored / parser).read_bytes() == (base_tree / parser).read_bytes()
