import { spawn } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const frontendRoot = path.resolve(__dirname, '..', '..', '..', 'frontend', 'noteapp-web');
const command = process.execPath;
const args = [path.resolve(frontendRoot, 'node_modules', 'vite', 'bin', 'vite.js'), 'build'];

const child = spawn(command, args, {
  cwd: frontendRoot,
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
