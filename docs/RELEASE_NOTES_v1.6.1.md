# HBlink4 Release Notes — v1.6.1

Release date: 2025-11-01

## Important compatibility note

This release (v1.6.1) continues to use the Twisted-based server implementation. Talker Alias (TA) support — the DMRA packet type used by MMDVMHost — is not present in this release and therefore talker alias text/ACK behavior will not work with v1.6.1.

If you need Talker Alias support today, it is implemented on the `feature/asyncio-migration` branch (an asyncio-based migration). That branch is running in production tests and will be merged back into `main` after additional validation.

Planned next steps:
- Merge `feature/asyncio-migration` → `main` once extended validation is complete
- Release a new mainline version (v1.7.0 or similar) that includes DMRA (Talker Alias) support

Thanks — the migration team
