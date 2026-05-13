import { spawn } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const desktopRoot = path.resolve(__dirname, '..');
const cliPath = path.resolve(desktopRoot, 'node_modules', 'electron-builder', 'cli.js');
const extraArgs = process.argv.slice(2);

const child = spawn(process.execPath, [cliPath, ...extraArgs], {
  cwd: desktopRoot,
  stdio: 'inherit',
  windowsHide: true,
  env: process.env,
});

child.once('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
