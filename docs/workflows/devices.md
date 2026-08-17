# Physical-device reusable workflow

`validation.device` is the canonical non-versioned physical-device API implemented by `.github/workflows/reusable-device.yml`.

The caller owns the exact admitted SHA, device family, bounded device capability, **semantic host capacity**, checked-in prepare/test/evidence/cleanup script paths, bounded test-script arguments, bounded non-secret environment, request identity, and optional named redacted-evidence exception. Central owns source admission, semantic runner resolution, deterministic discovery, authorization handling, fencing, execution order, restoration, cleanup, residue checks, and stable outputs.

The only public secret is `device_authorization_receipt`. Secrets, raw runner labels, runner names, raw device identifiers, serials, UDIDs, arbitrary commands, and caller-selected secret names are not public inputs. Caller environment is explicitly non-secret and rejects credential/token/password/secret-style keys as well as Central/GitHub/runner authority variables.

## Host placement

Host selection is semantic. Android physical validation may request `mobile` when the reviewed physical device is reachable from mobile capacity, or `apple` when an Android phone is attached to the organization-managed macOS capacity. iOS and tvOS physical validation use `apple`. The resolved selector remains Central-owned.

**Ordinary Android** build/test/lint validation is a different API and remains on semantic `mobile` capacity. Selecting `mobile` or `apple` never by itself proves a physical device is attached or authorized.

## Execution order

A real physical request must pass exact-source admission and an exact `device_authorization_receipt` before the heavy executor is scheduled. The executor uses one workspace and performs, in order:

1. exact admitted-source checkout and source revalidation;
2. deterministic physical-device discovery;
3. `device-lock/1` acquire and verify;
4. caller-owned checked-in prepare, test, and evidence stages;
5. caller-owned restoration/cleanup **exactly once** while the lock is still valid;
6. expected-state lock release and lock-residue verification;
7. Central device/source/workspace cleanup and zero-residue checks.

The public API has no GitHub Actions cache. The workflow retains **zero routine Actions artifacts**; only a separately reviewed bounded redacted-evidence exception may change that policy.

## Synthetic contract smoke

Central's permanent smoke runs the same generic request parser and typed plan for Android, iOS, and tvOS using checked-in synthetic fixtures. Synthetic mode is restricted to the `StreamScapeTV/ci-workflows` repository, remains non-physical, and cannot authorize the physical executor even on a trusted branch push or manual smoke dispatch.
