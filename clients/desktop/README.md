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

`download-blobs` returns `downloaded_blobs_base64` on stdout:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  download-blobs --blob-id blob-a --blob-id blob-b
```
