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
$env:PKB_VAULT_ROOT='C:\vaults\pkb'
$env:PKB_BASE_URL='https://sync.example.com'
$env:PKB_VAULT_ID='vault-001'
$env:PKB_DEVICE_ID='desktop-shanghai'
npm run sync:snapshot
```

The helper writes `fixtures/live-sync-shell.json`, which is gitignored.

Then start the shell. Without any query parameter it will try `live-sync-shell.json` first and fall back to the bundled sample:

```powershell
cd apps\frontend\noteapp-web
npm run dev
```

You can still point it at any custom exported snapshot explicitly:

```text
http://127.0.0.1:4173/?payload=./fixtures/live-sync-shell.json
```
