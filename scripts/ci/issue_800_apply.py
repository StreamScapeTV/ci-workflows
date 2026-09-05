from pathlib import Path

APPLE = Path('.github/workflows/apple.yml')
TEST = Path('tests/test_apple_workflow.py')

apple = APPLE.read_text(encoding='utf-8')
old_apple = '''          if test "${restore_enabled}" = "true"; then
            dependency_fingerprint="$({
              /usr/bin/shasum -a 256 scripts/bootstrap-streamscape-media-binary.sh
              /usr/bin/shasum -a 256 streamscapetv.xcodeproj/project.pbxproj
            } | /usr/bin/shasum -a 256 | awk '{print $1}')"
            xcode_fingerprint="$(xcodebuild -version | /usr/bin/shasum -a 256 | awk '{print $1}')"
            architecture="$(uname -m)"
            cache_key="iptv-apple-default-deps-v3-${architecture}-${xcode_fingerprint}-${dependency_fingerprint}"
'''
new_apple = '''          if test "${restore_enabled}" = "true"; then
            dependency_fingerprint="$(python3 - \\
              scripts/bootstrap-streamscape-media-binary.sh \\
              streamscapetv.xcodeproj/project.pbxproj <<'PY_APPLE_DEPENDENCY_FINGERPRINT'
          from __future__ import annotations

          from pathlib import Path
          import hashlib
          import re
          import sys

          bootstrap_path = Path(sys.argv[1])
          project_path = Path(sys.argv[2])
          bootstrap = bootstrap_path.read_text(encoding="utf-8")
          project = project_path.read_text(encoding="utf-8")

          def section_body(name: str) -> str:
              match = re.search(
                  rf"/\\* Begin {re.escape(name)} section \\*/(?P<body>.*?)/\\* End {re.escape(name)} section \\*/",
                  project,
                  flags=re.S,
              )
              return match.group("body") if match is not None else ""

          def object_blocks(section: str) -> list[str]:
              blocks: list[str] = []
              current: list[str] = []
              depth = 0
              for line in section.splitlines():
                  if depth == 0:
                      if "= {" not in line:
                          continue
                      current = [line]
                      depth = line.count("{") - line.count("}")
                  else:
                      current.append(line)
                      depth += line.count("{") - line.count("}")
                  if current and depth == 0:
                      blocks.append("\\n".join(current))
                      current = []
              if current or depth != 0:
                  raise SystemExit("Apple Swift package reference section is malformed")
              return blocks

          records: list[str] = []
          for name in (
              "RELEASE_REPOSITORY",
              "RELEASE_TAG",
              "RELEASE_COMMIT",
              "ASSET_NAME",
              "ASSET_SHA256",
          ):
              match = re.search(rf"(?m)^{re.escape(name)}=(?P<value>.+?)\\s*$", bootstrap)
              if match is None:
                  raise SystemExit(f"Apple Streamscape Media bootstrap is missing {name}")
              records.append(f"media:{name}={match.group('value').strip()}")

          for block in object_blocks(section_body("XCLocalSwiftPackageReference")):
              path_match = re.search(r"(?m)^\\s*relativePath\\s*=\\s*([^;]+);\\s*$", block)
              if path_match is None:
                  raise SystemExit("Apple local Swift package reference has no relativePath")
              path_value = " ".join(path_match.group(1).split())
              records.append(f"local:{path_value}")

          for block in object_blocks(section_body("XCRemoteSwiftPackageReference")):
              fields = {
                  key: " ".join(value.split())
                  for key, value in re.findall(
                      r"(?m)^\\s*([A-Za-z][A-Za-z0-9_]*)\\s*=\\s*([^;]+);\\s*$",
                      block,
                  )
                  if key != "isa"
              }
              repository_url = fields.get("repositoryURL")
              if not repository_url:
                  raise SystemExit("Apple remote Swift package reference has no repositoryURL")
              records.append(
                  "remote:" + "|".join(f"{key}={fields[key]}" for key in sorted(fields))
              )

          digest = hashlib.sha256()
          for record in sorted(records):
              digest.update(b"\\0package\\0")
              digest.update(record.encode("utf-8"))
          print(digest.hexdigest())
          PY_APPLE_DEPENDENCY_FINGERPRINT
            )"
            xcode_fingerprint="$(xcodebuild -version | /usr/bin/shasum -a 256 | awk '{print $1}')"
            architecture="$(uname -m)"
            cache_key="iptv-apple-default-deps-v4-${architecture}-${xcode_fingerprint}-${dependency_fingerprint}"
'''
if apple.count(old_apple) != 1:
    raise SystemExit(f'expected one Apple cache block, found {apple.count(old_apple)}')
apple = apple.replace(old_apple, new_apple)
APPLE.write_text(apple, encoding='utf-8')

test = TEST.read_text(encoding='utf-8')
replacements = [
('''        self.assertIn("test -f streamscapetv.xcodeproj/project.pbxproj", prepare)\n        self.assertIn("restore_enabled=false", prepare)\n        self.assertIn("save_enabled=false", prepare)\n        self.assertIn("iptv-apple-default-deps-v3-", prepare)\n''', '''        self.assertIn("test -f streamscapetv.xcodeproj/project.pbxproj", prepare)\n        self.assertIn('section_body("XCLocalSwiftPackageReference")', prepare)\n        self.assertIn('section_body("XCRemoteSwiftPackageReference")', prepare)\n        self.assertIn('fields.get("repositoryURL")', prepare)\n        self.assertNotIn('/usr/bin/shasum -a 256 streamscapetv.xcodeproj/project.pbxproj', prepare)\n        self.assertIn("restore_enabled=false", prepare)\n        self.assertIn("save_enabled=false", prepare)\n        self.assertIn("iptv-apple-default-deps-v4-", prepare)\n'''),
('''            (root / "scripts/bootstrap-streamscape-media-binary.sh").write_text(\n                "#!/bin/sh\\n",\n                encoding="utf-8",\n            )\n            (root / "streamscapetv.xcodeproj").mkdir()\n            (root / "streamscapetv.xcodeproj/project.pbxproj").write_text(\n                "// fixture\\n",\n                encoding="utf-8",\n            )\n''', '''            bootstrap_fixture = """#!/bin/sh\nRELEASE_REPOSITORY="StreamScapeTV/streamscape-media"\nRELEASE_TAG="v1.2.1"\nRELEASE_COMMIT="22c2ebb662d774d862e3dcb65e1dbb55b3e9253d"\nASSET_NAME="streamscape-media-1.2.1-apple-binary.zip"\nASSET_SHA256="0fc2e0c9713863bc3015628a04cccf2d36fccf8c1f56bb6f30204469a684a51a"\n"""\n            (root / "scripts/bootstrap-streamscape-media-binary.sh").write_text(\n                bootstrap_fixture,\n                encoding="utf-8",\n            )\n            (root / "streamscapetv.xcodeproj").mkdir()\n            project_path = root / "streamscapetv.xcodeproj/project.pbxproj"\n            project_path.write_text(\n                """// fixture\nCURRENT_PROJECT_VERSION = 1;\n/* Begin XCLocalSwiftPackageReference section */\n\\t\\tA1B2C3D4E5F60718293A4B5D /* XCLocalSwiftPackageReference "StreamscapeMediaApple" */ = {\n\\t\\t\\tisa = XCLocalSwiftPackageReference;\n\\t\\t\\trelativePath = Vendor/StreamscapeMediaApple;\n\\t\\t};\n/* End XCLocalSwiftPackageReference section */\n/* Begin XCRemoteSwiftPackageReference section */\n\\t\\tDBC44F5D80EDBFA365244685 /* XCRemoteSwiftPackageReference "purchases-ios-spm" */ = {\n\\t\\t\\tisa = XCRemoteSwiftPackageReference;\n\\t\\t\\trepositoryURL = "https://github.com/RevenueCat/purchases-ios-spm";\n\\t\\t\\trequirement = {\n\\t\\t\\t\\tkind = upToNextMajorVersion;\n\\t\\t\\t\\tminimumVersion = 5.87.1;\n\\t\\t\\t};\n\\t\\t};\n/* End XCRemoteSwiftPackageReference section */\n""",\n                encoding="utf-8",\n            )\n'''),
('''            self.assertEqual((tag["restore_enabled"], tag["save_enabled"]), ("false", "false"))\n            self.assertEqual((unrelated["restore_enabled"], unrelated["save_enabled"]), ("false", "false"))\n\n            subprocess.run(["git", "switch", "-c", "main"], cwd=root, check=True, capture_output=True)\n''', '''            self.assertEqual((tag["restore_enabled"], tag["save_enabled"]), ("false", "false"))\n            self.assertEqual((unrelated["restore_enabled"], unrelated["save_enabled"]), ("false", "false"))\n\n            baseline_key = develop["key"]\n            project_text = project_path.read_text(encoding="utf-8")\n            project_path.write_text(\n                project_text.replace("CURRENT_PROJECT_VERSION = 1;", "CURRENT_PROJECT_VERSION = 2;"),\n                encoding="utf-8",\n            )\n            self.assertEqual(cache_flags("StreamScapeTV/iptv-apple", "develop")["key"], baseline_key)\n\n            project_path.write_text(\n                project_text.replace("minimumVersion = 5.87.1;", "minimumVersion = 5.88.0;"),\n                encoding="utf-8",\n            )\n            self.assertNotEqual(cache_flags("StreamScapeTV/iptv-apple", "develop")["key"], baseline_key)\n\n            project_path.write_text(\n                project_text.replace(\n                    "relativePath = Vendor/StreamscapeMediaApple;",\n                    "relativePath = Vendor/StreamscapeMediaAppleV2;",\n                ),\n                encoding="utf-8",\n            )\n            self.assertNotEqual(cache_flags("StreamScapeTV/iptv-apple", "develop")["key"], baseline_key)\n\n            project_path.write_text(project_text, encoding="utf-8")\n            bootstrap = root / "scripts/bootstrap-streamscape-media-binary.sh"\n            bootstrap.write_text(bootstrap_fixture + "# logging-only edit\\n", encoding="utf-8")\n            self.assertEqual(cache_flags("StreamScapeTV/iptv-apple", "develop")["key"], baseline_key)\n            bootstrap.write_text(\n                bootstrap_fixture.replace(\n                    'ASSET_SHA256="0fc2e0c9713863bc3015628a04cccf2d36fccf8c1f56bb6f30204469a684a51a"',\n                    'ASSET_SHA256="1fc2e0c9713863bc3015628a04cccf2d36fccf8c1f56bb6f30204469a684a51a"',\n                ),\n                encoding="utf-8",\n            )\n            self.assertNotEqual(cache_flags("StreamScapeTV/iptv-apple", "develop")["key"], baseline_key)\n            bootstrap.write_text(bootstrap_fixture, encoding="utf-8")\n\n            subprocess.run(["git", "switch", "-c", "main"], cwd=root, check=True, capture_output=True)\n''')
]
for old, new in replacements:
    count = test.count(old)
    if count != 1:
        raise SystemExit(f'expected one test block, found {count}')
    test = test.replace(old, new)
TEST.write_text(test, encoding='utf-8')
