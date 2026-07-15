import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

MODULE_PATH = Path(__file__).parents[1] / "src" / "scripts" / "create_manifest.py"
SPEC = importlib.util.spec_from_file_location("create_manifest", MODULE_PATH)
create_manifest_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = create_manifest_module
SPEC.loader.exec_module(create_manifest_module)

create_manifest = create_manifest_module.create_manifest


def test_creates_manifest_for_exactly_one_matching_artifact(tmp_path):
    root = tmp_path / "wheelhouse"
    root.mkdir()
    wheel = root / "ttnn-1.0.0-cp310-manylinux.whl"
    wheel.write_bytes(b"real wheel bytes")
    output = tmp_path / "build-manifest.json"

    manifest = create_manifest(
        build_key="build-key",
        artifact_root=root,
        artifact_glob="*.whl",
        artifact_destination="wheelhouse",
        manifest_output=output,
        metadata_json='{"platform":"ubuntu-22.04","build_type":"Release"}',
    )

    assert manifest["schema"] == 1
    assert manifest["build_key"] == "build-key"
    assert (
        manifest["artifact_path"]
        == "wheelhouse/ttnn-1.0.0-cp310-manylinux.whl"
    )
    assert manifest["artifact_sha256"] == hashlib.sha256(b"real wheel bytes").hexdigest()
    assert manifest["artifact_size"] == len(b"real wheel bytes")
    assert manifest["metadata"]["platform"] == "ubuntu-22.04"
    assert json.loads(output.read_text()) == manifest


def test_rejects_zero_or_multiple_artifact_matches(tmp_path):
    root = tmp_path / "wheelhouse"
    root.mkdir()

    with pytest.raises(ValueError, match="exactly one"):
        create_manifest(
            build_key="key",
            artifact_root=root,
            artifact_glob="*.whl",
            artifact_destination="wheelhouse",
            manifest_output=tmp_path / "manifest.json",
            metadata_json="{}",
        )

    (root / "one.whl").write_bytes(b"one")
    (root / "two.whl").write_bytes(b"two")
    with pytest.raises(ValueError, match="exactly one"):
        create_manifest(
            build_key="key",
            artifact_root=root,
            artifact_glob="*.whl",
            artifact_destination="wheelhouse",
            manifest_output=tmp_path / "manifest.json",
            metadata_json="{}",
        )


def test_rejects_non_object_metadata(tmp_path):
    root = tmp_path / "wheelhouse"
    root.mkdir()
    (root / "one.whl").write_bytes(b"one")

    with pytest.raises(ValueError, match="JSON object"):
        create_manifest(
            build_key="key",
            artifact_root=root,
            artifact_glob="*.whl",
            artifact_destination="wheelhouse",
            manifest_output=tmp_path / "manifest.json",
            metadata_json='["not", "an", "object"]',
        )
