# Compatibility Matrix

This matrix tracks only scenarios that were actually exercised, not theoretical support.

| Peer / Device | Discovery | TLS | SHIP | SPINE | Notes |
| --- | --- | --- | --- | --- | --- |
| Local loopback self-test | Yes | Yes | Yes | Yes | Verified on one PC via `eebus selftest` |
| Reference energy-management peer | Yes | Yes | Yes | Yes | Verified with discovery exchange |
| EV charging peer | Yes | Yes | Yes | Yes | Verified as paired HEMS peer, including heartbeats, remote reads, and accepted LPC write with readback confirmation |
| CLS gateway peer | Yes | Yes | Yes | Yes | Verified with a pre-commissioned identity; fresh identities still require trust commissioning |

## Interpretation

- `Yes` means the step was observed with the current SDK on a real or controlled peer.
- A positive `SPINE` result means at least one real SPINE datagram exchange was completed.
- The EV charging peer result includes an accepted `loadControlLimitListData` write and matching readback.
- Gateway commissioning remains an operational prerequisite, not an SDK limitation.
