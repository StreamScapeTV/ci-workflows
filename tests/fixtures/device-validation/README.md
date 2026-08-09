# Synthetic physical-device fixtures

These fixtures contain no real serial, UDID, hostname, endpoint, credential, fleet name, personal data, or private media.

`android.txt` is a bounded projection of Android discovery output. `ios.json` and `tvos.json` are bounded projections of Apple `devicectl` output. They exist only for portable contract tests and the device contract smoke workflow. Passing these fixtures never certifies a physical device.

The four scripts are inert checked-in stage identities for the `ciw-device-synthetic` command profile. They do not inspect, lock, install to, erase, reboot, or execute against hardware.
