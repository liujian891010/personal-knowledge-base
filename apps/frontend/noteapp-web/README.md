# noteapp-web

Zero-dependency static shell prototype for the desktop sync contracts.

Current scope:

1. Renders `sync-shell-snapshot` JSON, including shell metadata, `sync_center`, and `activity_feed`.
2. Still accepts raw `sync-center` JSON, including panel, cards, summary metrics, and `recent_activity`.
3. Accepts standalone `sync-activity` JSON for activity-only inspection.
4. Previews action contracts as shell commands instead of re-implementing desktop execution logic.
5. Can auto-load a custom payload path through `?payload=...`.
6. Ships with bundled sample payloads under `fixtures/`.

Local usage:

```powershell
cd apps\frontend\noteapp-web
npm run dev
```

Build static output:

```powershell
cd apps\frontend\noteapp-web
npm run build
```

The build writes files into `dist/` without external dependencies.

Pair it with the desktop CLI:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'; python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url https://sync.example.com `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  sync-shell-snapshot `
  --output-json apps\frontend\noteapp-web\fixtures\live-sync-shell.json
```

Then start the shell and point it at that exported snapshot:

```text
http://127.0.0.1:4173/?payload=./fixtures/live-sync-shell.json
```
