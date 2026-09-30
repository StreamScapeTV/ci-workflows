from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BashPortabilityTests(unittest.TestCase):
    """Keep ordinary Central shell mechanics compatible with macOS Bash 3.2."""

    def shell_surfaces(self) -> list[Path]:
        return sorted(
            [*ROOT.rglob("*.sh")]
            + [*ROOT.joinpath(".github", "workflows").glob("*.yml")]
            + [*ROOT.joinpath("actions").glob("*/action.yml")]
        )

    def test_shell_surfaces_avoid_accidental_bash4_plus_constructs(self) -> None:
        forbidden = {
            "mapfile/readarray": re.compile(r"\b(?:mapfile|readarray)\b"),
            "associative arrays": re.compile(r"\b(?:declare|typeset|local)\s+-[A-Za-z]*A\b"),
            "namerefs": re.compile(r"\b(?:declare|typeset|local)\s+-[A-Za-z]*n\b"),
            "global declarations": re.compile(r"\bdeclare\s+-[A-Za-z]*g\b"),
            "coproc": re.compile(r"\bcoproc\b"),
            "globstar": re.compile(r"\bshopt\s+-s\s+globstar\b"),
            "lastpipe": re.compile(r"\bshopt\s+-s\s+lastpipe\b"),
            "test -v": re.compile(r"\[\[[^\n]*\s-v\s"),
            "wait -n": re.compile(r"\bwait\s+-n\b"),
            "read -N": re.compile(r"\bread\s+[^\n]*\s-N(?:\s|$)"),
            "pipe ampersand": re.compile(r"(?:^|[ \t])\|&(?:$|[ \t])", re.MULTILINE),
            "combined append redirect": re.compile(r"&>>"),
            "case fallthrough": re.compile(r";;&|;&"),
            "case conversion expansion": re.compile(r"\$\{[^}\n]+(?:\^\^|,,)[^}\n]*\}"),
            "at-operator expansion": re.compile(r"\$\{[^}\n]+@[A-Za-z][^}\n]*\}"),
            "negative array index": re.compile(r"\$\{[^}\n]*\[-[0-9]+\][^}\n]*\}"),
        }
        failures: list[str] = []
        for path in self.shell_surfaces():
            text = path.read_text(encoding="utf-8")
            for label, pattern in forbidden.items():
                match = pattern.search(text)
                if match is None:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                failures.append(f"{path.relative_to(ROOT)}:{line}: {label}")
        self.assertEqual(failures, [], "Accidental Bash 4+ syntax found:\n" + "\n".join(failures))

    def test_arrays_initialized_empty_use_nounset_safe_expansion(self) -> None:
        failures: list[str] = []
        declaration = re.compile(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=\(\)\s*$")
        for path in self.shell_surfaces():
            text = path.read_text(encoding="utf-8")
            for name in sorted(set(declaration.findall(text))):
                safe = f'"${{{name}[@]+"${{{name}[@]}}"}}"'
                without_safe = text.replace(safe, "")
                unsafe = re.search(r"\$\{" + re.escape(name) + r"\[@\]\}", without_safe)
                if unsafe is None:
                    continue
                line = without_safe.count("\n", 0, unsafe.start()) + 1
                failures.append(f"{path.relative_to(ROOT)}:{line}: {name} uses nounset-unsafe [@] expansion")
        self.assertEqual(
            failures,
            [],
            "Arrays initialized empty must use the Bash-3.2 nounset-safe [@] idiom:\n"
            + "\n".join(failures),
        )

    def test_optional_arrays_are_nounset_safe(self) -> None:
        repository = (ROOT / ".github/workflows/repository.yml").read_text(encoding="utf-8")
        self.assertIn(
            '"${runtime_probe_options[@]+"${runtime_probe_options[@]}"}"',
            repository,
        )
        self.assertNotIn('"${runtime_probe_options[@]}" &', repository)

        checkpoint = (ROOT / ".github/workflows/source-checkpoint-publish.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('"${cleanup_args[@]+"${cleanup_args[@]}"}"', checkpoint)
        self.assertNotIn('"${cleanup_args[@]}")"', checkpoint)

        chart = (ROOT / ".github/workflows/public-native-image-chart.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('"${repositories[@]+"${repositories[@]}"}"', chart)
        self.assertNotIn('for repository in "${repositories[@]}"', chart)

    def test_standalone_shell_scripts_parse_as_bash(self) -> None:
        scripts = sorted(ROOT.rglob("*.sh"))
        self.assertGreater(len(scripts), 0)
        for path in scripts:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first_line, "#!/usr/bin/env bash", str(path.relative_to(ROOT)))
            result = subprocess.run(
                ["bash", "-n", str(path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"{path.relative_to(ROOT)} failed bash -n: {result.stderr}",
            )

    def test_maven_modern_bash_requirement_remains_explicit(self) -> None:
        maven = (ROOT / ".github/workflows/maven.yml").read_text(encoding="utf-8")
        self.assertIn("Resolve modern Bash for Maven publication", maven)
        self.assertIn("brew --prefix bash", maven)
        self.assertIn('test "${bash_major}" -ge 4', maven)
        self.assertIn('steps.maven_bash.outputs.executable', maven)


if __name__ == "__main__":
    unittest.main()
