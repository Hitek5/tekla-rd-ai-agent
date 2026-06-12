# Tekla workstation code

This folder contains the Windows-side starter code.

- `TeklaAgent.Contracts` contains DTOs shared between the orchestrator and the workstation host.
- `TeklaWorkstationHost` is a minimal local HTTP host. It currently uses a stub facade and must be wired to Tekla Open API on a Tekla workstation.

Production implementation notes:

- Keep the host bound to `127.0.0.1` unless an approved network design says otherwise.
- Keep mutating tools guarded by approval headers and local confirmation UI.
- Wrap Tekla Open API calls in small typed tools instead of executing arbitrary generated C#.
- Add Tekla references only on workstations where Tekla is installed.

