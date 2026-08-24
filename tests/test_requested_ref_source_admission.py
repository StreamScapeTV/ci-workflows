from __future__ import annotations

import json
from pathlib import Path
import unittest
from typing import Any, Mapping

import yaml

from ci_workflows import source

ROOT = Path(__file__).resolve().parents[1]
CENTRAL_REPOSITORY = "StreamScapeTV/ci-workflows"
SOURCE_REPOSITORY = "OtherOrg/private-app"
CENTRAL_SHA = "a" * 40
BRANCH_SHA = "b" * 40
TAG_OBJECT_SHA = "c" * 40
TAG_COMMIT_SHA = "d" * 40


class RemoteProvider:
    def repository(self, repository: str) -> Mapping[str, Any]:
        assert repository == SOURCE_REPOSITORY
        return {"full_name": repository, "default_branch": "main"}

    def collaborator_permission(self, repository: str, actor: str) -> str:
        raise AssertionError("requested-ref admission must not query collaborator permission")

    def pull_request(self, repository: str, number: int) -> Mapping[str, Any]:
        raise AssertionError("requested-ref admission must not query pull requests")

    def commit(self, repository: str, sha: str) -> Mapping[str, Any]:
        assert repository == SOURCE_REPOSITORY
        if sha not in {BRANCH_SHA, TAG_COMMIT_SHA}:
            raise source.SourceAdmissionError("github_metadata_unavailable")
        return {"sha": sha}

    def branch_sha(self, repository: str, branch: str) -> str:
        assert repository == SOURCE_REPOSITORY
        assert branch == "develop"
        return BRANCH_SHA

    def tag_ref(self, repository: str, tag_name: str) -> Mapping[str, Any]:
        assert repository == SOURCE_REPOSITORY
        assert tag_name == "release/fancy-tag"
        return {"object": {"type": "tag", "sha": TAG_OBJECT_SHA}}

    def tag_object(self, repository: str, sha: str) -> Mapping[str, Any]:
        assert repository == SOURCE_REPOSITORY
        assert sha == TAG_OBJECT_SHA
        return {"object": {"type": "commit", "sha": TAG_COMMIT_SHA}}


def event(event_name: str = "workflow_dispatch") -> source.EventContext:
    return source.EventContext(
        event_name=event_name,
        repository=CENTRAL_REPOSITORY,
        sha=CENTRAL_SHA,
        ref="refs/heads/main",
        ref_name="main",
        ref_type="branch",
        actor="streamscape-ci-dispatcher[bot]",
        triggering_actor="streamscape-ci-dispatcher[bot]",
        workflow_sha=CENTRAL_SHA,
        payload={},
    )


class RequestedRefAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = source.load_contract(ROOT)

    def inputs(self, **overrides: object) -> source.SourceInputs:
        raw: dict[str, object] = {
            "source_mode": "requested-ref",
            "requested_ref": "develop",
            "is_tag": False,
            "caller_repository": SOURCE_REPOSITORY,
            "history_depth": 1,
        }
        raw.update(overrides)
        return source.validate_inputs(raw, self.contract)

    def test_remote_branch_ref_resolves_inside_central_without_sha_request_identity(self) -> None:
        result = source.admit_source(self.inputs(), event(), RemoteProvider())

        self.assertEqual(result.caller_repository, SOURCE_REPOSITORY)
        self.assertEqual(result.source_repository, SOURCE_REPOSITORY)
        self.assertEqual(result.caller_default_branch, "main")
        self.assertEqual(result.caller_integration_branch, "develop")
        self.assertEqual(result.source_sha, BRANCH_SHA)
        self.assertEqual(result.resolved_sha, BRANCH_SHA)
        self.assertIsNone(result.requested_sha)
        self.assertEqual(result.trust_mode, source.TrustMode.TRUSTED_VALIDATION)
        self.assertFalse(result.requires_freshness)
        self.assertIsNone(result.tag_name)

    def test_remote_tag_ref_uses_explicit_is_tag_without_release_privilege(self) -> None:
        result = source.admit_source(
            self.inputs(requested_ref="release/fancy-tag", is_tag=True),
            event("workflow_call"),
            RemoteProvider(),
        )

        self.assertEqual(result.source_repository, SOURCE_REPOSITORY)
        self.assertEqual(result.source_sha, TAG_COMMIT_SHA)
        self.assertEqual(result.tag_name, "release/fancy-tag")
        self.assertEqual(result.tag_object_sha, TAG_OBJECT_SHA)
        self.assertEqual(result.tag_commit_sha, TAG_COMMIT_SHA)
        self.assertEqual(result.trust_mode, source.TrustMode.TRUSTED_VALIDATION)
        self.assertFalse(result.requires_freshness)

    def test_requested_ref_forbids_sha_pin_and_conflicting_source_authority(self) -> None:
        for override, expected in (
            ({"requested_sha": "e" * 40}, "requested_ref_sha_pin_forbidden"),
            ({"expected_branch": "develop"}, "requested_ref_expected_branch_forbidden"),
            ({"caller_integration_branch": "develop"}, "requested_ref_integration_branch_forbidden"),
            ({"release_contract": "backend"}, "requested_ref_release_contract_forbidden"),
            ({"pr_number": 17}, "requested_ref_pr_metadata_forbidden"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(source.SourceAdmissionError) as caught:
                    source.admit_source(
                        self.inputs(**override),
                        event(),
                        RemoteProvider(),
                    )
                self.assertEqual(caught.exception.instruction, expected)

    def test_requested_ref_requires_full_repository_ref_and_explicit_boolean_tag_type(self) -> None:
        for raw, expected in (
            (
                {
                    "source_mode": "requested-ref",
                    "requested_ref": "develop",
                    "is_tag": False,
                },
                "requested_ref_repository_required",
            ),
            (
                {
                    "source_mode": "requested-ref",
                    "is_tag": False,
                    "caller_repository": SOURCE_REPOSITORY,
                },
                "requested_ref_required_for_is_tag",
            ),
            (
                {
                    "source_mode": "requested-ref",
                    "requested_ref": "develop",
                    "caller_repository": SOURCE_REPOSITORY,
                },
                "is_tag_required_for_requested_ref",
            ),
            (
                {
                    "source_mode": "requested-ref",
                    "requested_ref": "refs/heads/develop",
                    "is_tag": False,
                    "caller_repository": SOURCE_REPOSITORY,
                },
                "invalid_requested_ref",
            ),
            (
                {
                    "source_mode": "requested-ref",
                    "requested_ref": "develop",
                    "is_tag": "yes",
                    "caller_repository": SOURCE_REPOSITORY,
                },
                "invalid_is_tag",
            ),
        ):
            with self.subTest(expected=expected):
                try:
                    parsed = source.validate_inputs(raw, self.contract)
                    source.admit_source(parsed, event(), RemoteProvider())
                except source.SourceAdmissionError as error:
                    self.assertEqual(error.instruction, expected)
                else:
                    self.fail("invalid requested-ref source unexpectedly admitted")

    def test_requested_ref_rejects_native_push_event(self) -> None:
        with self.assertRaises(source.SourceAdmissionError) as caught:
            source.admit_source(
                self.inputs(),
                event("push"),
                RemoteProvider(),
            )
        self.assertEqual(caught.exception.instruction, "source_mode_event_mismatch")

    def test_contract_exposes_requested_ref_as_resolution_only_nonprivileged_mode(self) -> None:
        self.assertEqual(
            self.contract["source_modes"]["requested-ref"],
            ["workflow_dispatch", "workflow_call"],
        )
        security = self.contract["security"]
        self.assertTrue(security["requested_ref_resolved_before_exact_checkout"])
        self.assertTrue(security["requested_ref_sha_pin_forbidden"])
        self.assertTrue(security["mutable_ref_input_forbidden"])

    def test_resolve_source_action_accepts_private_token_but_never_outputs_it(self) -> None:
        action_path = ROOT / "actions/resolve-source/action.yml"
        text = action_path.read_text(encoding="utf-8")
        document = yaml.safe_load(text)

        self.assertIn("requested_ref", document["inputs"])
        self.assertIn("is_tag", document["inputs"])
        self.assertIn("source_token", document["inputs"])
        self.assertNotIn("source_token", document["outputs"])
        env = document["runs"]["steps"][0]["env"]
        self.assertEqual(env["INPUT_REQUESTED_REF"], "${{ inputs.requested_ref }}")
        self.assertEqual(env["INPUT_IS_TAG"], "${{ inputs.is_tag }}")
        self.assertIn("inputs.source_token", env["GITHUB_TOKEN"])
        self.assertIn("github.token", env["GITHUB_TOKEN"])
        serialized_outputs = json.dumps(document["outputs"], sort_keys=True).casefold()
        self.assertNotIn("token", serialized_outputs)


if __name__ == "__main__":
    unittest.main()
