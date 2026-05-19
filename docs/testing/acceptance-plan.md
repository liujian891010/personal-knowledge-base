# Acceptance Plan

The v1.0.43 release gate is fixed by
`docs/testing/v1.0.43-acceptance-matrix.md` and executed through
`scripts\ci\v1043-acceptance.ps1`.

## Gates

| Gate | Command | Required For |
| --- | --- | --- |
| Default acceptance | `powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1` | Every RC candidate |
| Signed artifact acceptance | `powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1 -ReleaseArtifacts` | Public Windows distribution |
| Stability acceptance | `powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1 -Stability` | Long-running sync confidence |

## Default Acceptance Coverage

The default gate covers diff hygiene, backend unit tests, JSON and SQLite sync
smokes, production backend smoke, vault-core unit tests, desktop unit tests,
desktop sync smoke, frontend lint/build, frontend entry policy, frontend
dist/snapshot smokes, and desktop frontend bundle input.

The signed artifact lane is separate because it requires Windows code signing
credentials. A sandbox waiver is acceptable only for local validation; public
distribution requires Authenticode verification to return `Valid`.

## Required Evidence Before Release

1. Default acceptance output.
2. Signed artifact output or an explicit signing-lane waiver for non-public RC
   validation.
3. Stability run output and the recorded stability report.
4. RC notes for AI provider behavior, documentation review, performance notes,
   and known waivers.

## Failure Handling

1. Treat any non-zero command exit as a failed gate.
2. Fix the failing lane before updating the RC report.
3. If a lane is environment-dependent, document the exact missing dependency and
   why the waiver does not apply to public release.
4. Keep generated artifacts and local fixture output out of Git unless they are
   intentionally checked-in protocol golden fixtures.
