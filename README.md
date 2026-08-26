# StreamScapeTV CI workflows

Small shared GitHub Actions workflows for StreamScapeTV products.

Canonical callable workflows are `apple.yml`, `android.yml`, `python.yml`, `node.yml`, `flutter.yml`, and `gitops.yml`. The workflow YAML is the API and behavior; product repositories supply their own prepare/build/test/release commands.

`actions/agent-state` and `actions/google-drive` are the only shared custom actions. Google Drive stores private CI logs and the `source.snapshot` intent updates tracked-source `source.zip` plus `manifest.json` under the fixed repositories root. `INVENTORY.yaml` is the only repository inventory.
