#!/usr/bin/env python3
"""Find or produce a CircleCI artifact identified by a build manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

TERMINAL_FAILURES = {"failed", "error", "canceled", "unauthorized", "not_run"}


@dataclass(frozen=True)
class ArtifactReference:
    build_key: str
    pipeline_id: str
    pipeline_number: int
    job_number: int
    artifact_path: str
    artifact_url: str
    artifact_sha256: str

    def download(self, client: Any, destination: Path) -> None:
        body = client.download_bytes(self.artifact_url)
        digest = hashlib.sha256(body).hexdigest()
        if digest != self.artifact_sha256:
            raise ValueError(
                f"artifact checksum mismatch: expected {self.artifact_sha256}, got {digest}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)


class CircleCIClient:
    def __init__(self, token: str, base_url: str = "https://circleci.com/api/v2"):
        if not token:
            raise ValueError("CircleCI API token is required")
        self.token = token
        self.base_url = base_url.rstrip("/")

    def _request(
        self, method: str, url: str, payload: Optional[Dict[str, Any]] = None
    ) -> bytes:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Circle-Token": self.token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"CircleCI API returned HTTP {error.code} for {url}: {detail}"
            ) from None
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"CircleCI API request failed for {url}: {error.reason}"
            ) from None

    def _get_json(self, url: str) -> Dict[str, Any]:
        return json.loads(self._request("GET", url) or b"{}")

    def list_pipelines(
        self, project_slug: str, branch: str, limit: int
    ) -> List[Dict[str, Any]]:
        slug = urllib.parse.quote(project_slug, safe="/")
        items: List[Dict[str, Any]] = []
        url = (
            f"{self.base_url}/project/{slug}/pipeline?"
            + urllib.parse.urlencode({"branch": branch})
        )
        while url and len(items) < limit:
            page = self._get_json(url)
            items.extend(page.get("items", []))
            token = page.get("next_page_token")
            url = (
                f"{self.base_url}/project/{slug}/pipeline?"
                + urllib.parse.urlencode({"branch": branch, "page-token": token})
                if token
                else ""
            )
        return items[:limit]

    def get_workflows(self, pipeline_id: str) -> List[Dict[str, Any]]:
        return self._get_json(f"{self.base_url}/pipeline/{pipeline_id}/workflow").get(
            "items", []
        )

    def get_jobs(self, workflow_id: str) -> List[Dict[str, Any]]:
        return self._get_json(f"{self.base_url}/workflow/{workflow_id}/job").get(
            "items", []
        )

    def get_artifacts(
        self, project_slug: str, job_number: int
    ) -> List[Dict[str, Any]]:
        slug = urllib.parse.quote(project_slug, safe="/")
        return self._get_json(
            f"{self.base_url}/project/{slug}/{job_number}/artifacts"
        ).get("items", [])

    def download_json(self, url: str) -> Dict[str, Any]:
        return json.loads(self.download_bytes(url))

    def download_bytes(self, url: str) -> bytes:
        return self._request("GET", url)

    def trigger_pipeline(
        self,
        *,
        project_slug: str,
        definition_id: str,
        branch: str,
        parameters: Dict[str, Any],
    ) -> Dict[str, Any]:
        slug = urllib.parse.quote(project_slug, safe="/")
        return json.loads(
            self._request(
                "POST",
                f"{self.base_url}/project/{slug}/pipeline/run",
                {
                    "definition_id": definition_id,
                    "config": {"branch": branch},
                    "checkout": {"branch": branch},
                    "parameters": parameters,
                },
            )
        )

    def wait_for_pipeline(
        self, pipeline_id: str, timeout_seconds: int, poll_interval: float
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            workflows = self.get_workflows(pipeline_id)
            if not workflows:
                time.sleep(poll_interval)
                continue
            statuses = {workflow.get("status") for workflow in workflows}
            failures = statuses & TERMINAL_FAILURES
            if failures:
                raise RuntimeError(
                    f"producer pipeline {pipeline_id} failed with {sorted(failures)}"
                )
            if statuses and statuses <= {"success"}:
                return
            time.sleep(poll_interval)
        raise TimeoutError(f"producer pipeline {pipeline_id} exceeded {timeout_seconds}s")


def _reference_from_pipeline(
    client: Any,
    *,
    pipeline: Dict[str, Any],
    build_key: str,
    producer_project_slug: str,
    producer_job_name: str,
) -> Optional[ArtifactReference]:
    pipeline_id = str(pipeline["id"])
    for workflow in client.get_workflows(pipeline_id):
        if workflow.get("status") != "success":
            continue
        for job in client.get_jobs(workflow["id"]):
            if job.get("name") != producer_job_name or job.get("status") != "success":
                continue
            job_number = int(job["job_number"])
            artifacts = client.get_artifacts(producer_project_slug, job_number)
            by_path = {artifact.get("path"): artifact for artifact in artifacts}
            manifest_artifact = by_path.get("build-manifest.json")
            if not manifest_artifact:
                continue
            try:
                manifest = client.download_json(manifest_artifact["url"])
            except (ValueError, json.JSONDecodeError, RuntimeError):
                continue
            if manifest.get("schema") != 1 or manifest.get("build_key") != build_key:
                continue
            artifact_path = manifest.get("artifact_path")
            artifact_sha256 = manifest.get("artifact_sha256")
            artifact = by_path.get(artifact_path)
            if not artifact or not isinstance(artifact_sha256, str):
                continue
            return ArtifactReference(
                build_key=build_key,
                pipeline_id=pipeline_id,
                pipeline_number=int(pipeline.get("number", 0)),
                job_number=job_number,
                artifact_path=str(artifact_path),
                artifact_url=artifact["url"],
                artifact_sha256=artifact_sha256,
            )
    return None


def find_matching_artifact(
    client: Any,
    *,
    build_key: str,
    producer_project_slug: str,
    producer_job_name: str,
    branch: str,
    search_limit: int,
) -> Optional[ArtifactReference]:
    for pipeline in client.list_pipelines(producer_project_slug, branch, search_limit):
        reference = _reference_from_pipeline(
            client,
            pipeline=pipeline,
            build_key=build_key,
            producer_project_slug=producer_project_slug,
            producer_job_name=producer_job_name,
        )
        if reference:
            return reference
    return None


def ensure_artifact(
    client: Any,
    *,
    build_key: str,
    producer_project_slug: str,
    producer_definition_id: str,
    producer_job_name: str,
    branch: str,
    search_limit: int,
    timeout_seconds: int,
    poll_interval: float,
) -> ArtifactReference:
    reference = find_matching_artifact(
        client,
        build_key=build_key,
        producer_project_slug=producer_project_slug,
        producer_job_name=producer_job_name,
        branch=branch,
        search_limit=search_limit,
    )
    if reference:
        return reference

    created = client.trigger_pipeline(
        project_slug=producer_project_slug,
        definition_id=producer_definition_id,
        branch=branch,
        parameters={"build_key": build_key},
    )
    pipeline_id = str(created["id"])
    client.wait_for_pipeline(pipeline_id, timeout_seconds, poll_interval)
    reference = _reference_from_pipeline(
        client,
        pipeline={"id": pipeline_id, "number": created.get("number", 0)},
        build_key=build_key,
        producer_project_slug=producer_project_slug,
        producer_job_name=producer_job_name,
    )
    if not reference:
        raise RuntimeError(
            f"producer pipeline {pipeline_id} succeeded without a matching artifact"
        )
    return reference


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    build_key = os.environ.get("SHARED_BUILD_KEY")
    project_slug = os.environ.get("SHARED_BUILD_PRODUCER_PROJECT_SLUG")
    definition_id = os.environ.get("SHARED_BUILD_PRODUCER_DEFINITION_ID")
    branch = os.environ.get("SHARED_BUILD_BRANCH")
    parser.add_argument("--build-key", default=build_key, required=not build_key)
    parser.add_argument(
        "--producer-project-slug",
        default=project_slug,
        required=not project_slug,
    )
    parser.add_argument(
        "--producer-definition-id",
        default=definition_id,
        required=not definition_id,
    )
    parser.add_argument(
        "--producer-job-name",
        default=os.environ.get("SHARED_BUILD_PRODUCER_JOB_NAME", "build-wheel"),
    )
    parser.add_argument("--branch", default=branch, required=not branch)
    parser.add_argument(
        "--artifact-output",
        default=os.environ.get(
            "SHARED_BUILD_ARTIFACT_OUTPUT", "/tmp/shared-build/artifact"
        ),
    )
    parser.add_argument(
        "--reference-output",
        default=os.environ.get(
            "SHARED_BUILD_REFERENCE_OUTPUT",
            "/tmp/shared-build/build-reference.json",
        ),
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=int(os.environ.get("SHARED_BUILD_SEARCH_LIMIT", "25")),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("SHARED_BUILD_TIMEOUT_SECONDS", "3600")),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("SHARED_BUILD_POLL_INTERVAL", "3")),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        client = CircleCIClient(os.environ.get("CIRCLECI_TOKEN", ""))
        reference = ensure_artifact(
            client,
            build_key=args.build_key,
            producer_project_slug=args.producer_project_slug,
            producer_definition_id=args.producer_definition_id,
            producer_job_name=args.producer_job_name,
            branch=args.branch,
            search_limit=args.search_limit,
            timeout_seconds=args.timeout_seconds,
            poll_interval=args.poll_interval,
        )
        reference.download(client, Path(args.artifact_output))
        Path(args.reference_output).write_text(
            json.dumps(asdict(reference), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"ARTIFACT_READY build_key={reference.build_key} "
            f"producer_job={reference.job_number} path={reference.artifact_path}"
        )
        return 0
    except Exception as error:
        print(f"ensure-artifact failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
