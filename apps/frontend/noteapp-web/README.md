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

Preview the generated command without contacting the server:

```powershell
npm run sync:snapshot -- --dry-run
```

Validate the checked-in example fixture and TypeScript adapter:

```powershell
npm run validate:sync-shell
```
