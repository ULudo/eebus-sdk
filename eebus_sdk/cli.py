"""Command line entry points for commissioning and debugging EEBus sessions."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .client import HemsClient
from .discovery import ShipService, detect_interface_ip, discover_ship_services
from .exceptions import EebusError, PairingRejectedError, ReplayError
from .identity import IdentityStore
from .replay import load_trace, summarize_trace
from .selftest import run_loopback_selftest
from .ship import ShipConnectionConfig, ShipSession
from .trace import TraceLogger
from .trust import TrustStore


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _add_common_connect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--identity", required=True, help="path to identity.json")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--path", default="/ship/")
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--expected-server-ski")
    parser.add_argument("--pairing-wait-seconds", type=int, default=60)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--trace-jsonl", help="write structured JSONL trace output")
    parser.add_argument("--trust-anchor", action="append", default=[], help="PEM trust anchor for TLS verification")
    parser.add_argument("--verify-tls", action="store_true", help="enable standard TLS verification")
    parser.add_argument(
        "--bootstrap-spine",
        action="store_true",
        help="reply to remote discovery requests and request remote detailed discovery data",
    )
    parser.add_argument(
        "--read-measurements",
        action="store_true",
        help="bootstrap SPINE and attempt to read remote measurement descriptions and values",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eebus", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="discover SHIP services via mDNS")
    discover.add_argument("--interface-ip", help="IPv4 address of the interface to use")
    discover.add_argument("--timeout", type=float, default=3.0)
    discover.add_argument("--tls-check", action="store_true")
    discover.add_argument("--json", action="store_true")

    identity = subparsers.add_parser("identity", help="identity operations")
    identity_subparsers = identity.add_subparsers(dest="identity_command", required=True)
    identity_create = identity_subparsers.add_parser("create", help="create a client identity")
    identity_create.add_argument("--out-dir", required=True)
    identity_create.add_argument("--device-id")
    identity_create.add_argument("--ship-id")
    identity_create.add_argument("--country", default="DE")
    identity_create.add_argument("--brand", default="HEMS")
    identity_create.add_argument("--model", default="LabClient")
    identity_create.add_argument("--type", default="EnergyManagementSystem", dest="device_type")
    identity_create.add_argument("--serial-number", type=int, default=1)
    identity_create.add_argument("--overwrite", action="store_true")
    identity_import = identity_subparsers.add_parser("import", help="import an existing client identity")
    identity_import.add_argument("--out-dir", required=True)
    identity_import.add_argument("--cert", required=True, help="path to an existing client certificate")
    identity_import.add_argument("--key", required=True, help="path to the matching private key")
    identity_import.add_argument("--ship-id", required=True)
    identity_import.add_argument("--device-id")
    identity_import.add_argument("--common-name")
    identity_import.add_argument("--ski", help="override SKI instead of extracting it from the certificate")
    identity_import.add_argument("--brand", default="HEMS")
    identity_import.add_argument("--model", default="ImportedClient")
    identity_import.add_argument("--type", default="EnergyManagementSystem", dest="device_type")
    identity_import.add_argument("--copy-files", action="store_true", help="copy cert and key into the identity directory")
    identity_import.add_argument("--overwrite", action="store_true")

    trust = subparsers.add_parser("trust", help="inspect trust configuration")
    trust_subparsers = trust.add_subparsers(dest="trust_command", required=True)
    trust_show = trust_subparsers.add_parser("show", help="show effective trust settings")
    trust_show.add_argument("--identity")
    trust_show.add_argument("--expected-server-ski")
    trust_show.add_argument("--trust-anchor", action="append", default=[])
    trust_show.add_argument("--verify-tls", action="store_true")

    connect = subparsers.add_parser("connect", help="open a live SHIP session")
    _add_common_connect_args(connect)
    connect.add_argument("--send-datagram-json", help="send one SPINE datagram after handshake")
    connect.add_argument("--read-one-datagram", action="store_true")

    trace = subparsers.add_parser("trace", help="summarize or print a recorded JSONL trace")
    trace.add_argument("trace_file")
    trace.add_argument("--events", action="store_true", help="print normalized event objects instead of a summary")

    replay = subparsers.add_parser("replay", help="validate a recorded JSONL trace")
    replay.add_argument("trace_file")
    replay.add_argument("--expect", choices=("success", "pairing-rejected", "spine-data"))

    selftest = subparsers.add_parser("selftest", help="run a local loopback SHIP/SPINE self-test")
    selftest.add_argument("--work-dir", help="persist generated loopback identities under this directory")
    selftest.add_argument("--trace-jsonl", help="write the client-side trace to this JSONL file")
    selftest.add_argument("--verify-tls", action="store_true", help="verify the local server certificate via trust anchor")
    selftest.add_argument("--json", action="store_true")

    return parser


def _render_services(services: list[dict]) -> str:
    lines: list[str] = []
    for service in services:
        lines.append(service["service_name"])
        lines.append(f"  target: {service.get('target') or '-'}")
        lines.append(f"  port: {service.get('port') or '-'}")
        lines.append(f"  path: {service.get('path') or '/ship/'}")
        lines.append(f"  ship_id: {service.get('ship_id') or '-'}")
        lines.append(f"  ski: {service.get('ski') or '-'}")
        lines.append(f"  ipv4: {', '.join(service['addresses']['ipv4']) or '-'}")
        lines.append(f"  ipv6: {', '.join(service['addresses']['ipv6']) or '-'}")
        tls_probe = service.get("tls_probe")
        if tls_probe:
            lines.append(f"  tls.peer_ski: {tls_probe.get('cert_ski') or '-'}")
            lines.append(f"  tls.client_cert_requested: {tls_probe.get('client_cert_requested')}")
            lines.append(f"  tls.txt_ski_matches_cert_ski: {tls_probe.get('txt_ski_matches_cert_ski')}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def _run_connect(args: argparse.Namespace) -> int:
    identity = IdentityStore.load(args.identity)
    trust = TrustStore.from_server_ski(
        args.expected_server_ski,
        verify_tls=args.verify_tls,
        trust_anchors=tuple(args.trust_anchor),
    )
    trace = TraceLogger(args.trace_jsonl)
    session = await ShipSession.connect(
        ShipConnectionConfig(
            host=args.host,
            port=args.port,
            path=args.path,
            server_name=args.server_name,
            timeout=args.timeout,
            pairing_wait_seconds=args.pairing_wait_seconds,
        ),
        identity,
        trust,
        trace_logger=trace,
    )
    service = ShipService(
        service_name=args.server_name,
        target=args.server_name,
        port=args.port,
        path=args.path,
        ski=args.expected_server_ski,
        addresses={"ipv4": [args.host], "ipv6": []},
    )
    client = HemsClient(session=session, service=service, identity=identity, trust=trust)
    try:
        print(f"SHIP handshake complete with remote SHIP ID: {session.remote_ship_id}")
        if args.bootstrap_spine and not args.read_measurements:
            discovery = await client.request_remote_detailed_discovery(timeout=args.timeout)
            if discovery:
                _print_json({"remote_detailed_discovery": discovery[-1]})
            else:
                print("no remote detailed discovery payload received", file=sys.stderr)
        if args.read_measurements:
            _print_json(await client.read_remote_measurements(timeout=max(args.timeout, 6.0)))
            return 0
        if args.send_datagram_json:
            payload = json.loads(Path(args.send_datagram_json).read_text(encoding="utf-8"))
            await session.send_spine(payload)
            print(f"sent datagram from {args.send_datagram_json}")
        if args.read_one_datagram:
            datagram = await session.receive_datagram(timeout=args.timeout)
            _print_json({"header": datagram.header, "payload": datagram.payload})
        elif not args.send_datagram_json:
            print("connection open; press Ctrl-C to stop")
            while True:
                await asyncio.sleep(1)
    finally:
        await session.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "discover":
            interface_ip = args.interface_ip or detect_interface_ip()
            services = discover_ship_services(interface_ip, timeout=args.timeout, tls_check=args.tls_check)
            payload = {
                "interface_ip": interface_ip,
                "services": [service.as_dict() for service in services],
            }
            if args.json:
                _print_json(payload)
            else:
                print(f"interface_ip: {interface_ip}")
                print(_render_services(payload["services"]))
            return 0 if services else 1

        if args.command == "identity" and args.identity_command == "create":
            identity = IdentityStore.create(
                args.out_dir,
                device_id=args.device_id,
                ship_id=args.ship_id,
                country=args.country,
                brand=args.brand,
                model=args.model,
                device_type=args.device_type,
                serial_number=args.serial_number,
                overwrite=args.overwrite,
            )
            _print_json(identity.as_dict())
            print()
            print("Enroll this SKI on the SMGW / CLS side before attempting a full SHIP handshake:")
            print(identity.ski)
            return 0

        if args.command == "identity" and args.identity_command == "import":
            identity = IdentityStore.import_existing(
                args.out_dir,
                cert_path=args.cert,
                key_path=args.key,
                ship_id=args.ship_id,
                device_id=args.device_id,
                common_name=args.common_name,
                ski=args.ski,
                brand=args.brand,
                model=args.model,
                device_type=args.device_type,
                copy_files=args.copy_files,
                overwrite=args.overwrite,
            )
            _print_json(identity.as_dict())
            return 0

        if args.command == "trust" and args.trust_command == "show":
            identity = IdentityStore.load(args.identity) if args.identity else None
            trust = TrustStore.from_server_ski(
                args.expected_server_ski,
                verify_tls=args.verify_tls,
                trust_anchors=tuple(args.trust_anchor),
            )
            payload = {"trust": trust.describe()}
            if identity is not None:
                payload["identity"] = identity.as_dict()
            _print_json(payload)
            return 0

        if args.command == "connect":
            return asyncio.run(_run_connect(args))

        if args.command == "trace":
            events = load_trace(args.trace_file)
            if args.events:
                _print_json(events)
            else:
                _print_json(summarize_trace(events))
            return 0

        if args.command == "replay":
            summary = summarize_trace(load_trace(args.trace_file))
            _print_json(summary)
            if args.expect == "success" and not summary["handshake_complete"]:
                raise ReplayError("trace does not reach a completed SHIP handshake")
            if args.expect == "pairing-rejected" and not summary["pairing_rejected"]:
                raise ReplayError("trace does not contain a pairing rejection")
            if args.expect == "spine-data" and summary["spine_datagrams"] < 1:
                raise ReplayError("trace does not contain any SPINE datagrams")
            return 0

        if args.command == "selftest":
            result = asyncio.run(
                run_loopback_selftest(
                    work_dir=args.work_dir,
                    trace_path=args.trace_jsonl,
                    verify_tls=args.verify_tls,
                )
            )
            if args.json:
                _print_json(result.as_dict())
            else:
                _print_json(result.as_dict())
            return 0
    except PairingRejectedError as exc:
        print(f"SHIP pairing rejected: {exc}", file=sys.stderr)
        return 2
    except EebusError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    parser.error("unhandled command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
