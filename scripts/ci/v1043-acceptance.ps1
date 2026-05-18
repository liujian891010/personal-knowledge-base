param(
  [switch]$List,
  [switch]$ReleaseArtifacts,
  [switch]$Stability,
  [int]$StabilityIterations = 1000
)

$ErrorActionPreference = 'Stop'

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptRoot '..\..')
$Python = if ([string]::IsNullOrWhiteSpace($env:PYTHON)) { 'python' } else { $env:PYTHON }
$RunFromRepoRoot = (Test-Path 'scripts\ci\v1043-acceptance.ps1') -and (Test-Path 'apps\frontend\noteapp-web\package.json')

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

function Invoke-Native {
  param(
    [string]$FilePath,
    [string[]]$Arguments = @()
  )

  & $FilePath @Arguments
  if ($null -ne $global:LASTEXITCODE -and $global:LASTEXITCODE -ne 0) {
    throw "Command '$FilePath $($Arguments -join ' ')' failed with exit code $global:LASTEXITCODE"
  }
}

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Block
  )

  Write-Host "==> $Name"
  $global:LASTEXITCODE = 0
  if ($RunFromRepoRoot) {
    & $Block
  } else {
    Push-Location $RepoRoot
    try {
      & $Block
    } finally {
      Pop-Location
    }
  }
  if ($null -ne $global:LASTEXITCODE -and $global:LASTEXITCODE -ne 0) {
    throw "Step '$Name' failed with exit code $global:LASTEXITCODE"
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
  Run = {
    Invoke-Native $Python @(
      'scripts\ci\run_in_directory.py',
      'apps\frontend\noteapp-web',
      'node',
      'node_modules\typescript\bin\tsc',
      '--noEmit'
    )
  }
})

$Steps.Add([pscustomobject]@{
  Name = 'frontend:build'
  Run = {
    Invoke-Native $Python @(
      'scripts\ci\run_in_directory.py',
      'apps\frontend\noteapp-web',
      'node',
      'node_modules\vite\bin\vite.js',
      'build'
    )
  }
})

$Steps.Add([pscustomobject]@{
  Name = 'frontend:entry-policy'
  Run = {
    Invoke-Native $Python @(
      'scripts\ci\run_in_directory.py',
      'apps\frontend\noteapp-web',
      'node',
      'scripts\smoke-entry-policy.mjs'
    )
  }
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
  Run = {
    Invoke-Native $Python @(
      'scripts\ci\run_in_directory.py',
      'apps\desktop\noteapp-desktop',
      'node',
      'scripts\build-frontend.mjs'
    )
  }
})

if ($ReleaseArtifacts) {
  $Steps.Add([pscustomobject]@{
    Name = 'desktop:dist-win-signed'
    Run = {
      Invoke-Native $Python @('scripts\ci\run_in_directory.py', 'apps\desktop\noteapp-desktop', 'node', 'scripts\build-frontend.mjs')
      Invoke-Native $Python @('scripts\ci\run_in_directory.py', 'apps\desktop\noteapp-desktop', 'node', 'scripts\check-win-signing-env.mjs')
      Invoke-Native $Python @('scripts\ci\run_in_directory.py', 'apps\desktop\noteapp-desktop', 'node', 'scripts\package-win.mjs', '--win', 'nsis')
      Invoke-Native $Python @('scripts\ci\run_in_directory.py', 'apps\desktop\noteapp-desktop', 'node', 'scripts\verify-win-signature.mjs')
    }
  })
}

if ($Stability) {
  $Steps.Add([pscustomobject]@{
    Name = 'stability:desktop-sync'
    Run = {
      Invoke-WithEnv @{ PYTHONPATH = $DesktopPythonPath } {
        & $Python tests\e2e\desktop_sync_stability.py --iterations $StabilityIterations
      }
    }
  })
}

if ($List) {
  $Steps | ForEach-Object { $_.Name }
  exit 0
}

foreach ($step in $Steps) {
  Invoke-Step $step.Name $step.Run
}
