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
$env:PYTHONPATH='../../../packages/vault-core/src;../../..'
python -m clients.desktop.cli `
  --vault-root C:\vaults\pkb `
  --base-url http://127.0.0.1:8000 `
  --vault-id vault-001 `
  --device-id desktop-shanghai `
  sync-shell-snapshot `
  --output-json fixtures\live-sync-shell.json
```

Validate the checked-in example fixture and TypeScript adapter:

```powershell
npm run validate:sync-shell
```
