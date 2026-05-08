# desktop

Desktop client runtime, workspace, service, and CLI assembly for AG05 sync flows.

Current CLI boundary:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  init --now-ms 1770000100000
```

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  pull --rewritten-at 1770000100100
```

Add `--plan-apply` to return an explicit post-pull apply plan before any blob download or local materialization work. The current plan surfaces `write`, `move`, and `delete` actions plus `blocking_paths` that will require a later two-phase materialization step.

Add `--download-required-blobs` to have `pull` immediately follow up on the returned `required_blob_ids`, and optionally combine it with `--output-dir .\downloaded-blobs` to materialize the encrypted blob payloads locally.

Add `--decrypt-required-blobs` on top of that to route the downloaded encrypted blobs through the desktop crypto provider and return an explicit `file_id -> plaintext` result. Add `--plaintext-output-dir .\materialized` when that decrypted payload should be materialized under a separate local directory using manifest-relative paths, while still stopping short of writing those files back into the live vault.

Add `--stage-required-blobs` on top of `--decrypt-required-blobs` when the decrypted payload should be written into the vault's internal `.noteapp/staging/<file_id>.staging` paths and paired with a persisted `sync_apply_journal` boundary for later apply work.

Add `--apply-nonblocking` to execute the fast-path live-vault apply boundary in one step: pull, download/decrypt required blobs, stage them, materialize non-blocking `write/move/delete` actions, and then advance the local `sync_apply_journal` through `filemap_rewrite/finalizing` cleanup. This path still rejects any plan with `blocking_paths`.

Add `--apply` to run the fuller pull apply boundary, including the current two-phase staging path for `blocking_paths` such as path swaps and rename cycles.

Use `recover-pull-apply --normalized-at ...` when a previous pull apply left `sync_apply_journal` behind. The current recovery fast-path finalizes journals already in `materializing`, `filemap_rewrite`, or `finalizing` when the workspace content already matches the target filemap state.

During `pull --apply` / `pull --apply-nonblocking`, the desktop client now also persists a local `.noteapp/sync-apply-plan.json` replay plan. That lets `recover-pull-apply` re-run interrupted `staging` / `materializing` work instead of only handling final journal cleanup.

If that replay plan is missing, corrupt, no longer matches the active `sync_apply_journal`, points at replay payloads that are no longer present, or the journal is still parked in the early `preparing` phase, `recover-pull-apply` now degrades safely by marking the local manifest summary stale, clearing the journal, and moving leftover staging files into `.noteapp/staging-orphans/`. The same command also isolates stray pull-apply `.staging` files and removes stale `.noteapp/sync-apply-plan.json` state when no active `sync_apply_journal` exists anymore.

`sync-once` is the current one-shot automation boundary. It now runs commit recovery, pull-apply recovery, and then the full `pull_and_apply` path instead of stopping at the older pull-and-ack boundary:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  sync-once `
  --now-ms 1770000100000 `
  --normalized-at 1770000100100 `
  --rewritten-at 1770000100200
```

For `sync-once`, `sync-loop`, `sync-cycle`, and `sync-cycle-loop`, these orchestration timestamps can now be omitted. The desktop client will derive a consistent ordered set from the current clock, while still allowing any explicit argument to override the generated value.

Those higher-level orchestration paths now run both commit recovery and pull-apply recovery before any new submit or pull step, so an active `sync_apply_journal` is drained through the recovery path before normal sync work resumes.

`sync-loop` runs bounded repeated `sync-once` iterations, with optional timestamp stepping and sleep interval:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  sync-loop `
  --iterations 3 `
  --now-ms 1770000100000 `
  --normalized-at 1770000100100 `
  --rewritten-at 1770000100200 `
  --step-ms 1000 `
  --interval-seconds 2
```

Add `--continue-on-error` when the loop should capture iteration failures and keep going instead of aborting on the first exception.

`sync-cycle` adds an optional local workspace commit step between recovery and the same full pull-apply boundary:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  sync-cycle `
  --now-ms 1770000100000 `
  --normalized-at 1770000100100 `
  --submit-created-at 1770000100150 `
  --file-id file-live `
  --encrypted-dir .\encrypted-blobs `
  --rewritten-at 1770000100200
```

If `--encrypted-map` / `--encrypted-dir` is omitted, the desktop client currently auto-generates a deterministic placeholder encrypted blob from the selected plaintext content. This keeps AG05 wiring moving until a real crypto provider lands.

When commit recovery or pull-apply recovery leaves the local manifest summary in a `stale` state, `sync-cycle` now promotes the full pull-apply step ahead of the optional submit step so the cycle re-establishes a valid remote baseline before attempting a new commit. Those recovery results now also expose a top-level `requires_full_pull` flag so orchestration can consume the boundary directly instead of inferring it from nested payload shape. In that reordered branch, the submit/cleanup timestamps are also rebased forward to stay monotonic after the pull timestamp.

When you want the cycle to detect and submit local changes automatically instead of enumerating `--file-id`, use `--submit-detected`:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  sync-cycle `
  --submit-created-at 1770000100150 `
  --submit-detected
```

`sync-cycle-loop` repeats the full cycle with bounded iterations and stepped timestamps:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  sync-cycle-loop `
  --iterations 2 `
  --now-ms 1770000100000 `
  --normalized-at 1770000100100 `
  --submit-created-at 1770000100150 `
  --file-id file-live `
  --rewritten-at 1770000100200 `
  --step-ms 1000 `
  --interval-seconds 2
```

`sync-cycle-loop` also supports `--continue-on-error`, returning per-iteration failure records in JSON while continuing later iterations.

`sync-cycle-loop` accepts the same `--submit-detected` mode for automatic local change submit on each iteration.

Current `pull` / `sync-*` JSON results also expose `required_blob_ids` under the applied reconcile result. The desktop service can now follow that through seven explicit boundaries: returning a concrete apply plan, downloading the required encrypted blobs, downloading plus decrypting them into a `file_id -> plaintext` result, materializing those plaintext files under a caller-provided output root, staging them under `.noteapp/staging/` together with a persisted `sync_apply_journal`, executing the non-blocking subset of the apply plan against the live vault, and executing the current two-phase staging path for `blocking_paths`. When that live apply would overwrite, delete, or move away from a dirty tracked local file, the current boundary now preserves the dirty bytes as a local `conflict_copy`, stores the copy under the vault or `.noteapp/conflict-orphans/` as needed, stages the pulled canonical blob when a dirty rename can no longer trust the live source bytes, and marks `has_unresolved_conflicts=true` before continuing with the pulled canonical state. Higher-level `sync-*` orchestration now routes through the full pull-apply session rather than the older ack-only pull path. The remaining gap is deeper recovery fidelity around mid-apply restarts, not the basic apply path itself.

`sync-worker` is a thinner bounded worker wrapper around `sync-cycle-loop`: it auto-plans timestamps, defaults to `continue_on_error=true`, and derives `step_ms` from `interval_seconds` when you do not provide one.

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  sync-worker `
  --iterations 2 `
  --file-id file-live `
  --interval-seconds 30
```

`sync-worker` also accepts `--submit-detected`, which routes the worker through local change detection plus auto-submit instead of explicit `--file-id` selection. The explicit `--file-id` submit path now also validates the selected workspace files against the frozen filemap metadata before staging, so mid-flight drift aborts instead of producing a mixed snapshot.

After each worker run, the desktop client also writes the latest worker summary to `.noteapp/sync-worker-state.json` inside the vault.

Use `worker-state` to read the persisted local worker summary, and `worker-health` to read a condensed local health view derived from that state file.

Use `detect-local-changes` to scan the current vault root for tracked file modifications, missing tracked files, and untracked local files before wiring automatic submit flows. A local rename still appears as a `missing + untracked` pair at scan time. `.ai/raw/` and `.ai/log.md` stay outside the regular sync set and are ignored by this scan.

Use `submit-detected-commit` to auto-submit detected `modified`, `missing`, and `untracked` changes. New local files now receive generated UUID `file_id` values by default, but the desktop service builder can also inject a custom file-id allocator for app-level identity control. `blob_id` plus encrypted blob materialization are routed through a replaceable desktop crypto provider that still defaults to the current deterministic placeholder implementation. The detected submit plan also writes explicit `source_version_token` metadata for changed files, and the desktop service now re-reads the workspace against that frozen metadata before materializing the commit snapshot so post-plan drift aborts the submit instead of silently mixing file versions. When the workspace is already clean, the command returns a skipped no-op result instead of failing. The current boundary also folds an unambiguous same-content `missing + untracked` pair into a tracked rename that preserves the original `file_id`. Any unresolved `conflict_copy` entry, or even a non-empty `.noteapp/conflict-orphans/` directory discovered before submit, now promotes `has_unresolved_conflicts` and blocks commit creation until the conflict artifacts are resolved. Unsupported cases such as modified `conflict_copy` records are still rejected.

`submit-commit` payload files use `file_id -> base64 bytes` JSON objects:

```json
{
  "file-live": "IyBMaXZlIG5vdGUK"
}
```

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  submit-commit `
  --created-at 1770000100200 `
  --commit-intent-id intent-001 `
  --content-map .\content-map.json `
  --encrypted-map .\encrypted-map.json
```

`submit-workspace-commit` reads the selected plain content directly from the current vault files:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  submit-workspace-commit `
  --created-at 1770000100300 `
  --file-id file-live
```

You can still override the blob payloads with `--encrypted-map` or `--encrypted-dir` when needed.

`download-blobs` returns `downloaded_blobs_base64` on stdout:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  download-blobs --blob-id blob-a --blob-id blob-b
```

Use `--output-dir` to materialize downloaded blobs as local files named `<blob-id>.blob`:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  download-blobs `
  --blob-id blob-a `
  --blob-id blob-b `
  --output-dir .\downloaded-blobs
```
