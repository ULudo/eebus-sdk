# EEBus SDK

`eebus-sdk` is a Python-first SHIP / EEBus / SPINE toolkit for HEMS vendors, lab engineers, and SMGW integrators.

This repository contains three public deliverables:

- `eebus_sdk`: importable Python package
- `eebus`: CLI for discovery, identity management, live sessions, and trace replay
- `interop_fixtures`: sanitized JSONL traces used for regression tests and interoperability debugging

The project is currently `alpha`. The transport and core SHIP path are implemented and tested, but PPC trust commissioning and broader profile coverage are still being expanded.

## Current capabilities

- mDNS / DNS-SD discovery of `_ship._tcp.local` services
- SHIP identity generation with persistent client certificates
- explicit trust configuration with SKI pinning and optional CA anchors
- asyncio-based TLS + WebSocket + SHIP session handling
- SPINE datagram send/receive helpers
- recorded JSONL tracing and replay summaries
- CLI workflows for:
  - `eebus discover`
  - `eebus identity create`
  - `eebus identity import`
  - `eebus trust show`
- `eebus connect`
- `eebus trace`
- `eebus replay`
- `eebus selftest`

## PPC status

The SDK has already proven the following on the lab network:

- PPC SHIP endpoint discovery via mDNS
- mutual TLS with both generated and imported client certificates
- WebSocket upgrade to `/ship/`
- full SHIP handshake
- reception of a real PPC SPINE discovery datagram when using an imported legacy client identity

There are now two verified PPC paths:

- New locally generated identities still hit the PPC trust gate. In that case PPC responds with `connectionHello phase=pending` and closes with `4452 Node rejected by application.` The SDK captures and replays that behavior through fixtures and tests.
- An imported legacy client identity from a previously paired project is accepted by PPC and reaches SPINE exchange. For that identity, the exact SHIP ID matters as well as the certificate; sending the wrong local `accessMethods.id` causes PPC to close with `4450 SHIP id mismatch`.

## Verified hardware tests

- `SPiNE EnergyLink One` installed in the lab: verified mutual TLS, WebSocket upgrade, full SHIP handshake, and reception of a real SPINE discovery datagram.
- `PPC CLS / SMGW endpoint` installed in the lab: verified mDNS discovery, mutual TLS, full SHIP handshake, and reception of a real `nodeManagementDetailedDiscoveryData` read request when using an imported legacy identity. Fresh identities still remain blocked by PPC-side trust enrollment and currently end with `4452 Node rejected by application.`

## Quickstart

Create an identity:

```bash
python3 -m eebus_sdk.cli identity create --out-dir .state/example-identity --device-id HEMS-EXAMPLE
```

Import an already-paired credential:

```bash
python3 -m eebus_sdk.cli identity import \
  --out-dir .state/imported-identity \
  --cert /path/to/client_ecdsa.crt \
  --key /path/to/client_ecdsa.key \
  --ship-id Demo-HEMS-123456789 \
  --copy-files
```

Discover SHIP peers:

```bash
python3 -m eebus_sdk.cli discover --interface-ip 192.0.2.2 --tls-check
```

Connect to a SHIP peer:

```bash
python3 -m eebus_sdk.cli connect \
  --identity .state/example-identity/identity.json \
  --host 192.0.2.10 \
  --port 23292 \
  --path /ship/ \
  --server-name ship-peer.example.local \
  --expected-server-ski 0123456789abcdef0123456789abcdef01234567 \
  --trace-jsonl .state/example-identity/ship-session.jsonl
```

Summarize a recorded trace:

```bash
python3 -m eebus_sdk.cli trace interop_fixtures/ship/spine_discovery_success.jsonl
```

Validate a fixture in CI:

```bash
python3 -m eebus_sdk.cli replay interop_fixtures/ship/ppc_pairing_rejected.jsonl --expect pairing-rejected
```

Run a full local loopback self-test with both transport and application roles on the same PC:

```bash
python3 -m eebus_sdk.cli selftest --verify-tls
```

## Python API

```python
import asyncio

from eebus_sdk import HemsClient, IdentityStore, TrustStore, discover_ship_services


async def main() -> None:
    identity = IdentityStore.load(".state/example-identity/identity.json")
    services = discover_ship_services("192.0.2.2")
    peer = next(service for service in services if service.service_name.endswith("._ship._tcp.local"))
    trust = TrustStore.from_server_ski(peer.ski, verify_tls=False)

    client = await HemsClient.connect(peer, identity, trust, interface_ip="192.0.2.2")
    try:
        discovery = await client.discover_nodes(timeout=5.0)
        print(discovery)
    finally:
        await client.close()


asyncio.run(main())
```

## Development

Run the unit test suite:

```bash
python3 -m unittest discover -s tests -v
```

Key documents:

- [interop_fixtures/README.md](interop_fixtures/README.md)
- [COMPATIBILITY.md](COMPATIBILITY.md)

## Scope and claims

- This project does not imply BSI or EEBus certification by itself.
- The repository documents behavior in original wording and links to official sources instead of republishing spec text.
- The alpha target is a usable SDK and debugging toolchain for PPC SMGW and at least one non-PPC SHIP/SPINE peer.
