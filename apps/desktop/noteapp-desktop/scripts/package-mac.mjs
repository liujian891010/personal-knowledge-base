import { spawn } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const desktopRoot = path.resolve(__dirname, '..');
const cliPath = path.resolve(desktopRoot, 'node_modules', 'electron-builder', 'cli.js');
const extraArgs = process.argv.slice(2);
const builderArgs = extraArgs.length > 0 ? extraArgs : ['--mac', 'dmg', 'zip'];

if (process.platform !== 'darwin') {
  console.warn(
    [
      'macOS artifacts should be built on macOS.',
      'This script will still invoke electron-builder so configuration errors can be caught early,',
      'but dmg/sign/notarize output is not expected to succeed on this host.',
    ].join(' '),
  );
}

const child = spawn(process.execPath, [cliPath, ...builderArgs], {
  cwd: desktopRoot,
  stdio: 'inherit',
  windowsHide: true,
  env: {
    ...process.env,
    CSC_IDENTITY_AUTO_DISCOVERY: process.env.CSC_IDENTITY_AUTO_DISCOVERY ?? 'false',
  },
});

child.once('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
