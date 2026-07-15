import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest

MODULE_PATH = Path(__file__).parents[1] / "src" / "scripts" / "ensure_artifact.py"
SPEC = importlib.util.spec_from_file_location("ensure_artifact", MODULE_PATH)
ensure_artifact_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ensure_artifact_module
SPEC.loader.exec_module(ensure_artifact_module)

ensure_artifact = ensure_artifact_module.ensure_artifact
find_matching_artifact = ensure_artifact_module.find_matching_artifact


class FakeClient:
    def __init__(self, pipelines, manifests):
        self.pipelines = pipelines
        self.manifests = manifests
        self.triggered = []

    def list_pipelines(self, project_slug, branch, limit):
        return self.pipelines[:limit]

    def get_workflows(self, pipeline_id):
        return [{"id": f"workflow-{pipeline_id}", "status": "success"}]

    def get_jobs(self, workflow_id):
        pipeline_id = workflow_id.removeprefix("workflow-")
        return [
            {
                "name": "build-wheel",
                "status": "success",
                "job_number": int(pipeline_id),
            }
        ]

    def get_artifacts(self, project_slug, job_number):
        return [
            {"path": "build-manifest.json", "url": f"manifest://{job_number}"},
            {"path": "artifact.bin", "url": f"artifact://{job_number}"},
        ]

    def download_json(self, url):
        return self.manifests[url]

    def trigger_pipeline(self, **kwargs):
        self.triggered.append(kwargs)
        return {"id": "3"}

    def wait_for_pipeline(self, pipeline_id, timeout_seconds, poll_interval):
        return None

    def download_bytes(self, url):
        return f"artifact-from-{url.removeprefix('artifact://')}".encode()


def manifest(build_key, job_number):
    body = f"artifact-from-{job_number}".encode()
    return {
        "schema": 1,
        "build_key": build_key,
        "artifact_path": "artifact.bin",
        "artifact_sha256": hashlib.sha256(body).hexdigest(),
    }


def test_finds_existing_artifact_without_triggering_producer():
    client = FakeClient(
        pipelines=[{"id": "2", "number": 2}],
        manifests={"manifest://2": manifest("wanted", 2)},
    )

    reference = find_matching_artifact(
        client,
        build_key="wanted",
        producer_project_slug="circleci/org/project",
        producer_job_name="build-wheel",
        branch="feature",
        search_limit=10,
    )

    assert reference.job_number == 2
    assert client.triggered == []


def test_triggers_one_producer_on_miss():
    client = FakeClient(
        pipelines=[{"id": "1", "number": 1}],
        manifests={
            "manifest://1": manifest("other", 1),
            "manifest://3": manifest("wanted", 3),
        },
    )

    reference = ensure_artifact(
        client,
        build_key="wanted",
        producer_project_slug="circleci/org/project",
        producer_definition_id="definition-id",
        producer_job_name="build-wheel",
        branch="feature",
        search_limit=10,
        timeout_seconds=60,
        poll_interval=0,
    )

    assert reference.job_number == 3
    assert len(client.triggered) == 1
    assert client.triggered[0]["parameters"] == {"build_key": "wanted"}


def test_rejects_artifact_with_wrong_checksum(tmp_path):
    client = FakeClient(
        pipelines=[{"id": "2", "number": 2}],
        manifests={"manifest://2": manifest("wanted", 999)},
    )
    reference = find_matching_artifact(
        client,
        build_key="wanted",
        producer_project_slug="circleci/org/project",
        producer_job_name="build-wheel",
        branch="feature",
        search_limit=10,
    )

    with pytest.raises(ValueError, match="checksum"):
        reference.download(client, tmp_path / "artifact.bin")
