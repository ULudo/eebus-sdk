# Interoperability Fixtures

This directory contains sanitized JSONL traces and fixture material used by the SDK test suite.

- `ship/gateway_pairing_rejected.jsonl`: pairing-rejected gateway handshake path captured before the client certificate was trusted.
- `ship/spine_discovery_success.jsonl`: successful SHIP handshake followed by one SPINE discovery datagram.

The fixtures intentionally avoid private keys, live customer data, and vendor-private documentation.
