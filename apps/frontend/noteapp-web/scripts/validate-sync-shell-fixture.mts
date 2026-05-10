import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  parseSyncShellSnapshot,
  summarizeSyncShellSnapshot,
} from '../src/syncShell.ts';

const fixturePath = resolve(process.argv[2] ?? 'fixtures/live-sync-shell.example.json');
const payload = JSON.parse(readFileSync(fixturePath, 'utf8'));
const summary = summarizeSyncShellSnapshot(parseSyncShellSnapshot(payload));

if (!summary.vaultId || !summary.deviceId || !summary.primaryActionLabel) {
  throw new Error(`invalid sync shell summary from ${fixturePath}`);
}

console.log(
  JSON.stringify(
    {
      ok: true,
      fixture: fixturePath,
      level: summary.level,
      headline: summary.headline,
      primaryActionLabel: summary.primaryActionLabel,
    },
    null,
    2,
  ),
);
