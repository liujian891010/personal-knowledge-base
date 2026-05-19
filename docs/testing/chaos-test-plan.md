# Chaos Test Plan

The chaos plan focuses on sync correctness under interruptions, stale devices,
storage faults, and recovery windows. These scenarios extend the default
acceptance gate; they do not replace it.

## Required Scenarios

| Scenario | Driver | Pass Condition |
| --- | --- | --- |
| Interrupted pull/apply | Desktop CLI/service tests and targeted recovery tests | Active `sync_apply_journal` can be finalized or safely degraded without data loss |
| Interrupted commit submission | Desktop service recovery tests | Commit intent journal recovers, resolves remote intent, or blocks new commits until safe |
| Long continuous sync | `scripts\ci\v1043-acceptance.ps1 -Stability` | 1000 sync iterations complete with stable final hash |
| Long-offline delete | Stability regression | Reconnected device does not resurrect deleted canonical paths |
| Backend CAS conflict | Backend unit/production smoke | Conflict response preserves monotonic revision and does not accept stale manifest |
| Capability expiry/revocation | Backend unit/production smoke | Expired or revoked upload/download capabilities fail closed |
| Object storage fault | Backend storage tests or production smoke extension | Blob operation failure is surfaced without committing an invalid manifest |
| Dirty local file during pull | Desktop unit coverage | Local dirty bytes are preserved as conflict artifacts and canonical state advances safely |

## Manual Fault-Injection Guidance

1. Stop the backend during `sync-cycle-loop`, restart it, then run
   `recover-pull-apply` or `sync-once`.
2. Kill the desktop process after staging but before finalizing pull/apply, then
   run `recover-pull-apply`.
3. Submit the same base revision from two devices and verify that exactly one
   commit advances the head.
4. Corrupt a local staging file and verify recovery isolates it rather than
   materializing it as canonical user content.
5. Revoke a device session and verify vault APIs and blob capabilities reject
   that device.

## Evidence Rules

1. Record the command, environment variables, iteration count, and final vault
   head revision.
2. Store only sanitized logs and deterministic fixtures.
3. Do not commit user vault content, secrets, or generated release artifacts.
4. Promote any repeated manual chaos check into an automated E2E or unit test
   before treating it as a release-blocking guarantee.
