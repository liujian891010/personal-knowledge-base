# e2e

End-to-end checks for the sync loop.

## Sync Server Smoke

`sync_server_smoke.py` starts the FastAPI sync server on a temporary local port and runs:

1. device registration
2. blob upload capability
3. blob upload
4. CAS commit
5. head lookup
6. manifest fetch
7. blob download capability
8. blob download

Run from the repository root:

```powershell
python tests\e2e\sync_server_smoke.py
```

## Desktop Sync Smoke

`desktop_sync_smoke.py` starts the local FastAPI sync server and drives the real desktop service/runtime against it with two temporary vault roots:

1. register two desktop devices
2. initialize both local vaults
3. submit a detected local note from vault A
4. pull and apply that note into vault B
5. edit and submit the same note from vault B
6. pull and apply the update back into vault A

Run from the repository root with `PYTHONPATH` pointing at `packages/vault-core/src`:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'
python tests\e2e\desktop_sync_smoke.py
```

Optional port override:

```powershell
$env:NOTEAPP_SMOKE_PORT='8091'
python tests\e2e\sync_server_smoke.py
```

Prerequisites:

```powershell
cd apps\backend\noteapp-server
pip install -r requirements.txt
```
