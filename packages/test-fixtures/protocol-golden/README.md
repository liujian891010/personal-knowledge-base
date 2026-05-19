# protocol-golden

Use this directory for cross-package protocol snapshots only when the fixture
cannot live under `packages/protocol/fixtures`.

## Default Location

Protocol schemas and single-payload golden files belong in:

```text
packages/protocol/fixtures/
```

This directory is reserved for larger integration fixtures that combine backend,
desktop, frontend, and protocol state in one reproducible scenario.

## Required Metadata

Every fixture set added here must document:

1. Schema or API version.
2. Producer command.
3. Consumer test or smoke command.
4. Sanitization notes.
5. Expected compatibility behavior.
