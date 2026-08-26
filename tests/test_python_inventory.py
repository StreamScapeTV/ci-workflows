from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "ci_workflows"
INVENTORY_PATH = ROOT / "PYTHON_INVENTORY.yml"
TRIVIAL_CLASS_METHODS = {
    "__init__",
    "__repr__",
    "__str__",
    "__hash__",
    "__eq__",
    "__post_init__",
}
ALLOWED_MULTI_FILE_ASSESSMENTS = {"justified", "consolidation-required"}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _load_inventory() -> Mapping[str, Any]:
    value = yaml.safe_load(INVENTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("PYTHON_INVENTORY.yml must contain one mapping")
    return value


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    suffix = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix}{node.name}({ast.unparse(node.args)}){suffix}"


def _named_records(value: object, *, location: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        raise AssertionError(f"{location} must be a list")
    records: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise AssertionError(f"{location}[{index}] must be a mapping")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise AssertionError(f"{location}[{index}] must have a non-empty name")
        if name in records:
            raise AssertionError(f"duplicate declaration {name!r} in {location}")
        records[name] = raw
    return records


def _purpose(record: Mapping[str, Any], *, location: str) -> None:
    purpose = record.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        raise AssertionError(f"{location} must have a non-empty purpose")
    if purpose.rstrip()[-1] not in ".!?":
        raise AssertionError(f"{location} purpose must be one sentence")


def _internal_dependencies(tree: ast.Module, module_names: set[str]) -> list[str]:
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            candidates: list[str] = []
            if node.level and node.module:
                candidates.append(node.module.split(".")[0])
            elif node.module and node.module.startswith("ci_workflows."):
                candidates.append(node.module.removeprefix("ci_workflows.").split(".")[0])
            elif node.level and not node.module:
                candidates.extend(alias.name.split(".")[0] for alias in node.names)
            dependencies.update(candidate for candidate in candidates if candidate in module_names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ci_workflows."):
                    candidate = alias.name.removeprefix("ci_workflows.").split(".")[0]
                    if candidate in module_names:
                        dependencies.add(candidate)
    return sorted(dependencies)


class PythonInventoryTests(unittest.TestCase):
    def test_complete_inventory_matches_current_python_tree_and_ast(self) -> None:
        inventory = _load_inventory()
        self.assertEqual(inventory.get("source_root"), "src/ci_workflows")
        self.assertTrue(inventory.get("inventory_complete"))
        self.assertRegex(str(inventory.get("generated_from_sha", "")), SHA_PATTERN)

        source_paths = sorted(SOURCE_ROOT.glob("*.py"))
        source_relative = {path.relative_to(ROOT).as_posix() for path in source_paths}
        module_names = {path.stem for path in source_paths if path.name != "__init__.py"}

        summary = inventory.get("summary")
        self.assertIsInstance(summary, dict)
        self.assertEqual(summary.get("python_files_discovered"), len(source_paths))

        status_values = inventory.get("status_values")
        self.assertIsInstance(status_values, list)
        allowed_statuses = set(status_values)
        self.assertEqual(
            allowed_statuses,
            {"keep", "consolidate", "compatibility", "remove-after-migration", "review"},
        )

        domains = inventory.get("domains")
        self.assertIsInstance(domains, dict)
        inventory_files: dict[str, tuple[str, Mapping[str, Any]]] = {}

        for domain, raw_domain in domains.items():
            self.assertIsInstance(domain, str)
            self.assertIsInstance(raw_domain, dict)
            self.assertTrue(str(raw_domain.get("target_module", "")).startswith("src/ci_workflows/"))
            self.assertTrue(str(raw_domain.get("responsibility", "")).strip())
            files = raw_domain.get("files")
            self.assertIsInstance(files, list)
            for raw_file in files:
                self.assertIsInstance(raw_file, dict)
                relative = raw_file.get("path")
                self.assertIsInstance(relative, str)
                self.assertNotIn(relative, inventory_files, f"duplicate file ownership for {relative}")
                inventory_files[relative] = (domain, raw_file)

        self.assertSetEqual(set(inventory_files), source_relative)

        for path in source_paths:
            relative = path.relative_to(ROOT).as_posix()
            domain, record = inventory_files[relative]
            self.assertIn(record.get("status"), allowed_statuses, f"invalid status for {relative}")
            responsibility = record.get("responsibility")
            self.assertIsInstance(responsibility, str)
            self.assertTrue(responsibility.strip(), f"missing responsibility for {relative}")

            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            expected_classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
            expected_functions = {
                node.name: node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

            class_records = _named_records(record.get("classes"), location=f"{domain}:{relative}:classes")
            function_records = _named_records(record.get("functions"), location=f"{domain}:{relative}:functions")
            self.assertSetEqual(set(class_records), set(expected_classes), f"class coverage drift in {relative}")
            self.assertSetEqual(set(function_records), set(expected_functions), f"function coverage drift in {relative}")

            for name, node in expected_functions.items():
                function_record = function_records[name]
                _purpose(function_record, location=f"{relative}:{name}")
                self.assertEqual(function_record.get("signature"), _function_signature(node))

            for name, node in expected_classes.items():
                class_record = class_records[name]
                _purpose(class_record, location=f"{relative}:{name}")
                expected_methods = {
                    child.name: child
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name not in TRIVIAL_CLASS_METHODS
                }
                method_records = _named_records(
                    class_record.get("methods"),
                    location=f"{domain}:{relative}:{name}:methods",
                )
                self.assertSetEqual(
                    set(method_records),
                    set(expected_methods),
                    f"method coverage drift in {relative}:{name}",
                )
                for method_name, method in expected_methods.items():
                    method_record = method_records[method_name]
                    _purpose(method_record, location=f"{relative}:{name}.{method_name}")
                    self.assertEqual(method_record.get("signature"), _function_signature(method))

            self.assertEqual(
                record.get("internal_dependencies"),
                _internal_dependencies(tree, module_names),
                f"internal dependency drift in {relative}",
            )

        multi_file_domains = inventory.get("multi_file_domains")
        self.assertIsInstance(multi_file_domains, list)
        reported: dict[str, Mapping[str, Any]] = {}
        for raw in multi_file_domains:
            self.assertIsInstance(raw, dict)
            domain = raw.get("domain")
            self.assertIsInstance(domain, str)
            self.assertNotIn(domain, reported, f"duplicate multi-file report for {domain}")
            reported[domain] = raw

        expected_multi = {
            domain
            for domain, raw_domain in domains.items()
            if isinstance(raw_domain, dict)
            and isinstance(raw_domain.get("files"), list)
            and len(raw_domain["files"]) > 2
        }
        self.assertSetEqual(set(reported), expected_multi)
        for domain in expected_multi:
            raw_domain = domains[domain]
            report = reported[domain]
            files = [record["path"] for record in raw_domain["files"]]
            self.assertEqual(report.get("current_file_count"), len(files))
            self.assertListEqual(report.get("files"), sorted(files))
            self.assertIn(report.get("assessment"), ALLOWED_MULTI_FILE_ASSESSMENTS)
            self.assertTrue(str(report.get("reason", "")).strip())
            self.assertTrue(str(report.get("intended_target", "")).strip())


if __name__ == "__main__":
    unittest.main()
