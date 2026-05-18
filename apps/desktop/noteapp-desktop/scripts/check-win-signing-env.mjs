import process from 'node:process';

const certLink = process.env.CSC_LINK || process.env.WIN_CSC_LINK || '';
const certPassword = process.env.CSC_KEY_PASSWORD || process.env.WIN_CSC_KEY_PASSWORD || '';

if (!certLink || !certPassword) {
  console.error([
    'Windows signing is not configured.',
    'Set CSC_LINK and CSC_KEY_PASSWORD before running the signed Windows release build.',
    'WIN_CSC_LINK and WIN_CSC_KEY_PASSWORD are also accepted and forwarded to electron-builder.',
  ].join('\n'));
  process.exit(1);
}

console.log('Windows signing environment is configured.');
