# local-settings

`.noteapp/settings.json` stores device-local preferences for one vault.

This file is not synchronized across devices. Cross-device preferences should use a separate contract later instead of reusing this local file.

## Contract

- Schema: `packages/protocol/schemas/local-settings.schema.json`
- Golden fixture: `packages/protocol/fixtures/local-settings/golden-local-settings.json`
- Current version: `schema_version = "v1"`

## Fields

- `appearance.theme`: one of `dark`, `light`, `system`
- `ai.local_model_status`: one of `not_configured`, `available`, `unavailable`, `disabled`, `error`
- `ai.embedding_status`: one of `not_configured`, `ready`, `indexing`, `disabled`, `error`

Sync connection fields such as base URL, bearer token, timeouts, and user agent are runtime configuration. They are exposed in the settings snapshot for inspection but are not persisted in `settings.json`.
