param(
  [switch]$List,
  [switch]$ReleaseArtifacts
)

$ErrorActionPreference = 'Stop'

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptRoot '..\..')
$Python = if ([string]::IsNullOrWhiteSpace($env:PYTHON)) { 'python' } else { $env:PYTHON }

function Join-RepoPathList {
  param([string[]]$Paths)
  return ($Paths | ForEach-Object { Join-Path $RepoRoot $_ }) -join [IO.Path]::PathSeparator
}

function Invoke-WithEnv {
  param(
    [hashtable]$Variables,
    [scriptblock]$Block
  )

  $previous = @{}
  foreach ($key in $Variables.Keys) {
    $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, [string]$Variables[$key], 'Process')
  }

  try {
    & $Block
  } finally {
    foreach ($key in $Variables.Keys) {
      if ($null -eq $previous[$key]) {
        [Environment]::SetEnvironmentVariable($key, $null, 'Process')
      } else {
        [Environment]::SetEnvironmentVariable($key, [string]$previous[$key], 'Process')
      }
    }
  }
}

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Block
  )

  Write-Host "==> $Name"
  Push-Location $RepoRoot
  try {
    & $Block
  } finally {
    Pop-Location
  }
}

$VaultCorePath = Join-RepoPathList @('packages\vault-core\src')
$DesktopPythonPath = Join-RepoPathList @('packages\vault-core\src', 'packages\ai-core\src', '.')

$Steps = [System.Collections.Generic.List[object]]::new()

$Steps.Add([pscustomobject]@{
  Name = 'quality:diff-check'
  Run = { git diff --check }
})

$Steps.Add([pscustomobject]@{
  Name = 'backend:unit'
  Run = {
    & $Python -m unittest `
      apps\backend\noteapp-server\tests\test_main.py `
      apps\backend\noteapp-server\tests\test_sync_store.py
  }
})

$Steps.Add([pscustomobject]@{
  Name = 'backend:json-smoke'
  Run = {
    Invoke-WithEnv @{ NOTEAPP_SMOKE_PORT = '8090' } {
      & $Python tests\e2e\sync_server_smoke.py
    }
  }
})

$Steps.Add([pscustomobject]@{
  Name = 'backend:sqlite-smoke'
  Run = {
    Invoke-WithEnv @{ NOTEAPP_SERVER_STORAGE = 'sqlite'; NOTEAPP_SMOKE_PORT = '8091' } {
      & $Python tests\e2e\sync_server_smoke.py
    }
  }
})

$Steps.Add([pscustomobject]@{
  Name = 'backend:production-smoke'
  Run = { & $Python tests\e2e\sync_server_production_smoke.py }
})

$Steps.Add([pscustomobject]@{
  Name = 'vault-core:unit'
  Run = {
    Invoke-WithEnv @{ PYTHONPATH = $VaultCorePath } {
      & $Python -m unittest `
        packages\vault-core\tests\test_sync_api.py `
        packages\vault-core\tests\test_sync_client.py `
        packages\vault-core\tests\test_sync_http.py `
        packages\vault-core\tests\test_storage.py
    }
  }
})

$Steps.Add([pscustomobject]@{
  Name = 'desktop-core:unit'
  Run = {
    Invoke-WithEnv @{ PYTHONPATH = $DesktopPythonPath } {
      & $Python -m unittest `
        clients\desktop\tests\test_change_detection.py `
        clients\desktop\tests\test_cli.py `
        clients\desktop\tests\test_crypto.py `
        clients\desktop\tests\test_runner.py `
        clients\desktop\tests\test_scheduler.py `
        clients\desktop\tests\test_service.py `
        clients\desktop\tests\test_sync_runtime.py `
        clients\desktop\tests\test_worker.py `
        clients\desktop\tests\test_workspace.py `
        packages\ai-core\tests\test_compiler.py
    }
  }
})

$Steps.Add([pscustomobject]@{
  Name = 'desktop:e2e-sync'
  Run = {
    Invoke-WithEnv @{ PYTHONPATH = $DesktopPythonPath } {
      & $Python tests\e2e\desktop_sync_smoke.py
    }
  }
})

$Steps.Add([pscustomobject]@{
  Name = 'frontend:lint'
  Run = { npm.cmd --prefix apps\frontend\noteapp-web run lint }
})

$Steps.Add([pscustomobject]@{
  Name = 'frontend:build'
  Run = { npm.cmd --prefix apps\frontend\noteapp-web run build }
})

$Steps.Add([pscustomobject]@{
  Name = 'frontend:entry-policy'
  Run = { npm.cmd --prefix apps\frontend\noteapp-web run smoke:entry-policy }
})

$Steps.Add([pscustomobject]@{
  Name = 'frontend:e2e-dist'
  Run = { & $Python tests\e2e\frontend_dist_smoke.py }
})

$Steps.Add([pscustomobject]@{
  Name = 'frontend:e2e-sync-snapshot'
  Run = { & $Python tests\e2e\frontend_sync_snapshot_smoke.py }
})

$Steps.Add([pscustomobject]@{
  Name = 'desktop:build-frontend'
  Run = { npm.cmd --prefix apps\desktop\noteapp-desktop run build:frontend }
})

if ($ReleaseArtifacts) {
  $Steps.Add([pscustomobject]@{
    Name = 'desktop:dist-win-signed'
    Run = { npm.cmd --prefix apps\desktop\noteapp-desktop run dist:win:signed }
  })
}

if ($List) {
  $Steps | ForEach-Object { $_.Name }
  exit 0
}

foreach ($step in $Steps) {
  Invoke-Step $step.Name $step.Run
}
