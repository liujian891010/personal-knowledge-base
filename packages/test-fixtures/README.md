# test-fixtures

This package documents shared fixture policy for E2E, compatibility, and chaos
testing. It is intentionally separate from `packages/protocol/fixtures`, which
owns versioned protocol golden payloads.

## Fixture Categories

| Directory | Intended Use | Current Policy |
| --- | --- | --- |
| [vault-samples](vault-samples/README.md) | Sanitized vault directory samples | Check in only small deterministic vaults with no personal content |
| [protocol-golden](protocol-golden/README.md) | Cross-runtime protocol snapshots | Prefer `packages/protocol/fixtures` unless the payload spans multiple packages |
| [crash-cases](crash-cases/README.md) | Minimal recovery and failure fixtures | Check in only reduced cases that cannot be generated quickly in tests |

## Rules

1. Fixtures must be deterministic and small.
2. Fixtures must not contain secrets, private URLs, tokens, user vault content,
   signing credentials, or provider keys.
3. Generated runtime output belongs under ignored app fixture/output paths unless
   the file is promoted here with a documented owner and test.
4. Every checked-in fixture needs a test or smoke command listed in the owning
   directory README.
