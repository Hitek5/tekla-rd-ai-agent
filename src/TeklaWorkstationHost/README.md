# TeklaWorkstationHost

Minimal Windows-side host for Tekla tool calls.

## Current state

This is a starter host with a `StubTeklaFacade`. It does not modify a Tekla model yet.

Implemented routes:

- `GET /health`
- `POST /tools/GetSelection`
- `POST /tools/QueryObjects`
- `POST /tools/ValidateModel`
- `POST /tools/DryRun`
- `POST /tools/CreateBeam`
- `POST /tools/CreateColumn`

Mutating routes require the `X-Agent-Approval` header even if a caller bypasses the central orchestrator.

## Next implementation step

Replace `StubTeklaFacade` with a Tekla Open API adapter:

- reference the installed Tekla Open API assemblies;
- create a `TeklaModelFacade` implementing `ITeklaFacade`;
- keep every tool small and typed;
- return Tekla GUIDs and validation warnings in `ToolResult`;
- keep local audit enabled.

