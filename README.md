# StreamScapeTV CI workflows

Small shared GitHub Actions workflows for StreamScapeTV products.

The workflow files are the API and behavior. Product repositories supply their own prepare/build/test/release commands. `actions/agent-state` and `actions/google-drive` are the only shared custom actions. Google Drive stores private CI logs now and is also the shared storage primitive for source snapshots. `INVENTORY.yaml` is the only repository inventory.
