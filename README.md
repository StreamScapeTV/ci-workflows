# StreamScapeTV CI Workflows

Reusable GitHub Actions workflows and shared actions for build, test, validation, packaging, and release automation.

## Overview

This repository provides reusable CI building blocks for projects that need consistent execution across different languages, platforms, and runner types.

It includes:

- reusable build and test workflows;
- repository-owned CI entrypoints for Linux and macOS workloads;
- shared actions for source setup, authentication, networking, diagnostics, and cleanup;
- validation and release workflows for several common project types;
- bounded workflow inputs intended for predictable, repeatable CI execution.

## Repository layout

- `.github/workflows/` — reusable workflow entrypoints
- `actions/` — shared GitHub Actions
- `scripts/` — CI helpers and validation utilities
- `contracts/` — machine-readable CI contracts
- `tests/` — repository-level workflow and contract checks

The workflow YAML files are the source of truth for supported inputs, outputs, permissions, and execution behavior.

## License

This project is available under the MIT License. See [LICENSE](LICENSE).
