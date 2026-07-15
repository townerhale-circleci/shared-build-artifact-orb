#!/usr/bin/env python3
"""Create a build manifest for one artifact selected from an upload directory."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_manifest(
    *,
    build_key: str,
    artifact_root: Path,
    artifact_glob: str,
    artifact_destination: str,
    manifest_output: Path,
    metadata_json: str,
) -> Dict[str, Any]:
    matches = sorted(path for path in artifact_root.glob(artifact_glob) if path.is_file())
    if len(matches) != 1:
        raise ValueError(
            f"artifact glob must match exactly one file; matched {len(matches)} "
            f"under {artifact_root}: {artifact_glob}"
        )
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError as error:
        raise ValueError(f"metadata must be valid JSON: {error}") from None
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")

    artifact = matches[0]
    relative_path = artifact.relative_to(artifact_root).as_posix()
    destination = artifact_destination.strip("/")
    artifact_api_path = f"{destination}/{relative_path}" if destination else relative_path
    manifest = {
        "schema": 1,
        "build_key": build_key,
        "artifact_path": artifact_api_path,
        "artifact_sha256": sha256_file(artifact),
        "artifact_size": artifact.stat().st_size,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metadata": metadata,
    }
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    build_key = os.environ.get("SHARED_BUILD_PUBLISH_BUILD_KEY")
    artifact_root = os.environ.get("SHARED_BUILD_PUBLISH_ARTIFACT_ROOT")
    artifact_destination = os.environ.get(
        "SHARED_BUILD_PUBLISH_ARTIFACT_DESTINATION"
    )
    parser.add_argument("--build-key", default=build_key, required=not build_key)
    parser.add_argument(
        "--artifact-root", default=artifact_root, required=not artifact_root
    )
    parser.add_argument(
        "--artifact-glob",
        default=os.environ.get("SHARED_BUILD_PUBLISH_ARTIFACT_GLOB", "*"),
    )
    parser.add_argument(
        "--artifact-destination",
        default=artifact_destination,
        required=not artifact_destination,
    )
    parser.add_argument(
        "--manifest-output",
        default=os.environ.get(
            "SHARED_BUILD_PUBLISH_MANIFEST_OUTPUT",
            "/tmp/shared-build-publish/build-manifest.json",
        ),
    )
    parser.add_argument(
        "--metadata-json",
        default=os.environ.get("SHARED_BUILD_PUBLISH_METADATA_JSON", "{}"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = create_manifest(
            build_key=args.build_key,
            artifact_root=Path(args.artifact_root),
            artifact_glob=args.artifact_glob,
            artifact_destination=args.artifact_destination,
            manifest_output=Path(args.manifest_output),
            metadata_json=args.metadata_json,
        )
        print(
            f"MANIFEST_READY build_key={manifest['build_key']} "
            f"path={manifest['artifact_path']} sha256={manifest['artifact_sha256']}"
        )
        return 0
    except Exception as error:
        print(f"create-manifest failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
