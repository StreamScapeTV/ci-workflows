from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_types import AppleValidationError

ROOT = Path(__file__).resolve().parents[1]


class AppleContractFragmentPathSafetyTests(unittest.TestCase):
    def test_symlinked_fragment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts = root / "contracts"
            contracts.mkdir()
            (contracts / "apple-validation.json").write_text(
                (ROOT / "contracts/apple-validation.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            target = root / "outside-fragment.json"
            target.write_text(
                json.dumps({"tasks": {}, "consumer_contracts": {}}),
                encoding="utf-8",
            )
            (contracts / "apple-validation-symlink.json").symlink_to(target)
            with self.assertRaises(AppleValidationError) as failure:
                load_apple_contract(root)
            self.assertEqual(failure.exception.code, "contract_invalid")


if __name__ == "__main__":
    unittest.main()
