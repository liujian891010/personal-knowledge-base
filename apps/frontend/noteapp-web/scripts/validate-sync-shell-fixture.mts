import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  parseSyncShellSnapshot,
  summarizeSyncShellSnapshot,
} from '../src/syncShell.ts';

const fixturePath = resolve(process.argv[2] ?? 'fixtures/live-sync-shell.example.json');
const payload = JSON.parse(readFileSync(fixturePath, 'utf8'));
const snapshot = parseSyncShellSnapshot(payload);
const summary = summarizeSyncShellSnapshot(snapshot);

if (!summary.vaultId || !summary.deviceId || !summary.primaryActionLabel) {
  throw new Error(`invalid sync shell summary from ${fixturePath}`);
}

if (summary.cardCount !== snapshot.sync_center.cards.length) {
  throw new Error(`invalid sync shell card count from ${fixturePath}`);
}

if (snapshot.activity_feed.total_count < snapshot.activity_feed.records.length) {
  throw new Error(`invalid sync shell activity feed count from ${fixturePath}`);
}

console.log(
  JSON.stringify(
    {
      ok: true,
      fixture: fixturePath,
      level: summary.level,
      headline: summary.headline,
      cardCount: summary.cardCount,
      activityCount: summary.recentActivityCount,
      primaryActionLabel: summary.primaryActionLabel,
    },
    null,
    2,
  ),
);
