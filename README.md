# StreamScapeTV CI workflows

Small shared GitHub Actions workflows for StreamScapeTV products.

Canonical callable validation workflows are `apple.yml`, `android.yml`, `python.yml`, `node.yml`, and `flutter.yml`. Their YAML owns a small reviewed catalog of technology profiles; callers select a profile and bounded parameters instead of passing shell commands. `validation.gitops` was retired because no live external consumer remains.

`actions/agent-state` and `actions/google-drive` are the only shared custom actions. Google Drive stores literal-scrubbed private CI logs and the `source.snapshot` intent updates tracked-source `source.zip` plus `manifest.json` under the fixed repositories root. `INVENTORY.yaml` is the only repository inventory.
