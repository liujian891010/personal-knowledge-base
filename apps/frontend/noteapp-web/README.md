# noteapp-web

Zero-dependency static shell prototype for the desktop sync contracts.

Current scope:

1. Renders `sync-center` JSON, including panel, cards, summary metrics, and `recent_activity`.
2. Accepts standalone `sync-activity` JSON for activity-only inspection.
3. Previews action contracts as shell commands instead of re-implementing desktop execution logic.
4. Ships with a bundled sample payload under `fixtures/`.

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
