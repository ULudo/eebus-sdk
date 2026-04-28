# EEBus SDK

Python SDK and command-line tools for EEBus SHIP/SPINE integrations, with a focus on HEMS, load-power control, discovery, identity handling, and interoperability testing.

The SDK provides:

- SHIP client and server connections with TLS identity and trust handling.
- SPINE discovery, read, notify, binding, and write flows used by common HEMS integrations.
- Load-power CLI workflows for LPC and LPP.
- A generic LP bridge that can receive load-power commands and forward them to another EEBus peer.
- Sanitized interoperability fixtures for regression tests.

## Installation

```bash
python3 -m pip install -e .
```

Python 3.11 or newer is required. The `zeroconf` runtime dependency is intentional: it is used for mDNS/DNS-SD discovery and local SHIP service advertisement.

## Quickstart

Use an existing EEBus identity file as `<IDENTITY_JSON>`. Keep identity files, private keys, peer certificates, and traces outside source control.

Discover SHIP services on the local interface:

```bash
eebus discover --interface-ip <INTERFACE_IP> --tls-check
```

Run the built-in protocol self-test:

```bash
eebus selftest --verify-tls
```

Replay a sanitized interoperability fixture:

```bash
eebus trace tests/interop_fixtures/ship/spine_discovery_success.jsonl
```

Send an LPC consumption limit:

```bash
eebus lpc send \
  --identity <IDENTITY_JSON> \
  --interface-ip <INTERFACE_IP> \
  --peer-trust-anchor <PEER_CERT_PEM> \
  --trusted-client-ski <PEER_SKI> \
  --watts <WATTS> \
  --duration-seconds <DURATION> \
  --trace-jsonl <TRACE_JSONL> \
  --exit-after-confirmation
```

Send an LPP production/feed-in limit:

```bash
eebus lpp send \
  --identity <IDENTITY_JSON> \
  --interface-ip <INTERFACE_IP> \
  --peer-trust-anchor <PEER_CERT_PEM> \
  --trusted-client-ski <PEER_SKI> \
  --watts <WATTS> \
  --duration-seconds <DURATION> \
  --trace-jsonl <TRACE_JSONL> \
  --exit-after-confirmation
```

For both commands, users pass positive watts. LPC treats `<WATTS>` as a consumption limit; LPP treats `<WATTS>` as a production or feed-in limit. Any protocol-specific sign convention is handled inside the SDK.

Bridge load-power commands to another EEBus peer:

```bash
eebus lp bridge \
  --identity <IDENTITY_JSON> \
  --interface-ip <INTERFACE_IP> \
  --peer-trust-anchor <PEER_CERT_PEM> \
  --trusted-client-ski <PEER_SKI> \
  --wallbox-identity <IDENTITY_JSON> \
  --wallbox-peer-trust-anchor <PEER_CERT_PEM> \
  --wallbox-trusted-client-ski <PEER_SKI> \
  --wallbox-ski <PEER_SKI> \
  --trace-jsonl <TRACE_JSONL> \
  --wallbox-trace-jsonl <TRACE_JSONL>
```

The bridge listens for inbound LPC or LPP load-power commands and forwards the matching command type to the configured downstream peer. If the upstream source peer is itself the SHIP server, add the outbound-source options `--source-host`, `--source-port`, `--source-server-name`, and `--source-peer-ski`; in that mode the SDK connects to the source peer and still hosts the wallbox-facing endpoint.

## Live-Device Interoperability Workflow

For a sanitized lab validation run:

1. Provision a dedicated test identity and use it as `<IDENTITY_JSON>`.
2. Import the peer certificate as `<PEER_CERT_PEM>` or configure the expected `<PEER_SKI>`.
3. Discover the peer on `<INTERFACE_IP>` and confirm the SKI before sending control commands.
4. Run either `eebus lpc send`, `eebus lpp send`, or `eebus lp bridge` with `<TRACE_JSONL>` enabled.
5. Verify that the session connects, the peer is trusted, the SPINE write is accepted, and the expected acknowledgement or readback is observed.
6. Sanitize traces before sharing them.

Do not commit real identity files, private keys, certificates, or unsanitized traces.

## Python API

```python
import asyncio

from eebus_sdk import HemsClient, IdentityStore, TrustStore, discover_ship_services


async def main() -> None:
    identity = IdentityStore.load("<IDENTITY_JSON>")
    trust = TrustStore.from_server_ski(
        "<PEER_SKI>",
        verify_tls=True,
        trust_anchors=("<PEER_CERT_PEM>",),
    )
    services = discover_ship_services("<INTERFACE_IP>", tls_check=True)
    service = next(item for item in services if item.ski == "<PEER_SKI>")

    client = await HemsClient.connect(
        service,
        identity,
        trust,
        interface_ip="<INTERFACE_IP>",
    )
    measurements = await client.read_remote_measurements()
    print(measurements)
    await client.close()


asyncio.run(main())
```

The intended public API is small:

- `IdentityStore` for local SHIP identity material.
- `TrustStore` for peer trust configuration.
- `discover_ship_services` and `ShipService` for discovery.
- `HemsClient` for outgoing SHIP/SPINE client workflows.
- `ShipServer` and `ShipServerConfig` for server-side HEMS or bridge workflows.

Private modules in `eebus_sdk` are implementation details and may change without notice.

## Compatibility

The test fixtures cover successful SHIP pairing, SPINE discovery, feature reads, subscriptions, load-power writes, and selected peer compatibility behavior. See `COMPATIBILITY.md` for the current compatibility matrix and fixture notes.

## Repository Layout

```text
eebus_sdk/          SDK package and CLI implementation
tests/              Unit tests and sanitized SHIP/SPINE JSONL fixtures
COMPATIBILITY.md    Compatibility and fixture notes
```

## Development

Run the test suite:

```bash
python3 -m unittest discover -s tests -v
```

Check for whitespace problems before committing:

```bash
git diff --check
```
