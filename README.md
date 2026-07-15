# Shared Build Artifact Orb

A generic CircleCI orb for deduplicating build artifacts across projects.

The `ensure` job:

1. Runs under a caller-supplied `serial-group` keyed by a canonical build identity.
2. Searches recent successful producer pipelines for a matching
   `build-manifest.json`.
3. Triggers one producer pipeline when no match exists.
4. Waits for producer completion.
5. Downloads and SHA-256 verifies the artifact.
6. Persists the artifact and build reference to the consumer workflow workspace.

## Required producer manifest

```json
{
  "schema": 1,
  "build_key": "canonical-build-identity",
  "artifact_path": "artifact.bin",
  "artifact_sha256": "sha256-hex"
}
```

The producer stores both the manifest and artifact using `store_artifacts`.

## Authentication

The orb reads `CIRCLECI_TOKEN` from a context supplied by the consuming
project. Do not store tokens in orb parameters or source.

## Development

```bash
python3 -m pytest tests
circleci orb pack src > orb.yml
circleci orb process orb.yml
```

See `src/examples/ensure-wheel.yml` for usage.

## Limitations

- Artifact retention follows CircleCI artifact retention settings.
- The recent-pipeline search is bounded by `search-limit`.
- The serial group wait limit is five hours.
- Private projects require a token with access to producer and consumer projects.
