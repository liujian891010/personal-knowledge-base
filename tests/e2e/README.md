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
