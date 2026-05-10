# noteapp-web

Vite + React UI for the NoteApp personal knowledge base.

## Run Locally

Prerequisite: Node.js.

```powershell
npm install
npm run dev
```

The dev server listens on:

```text
http://127.0.0.1:3000/
```

Set `VITE_NOTEAPP_SYNC_BRIDGE_URL` when the browser should use a bridge address other than `http://127.0.0.1:3187`.

## Build

```powershell
npm run build
```

## Sync Shell Fixture

The desktop CLI can write the app-shell sync snapshot consumed by the web UI contract:

```powershell
$env:PYTHON='C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe'
$env:NOTEAPP_VAULT_ROOT='C:\vaults\pkb'
$env:NOTEAPP_SYNC_BASE_URL='http://127.0.0.1:8000'
$env:NOTEAPP_VAULT_ID='vault-001'
$env:NOTEAPP_DEVICE_ID='desktop-shanghai'
$env:NOTEAPP_BEARER_TOKEN='<device bearer token>'
npm run sync:snapshot
```

The app falls back to `fixtures/live-sync-shell.example.json` when no live public fixture exists.
Generated files under `public/fixtures/` are local runtime artifacts and are ignored by git.
Set `NOTEAPP_SYNC_SNAPSHOT_OUTPUT` to write the snapshot somewhere else for smoke tests or tooling.
The settings panes use the same pattern with `fixtures/local-settings-snapshot.example.json` and
`NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT`.

Preview the generated command without contacting the server:

```powershell
npm run sync:snapshot -- --dry-run
```

Generate the local settings snapshot consumed by the Settings panes:

```powershell
npm run settings:snapshot
```

Generate the local workspace file index consumed by the Explorer:

```powershell
npm run workspace:files
```

Read a single workspace file content fixture:

```powershell
$env:NOTEAPP_WORKSPACE_FILE_ID='file-roadmap'
npm run workspace:content
```

Write local settings and refresh the same live fixture:

```powershell
$env:NOTEAPP_SETTINGS_INPUT_JSON='C:\vaults\pkb-settings.json'
npm run settings:write
```

Execute an action id from the snapshot and refresh the same live fixture:

```powershell
$env:NOTEAPP_SYNC_ACTION_ID='submit-detected-commit'
npm run sync:action
```

Run the local HTTP bridge used by the browser UI:

```powershell
npm run sync:bridge
```

The bridge binds to `127.0.0.1` and defaults CORS to `http://127.0.0.1:3000`. Set `NOTEAPP_SYNC_BRIDGE_ORIGIN` if the frontend runs elsewhere. Non-loopback hosts are rejected unless `NOTEAPP_SYNC_BRIDGE_ALLOW_REMOTE=true` is set.

Bridge endpoints:

```text
GET  http://127.0.0.1:3187/api/sync/snapshot
POST http://127.0.0.1:3187/api/sync/actions/<action-id>
GET  http://127.0.0.1:3187/api/sync/live
GET  http://127.0.0.1:3187/api/settings/snapshot
POST http://127.0.0.1:3187/api/settings/snapshot
GET  http://127.0.0.1:3187/api/settings/live
GET  http://127.0.0.1:3187/api/workspace/files
GET  http://127.0.0.1:3187/api/workspace/files/<file-id>/content
GET  http://127.0.0.1:3187/api/workspace/live
```

Validate the checked-in example fixture and TypeScript adapter:

```powershell
npm run validate:sync-shell
npm run validate:settings-snapshot
npm run validate:workspace-files
npm run validate:workspace-file-content
```
