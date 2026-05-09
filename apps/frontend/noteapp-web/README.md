# noteapp-web

Zero-dependency static shell prototype for the desktop sync contracts.

Current scope:

1. Renders `sync-shell-snapshot` JSON, including shell metadata, `sync_center`, and `activity_feed`.
2. Still accepts raw `sync-center` JSON, including panel, cards, summary metrics, and `recent_activity`.
3. Accepts standalone `sync-activity` JSON for activity-only inspection.
4. Previews action contracts as shell commands instead of re-implementing desktop execution logic.
5. Can auto-load a custom payload path through `?payload=...`.
6. Ships with bundled sample payloads under `fixtures/`.
7. Includes a prototype workspace shell with file tree, editor, and AI side panel chrome above the sync dock.

Local usage:

```powershell
cd apps\frontend\noteapp-web
npm run dev
```

You can also pass bridge config directly on the dev command line:

```powershell
npm run dev -- --vault-root C:\vaults\pkb --base-url https://sync.example.com --vault-id vault-001 --device-id desktop-shanghai
```

When `PKB_VAULT_ROOT` / `PKB_BASE_URL` / `PKB_VAULT_ID` / `PKB_DEVICE_ID` are set, the dev server also enables a local desktop bridge:

1. `POST /api/bridge/refresh-snapshot`
2. `POST /api/bridge/execute-action`
3. `GET /api/bridge/status`

The shell uses that bridge for `Refresh Local Snapshot` and `Run Selected Action`. Internally the action path now delegates to the desktop CLI's `execute-sync-action-and-snapshot` boundary instead of stitching two commands together in the browser shell.

If you do not want to export environment variables every time, you can create a local gitignored config file:

```json
{
  "vaultRoot": "C:/vaults/pkb",
  "baseUrl": "https://sync.example.com",
  "vaultId": "vault-001",
  "deviceId": "desktop-shanghai",
  "activityLimit": 20
}
```

Path:

```text
apps/frontend/noteapp-web/bridge.local.json
```

You can start from the tracked example file:

```text
apps/frontend/noteapp-web/bridge.local.example.json
```

The dev server also returns structured bridge errors from `/api/bridge/refresh-snapshot` and `/api/bridge/execute-action`, and `GET /api/bridge/status` now exposes config source and diagnostics so the shell can explain missing settings or CLI failures.

The browser shell polls bridge status every 15 seconds and refreshes it again when the tab becomes visible, so desktop-side config changes show up without restarting the page.

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
$env:PKB_VAULT_ROOT='C:\vaults\pkb'
$env:PKB_BASE_URL='https://sync.example.com'
$env:PKB_VAULT_ID='vault-001'
$env:PKB_DEVICE_ID='desktop-shanghai'
npm run dev
```

You can still point it at any custom exported snapshot explicitly:

```text
http://127.0.0.1:4173/?payload=./fixtures/live-sync-shell.json
```
