# Interoperability Fixtures

This directory contains sanitized JSONL traces and fixture material used by the SDK test suite.

- `ship/ppc_pairing_rejected.jsonl`: expected PPC behavior before the client certificate is trusted.
- `ship/spine_discovery_success.jsonl`: successful SHIP handshake followed by one SPINE discovery datagram.

The fixtures intentionally avoid private keys, live customer data, and vendor-private documentation.
