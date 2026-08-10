# Synthetic physical-device fixtures

All rows are synthetic. No real serial, UDID, host, endpoint, credential, fleet identity, personal data, or private media is stored here. Passing these fixtures never certifies hardware.

`cases.json` is the exact inventory and semantic index. Every listed data fixture is consumed by `tests/test_device_validation.py`; the four scripts are the inert checked-in stage identities referenced by the `ciw-device-synthetic` command profile and verified by the fixture-completeness test.

There are deliberately no hidden checkpoint files, placeholder files, or unnamed filler fixtures.
