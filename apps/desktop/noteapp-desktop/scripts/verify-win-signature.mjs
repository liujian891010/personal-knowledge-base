import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const desktopRoot = path.resolve(__dirname, '..');
const releaseRoot = path.resolve(desktopRoot, 'release');
const explicitTargets = process.argv.slice(2).map((target) => path.resolve(desktopRoot, target));

function releaseExecutables() {
  if (!existsSync(releaseRoot)) {
    return [];
  }
  return readdirSync(releaseRoot, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.exe$/i.test(entry.name))
    .map((entry) => path.join(releaseRoot, entry.name));
}

const targets = explicitTargets.length > 0 ? explicitTargets : releaseExecutables();
if (targets.length === 0) {
  console.error('No Windows installer executable was found. Build release/*.exe first or pass explicit paths.');
  process.exit(1);
}

const missingTargets = targets.filter((target) => !existsSync(target));
if (missingTargets.length > 0) {
  console.error(`Signature verification target was not found: ${missingTargets.join(', ')}`);
  process.exit(1);
}

const powershell = process.env.SystemRoot
  ? path.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
  : 'powershell.exe';
const quotedTargets = targets.map((target) => `'${target.replace(/'/g, "''")}'`).join(', ');
const command = `
$ErrorActionPreference = 'Stop'
$targets = @(${quotedTargets})
$tab = [char]9
$failed = $false
foreach ($target in $targets) {
  $signature = Get-AuthenticodeSignature -LiteralPath $target
  $subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { '<none>' }
  Write-Output ($signature.Status.ToString() + $tab + $subject + $tab + $target)
  if ($signature.Status -ne 'Valid') {
    $failed = $true
  }
}
if ($failed) {
  exit 2
}
`;

const result = spawnSync(
  powershell,
  ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
  {
    cwd: desktopRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  },
);

if (result.stdout) {
  process.stdout.write(result.stdout);
}
if (result.stderr) {
  process.stderr.write(result.stderr);
}
if (result.error) {
  console.error(`Failed to run PowerShell signature verification: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
