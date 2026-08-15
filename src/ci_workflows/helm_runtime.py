"""Reviewed fixed runtime identity for the portable Helm workflow family."""
from __future__ import annotations

HELM_VERSION = "v3.18.6"
HELM_REQUIRED_COMMANDS = ("version", "lint", "template", "package", "pull", "push")
