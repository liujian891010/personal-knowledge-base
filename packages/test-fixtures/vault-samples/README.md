# vault-samples

Use this directory only for sanitized vault directory samples that are shared by
multiple E2E or manual compatibility checks.

## Required Shape

Each sample should include:

1. A short README explaining the scenario.
2. Minimal note or attachment files.
3. A `.noteapp/` state set only when the scenario needs local metadata.
4. A command that validates or consumes the sample.

## Exclusions

Do not commit personal vault exports, raw AI cache data, provider credentials,
desktop key material, or generated release output.

Most tests should generate vaults dynamically under temp directories. Add a
sample here only when the fixture is hard to reconstruct or is needed for
cross-tool compatibility.
