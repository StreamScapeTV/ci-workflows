"""Strict product-neutral one-simulator confidence planning for validation.apple."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from .apple_contract import _validate_command, require, safe_relative
from .apple_simulator_script import SIMULATOR_UDID_TOKEN
from .apple_types import (
    AppleProfile,
    AppleRunnerCapability,
    AppleValidationError,
    AppleValidationPlan,
    AppleValidationRequest,
)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_ARGUMENT = re.compile(
    r"^(?:--?[A-Za-z0-9][A-Za-z0-9_-]{0,63}|[A-Za-z0-9][A-Za-z0-9_./:=+@%,-]{0,127})$"
)
_MAX_PACKET_BYTES = 32 * 1024
_MAX_STEPS = 8
_MAX_ARGUMENTS = 16


@dataclass(frozen=True, slots=True)
class SimulatorConfidencePacket:
    packet_id: str
    platform: str
    apple_plan: AppleValidationPlan

    def planning_outputs(self) -> dict[str, str]:
        outputs = self.apple_plan.planning_outputs()
        outputs.update(
            validation_scope="simulator-confidence",
            confidence_scope="simulator-confidence-only",
            runner_profile="github-hosted-macos",
            runs_on_json='["macos-latest"]',
            packet_id=self.packet_id,
        )
        return outputs


def _arguments(value: object) -> tuple[str, ...]:
    require(isinstance(value, list) and len(value) <= _MAX_ARGUMENTS, "command_profile_rejected")
    result: list[str] = []
    for item in value:
        require(
            isinstance(item, str)
            and _ARGUMENT.fullmatch(item) is not None
            and item != SIMULATOR_UDID_TOKEN,
            "command_profile_rejected",
        )
        result.append(item)
    return tuple(result)


def build_simulator_confidence_packet(
    raw_json: str,
    *,
    repository: str,
    admitted_sha: str,
    source_trust: str,
    contract: Mapping[str, object],
) -> SimulatorConfidencePacket:
    """Parse one caller-owned packet and bind it to Central's existing simulator executor."""

    require(
        isinstance(raw_json, str)
        and 0 < len(raw_json.encode("utf-8")) <= _MAX_PACKET_BYTES,
        "command_profile_rejected",
    )
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError as error:
        raise AppleValidationError("command_profile_rejected") from error
    require(isinstance(raw, Mapping), "command_profile_rejected")
    require(set(raw) == {"schema_version", "packet_id", "platform", "steps"}, "command_profile_rejected")
    require(raw.get("schema_version") == 1, "command_profile_rejected")
    packet_id = raw.get("packet_id")
    platform = raw.get("platform")
    require(isinstance(packet_id, str) and _IDENTIFIER.fullmatch(packet_id) is not None, "command_profile_rejected")
    require(platform in {"ios", "tvos"}, "platform_rejected")
    steps = raw.get("steps")
    require(isinstance(steps, list) and 1 <= len(steps) <= _MAX_STEPS, "command_profile_rejected")

    commands = []
    seen_ids: set[str] = set()
    for row in steps:
        require(isinstance(row, Mapping), "command_profile_rejected")
        require(set(row) == {"id", "script_path", "arguments"}, "command_profile_rejected")
        step_id = row.get("id")
        require(
            isinstance(step_id, str)
            and _IDENTIFIER.fullmatch(step_id) is not None
            and step_id not in seen_ids,
            "command_profile_rejected",
        )
        seen_ids.add(step_id)
        script_path = safe_relative(row.get("script_path"), "command_profile_rejected")
        arguments = _arguments(row.get("arguments"))
        commands.append(
            _validate_command(
                {
                    "stage": "test",
                    "kind": "bash-script",
                    "action": "run",
                    "script_path": script_path,
                    "fixed_arguments": [*arguments, "--simulator", SIMULATOR_UDID_TOKEN],
                    "expected_outputs": [],
                }
            )
        )

    profile = AppleProfile.IOS_SIMULATOR if platform == "ios" else AppleProfile.TVOS_SIMULATOR
    simulator_id = "ciw-ios" if platform == "ios" else "ciw-tvos"
    simulators = contract.get("_simulators")
    profiles = contract.get("profiles")
    require(isinstance(simulators, Mapping) and simulator_id in simulators, "simulator_contract_invalid")
    require(isinstance(profiles, Mapping) and profile.value in profiles, "unsupported_profile")
    generic_profile = profiles[profile.value]
    require(isinstance(generic_profile, Mapping), "unsupported_profile")
    require(source_trust == "trusted-exact", "source_trust_rejected")

    request = AppleValidationRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        consumer_contract="simulator-confidence",
        validation_profile=profile,
        source_trust=source_trust,
        platform=platform,
    )
    plan = AppleValidationPlan(
        request=request,
        task_profile=f"simulator-confidence:{packet_id}",
        runner_profile=AppleRunnerCapability.APPLE,
        planner_runner_profile=AppleRunnerCapability.PORTABLE,
        workspace_profile="apple",
        timeout_minutes=min(int(generic_profile["timeout_minutes"]), 100),
        toolchain=contract["_toolchain"],
        working_directory=".",
        container=None,
        simulator=simulators[simulator_id],
        commands=tuple(commands),
        protected_paths=(),
        cleanup_paths=(),
        environment_bindings=(),
        artifact_exception_id=None,
    )
    return SimulatorConfidencePacket(packet_id=packet_id, platform=platform, apple_plan=plan)


def confidence_outputs(values: Mapping[str, str], packet: SimulatorConfidencePacket) -> dict[str, str]:
    """Mark ordinary Apple result evidence as simulator confidence only."""

    result = dict(values)
    summary_raw = result.get("test_summary", "")
    if summary_raw:
        try:
            summary = json.loads(summary_raw)
        except json.JSONDecodeError:
            summary = {}
        if isinstance(summary, dict):
            summary.update(
                confidence_scope="simulator-confidence-only",
                packet_id=packet.packet_id,
                physical_device_authority=False,
                signing_authority=False,
                release_authority=False,
            )
            result["test_summary"] = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    result["runner_profile"] = "github-hosted-macos"
    result["confidence_scope"] = "simulator-confidence-only"
    result["packet_id"] = packet.packet_id
    return result
