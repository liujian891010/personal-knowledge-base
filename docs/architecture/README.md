# Architecture

This directory records the stable architecture boundaries for the current V1
desktop, web, sync, and backend release path.

## Documents

| Document | Purpose |
| --- | --- |
| [system-overview.md](system-overview.md) | Product scope, component map, and primary sync flow |
| [runtime-topology.md](runtime-topology.md) | Local desktop, web bridge, backend, packaging, and operations topology |
| [module-boundaries.md](module-boundaries.md) | Ownership rules between frontend, desktop service, vault core, backend, protocol, and AI modules |

## Current Release Scope

v1.0.43 covers the desktop shell, web UI, local sync bridge, Python desktop
service, vault-core sync logic, ai-core helpers, protocol fixtures, and the
FastAPI sync server.

The mobile client directory is outside the v1.0.43 RC and public release scope.

## Maintenance Rules

1. Keep diagrams and flows consistent with executable commands in `docs/testing/`.
2. Do not document secrets, private service URLs, or internal credentials.
3. When a module boundary changes, update `module-boundaries.md` before changing
   cross-module call sites.
4. When a runtime port, environment variable, or package resource changes, update
   `runtime-topology.md` and the related app README in the same change.
