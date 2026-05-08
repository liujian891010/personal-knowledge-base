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

`sync-once` is the current one-shot automation boundary:

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

`sync-cycle` adds an optional local workspace commit step between recovery and pull:

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
