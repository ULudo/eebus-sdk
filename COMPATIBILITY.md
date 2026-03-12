# Compatibility Matrix

| Peer / Device | Discovery | TLS | SHIP | SPINE | Status |
| --- | --- | --- | --- | --- | --- |
| PPC SHIP endpoint (service instance redacted) | Yes | Yes | Yes | Yes | Verified on lab network with imported legacy identity; fresh identities require PPC-side trust enrollment |
| Lab SHIP / SPINE peer | Yes | Yes | Yes | Yes | Verified with discovery datagram |
| Local loopback self-test | Yes | Yes | Yes | Yes | Verified on this PC via `eebus selftest` |
