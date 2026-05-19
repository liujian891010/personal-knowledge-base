# Testing

This directory records the release gates and supporting test plans for the V1
desktop, web, sync, and backend path.

## Documents

| Document | Purpose |
| --- | --- |
| [acceptance-plan.md](acceptance-plan.md) | How to run and interpret the release gates |
| [v1.0.43-acceptance-matrix.md](v1.0.43-acceptance-matrix.md) | Fixed v1.0.43 CI lane matrix and P0 coverage |
| [v1.0.43-rc-acceptance-report.md](v1.0.43-rc-acceptance-report.md) | Latest RC acceptance evidence |
| [v1.0.43-stability-regression.md](v1.0.43-stability-regression.md) | Long sync stability evidence |
| [compatibility-plan.md](compatibility-plan.md) | Compatibility dimensions and regression rules |
| [chaos-test-plan.md](chaos-test-plan.md) | Failure-injection scenarios and acceptance criteria |

## Fast Commands

Run the default non-signing acceptance gate:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1
```

List the fixed gate steps:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1 -List
```

Run the signed Windows artifact lane when signing credentials are available:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1 -ReleaseArtifacts
```

Run the long stability lane:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1 -Stability
```
