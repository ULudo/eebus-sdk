"""Command line entry points for commissioning and EEBus interoperability work."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from typing import Any, AsyncIterator

from .advertisement import ShipServiceAdvertiser, ShipServiceAdvertisement
from .client import HemsClient
from .discovery import ShipService, detect_interface_ip, discover_ship_services
from .exceptions import EebusError, PairingRejectedError, ReplayError
from .identity import IdentityStore, normalize_ski
from .server import ShipServer, ShipServerConfig, ShipServerEvent
from .replay import load_trace, summarize_trace
from .selftest import run_loopback_selftest
from .ship import ShipConnectionConfig, ShipSession
from .spine import extract_commands, extract_header
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
    parser.add_argument(
        "--profile",
        choices=("default", "hems-reference", "cls-adapter"),
        default="default",
        help="select the local SPINE profile presented to the peer",
    )
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
    parser.add_argument(
        "--listen",
        action="store_true",
        help="keep reading incoming SPINE datagrams and print summaries until interrupted",
    )


def _add_common_serve_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--identity", required=True, help="path to identity.json")
    parser.add_argument("--interface-ip", help="IPv4 address to publish via mDNS")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4712)
    parser.add_argument("--path", default="/ship/")
    parser.add_argument("--ship-id", help="override the local SHIP ID served to the peer")
    parser.add_argument("--device-id", default="EEBUS-SDK-SERVER")
    parser.add_argument("--instance-name", help="full DNS-SD instance name; defaults to <device-id>._ship._tcp.local.")
    parser.add_argument("--server-name", help="advertised server host name; defaults to <hostname>.local.")
    parser.add_argument(
        "--peer-trust-anchor",
        action="append",
        default=[],
        help="PEM certificate used to validate an incoming client certificate; may be provided multiple times",
    )
    parser.add_argument(
        "--trusted-client-ski",
        action="append",
        default=[],
        help="allow only incoming peers with this certificate SKI; may be provided multiple times",
    )
    parser.add_argument("--trace-jsonl", help="write structured JSONL trace output")


def _add_load_power_send_args(
    parser: argparse.ArgumentParser,
    *,
    use_case: str,
    default_limit_id: int,
) -> None:
    _add_common_serve_args(parser)
    parser.add_argument("--watts", type=int, required=True, help="positive active power limit in W")
    parser.add_argument("--duration-seconds", type=int, required=True, help="limit duration in seconds")
    parser.add_argument(
        "--limit-id",
        type=int,
        default=default_limit_id,
        help=f"LoadControl limitId to write; defaults to {default_limit_id} for {use_case}",
    )
    parser.add_argument(
        "--confirmation-timeout",
        type=float,
        default=30.0,
        help="seconds to wait for the peer to acknowledge the write and readback",
    )
    parser.add_argument(
        "--exit-after-confirmation",
        action="store_true",
        help="stop after the peer confirms the requested limit instead of keeping the session open",
    )
    parser.set_defaults(lp_use_case=use_case)


def _add_load_power_bridge_args(parser: argparse.ArgumentParser) -> None:
    _add_common_serve_args(parser)
    parser.add_argument("--wallbox-ski", required=True, help="SKI of the connected wallbox peer")
    parser.add_argument(
        "--source-peer-ski",
        help="optional SKI of the inbound source peer; when omitted, forward load-power writes from any non-wallbox peer",
    )
    parser.add_argument(
        "--wallbox-identity",
        help="optional separate identity.json for the wallbox-facing listener",
    )
    parser.add_argument(
        "--wallbox-port",
        type=int,
        help="port for the wallbox-facing listener; defaults to 4712 unless the source listener already uses it",
    )
    parser.add_argument("--wallbox-bind-host", default="0.0.0.0")
    parser.add_argument("--wallbox-path", default="/ship/")
    parser.add_argument("--wallbox-ship-id", help="SHIP ID exposed to the wallbox peer")
    parser.add_argument("--wallbox-device-id", help="mDNS instance base name for the wallbox-facing listener")
    parser.add_argument("--wallbox-instance-name", help="full DNS-SD instance name for the wallbox-facing listener")
    parser.add_argument("--wallbox-server-name", help="advertised host name for the wallbox-facing listener")
    parser.add_argument(
        "--wallbox-peer-trust-anchor",
        action="append",
        default=[],
        help="PEM certificate used to validate the wallbox client certificate; may be provided multiple times",
    )
    parser.add_argument(
        "--wallbox-trusted-client-ski",
        action="append",
        default=[],
        help="allow only wallbox peers with this certificate SKI; may be provided multiple times",
    )
    parser.add_argument("--wallbox-trace-jsonl", help="write wallbox-side trace output to this JSONL file")


def _build_connection_config(args: argparse.Namespace) -> ShipConnectionConfig:
    config = ShipConnectionConfig(
        host=args.host,
        port=args.port,
        path=args.path,
        server_name=args.server_name,
        timeout=args.timeout,
        pairing_wait_seconds=args.pairing_wait_seconds,
    )
    if args.profile in {"hems-reference", "cls-adapter"}:
        config.access_handshake_mode = "standard"
        config.send_access_methods_response = False
        config.send_local_access_methods = True
    return config


def _should_request_remote_detailed_discovery(args: argparse.Namespace, client: HemsClient) -> bool:
    if args.profile == "cls-adapter":
        return False
    if args.profile == "hems-reference":
        return client._remote_device_address is not None
    return True


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
    identity_create.add_argument("--model", default="SDKClient")
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
    identity_import.add_argument("--model", default="ImportedIdentity")
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

    serve = subparsers.add_parser("serve", help="announce and host a local SHIP service for interoperability tests")
    _add_common_serve_args(serve)

    lpc = subparsers.add_parser("lpc", help="Limitation of Power Consumption commands")
    lpc_subparsers = lpc.add_subparsers(dest="lp_command", required=True)
    lpc_send = lpc_subparsers.add_parser(
        "send",
        help="host a local HEMS, wait for the paired peer to connect, and send one LPC limit",
    )
    _add_load_power_send_args(lpc_send, use_case="LPC", default_limit_id=0)

    lpp = subparsers.add_parser("lpp", help="Limitation of Power Production commands")
    lpp_subparsers = lpp.add_subparsers(dest="lp_command", required=True)
    lpp_send = lpp_subparsers.add_parser(
        "send",
        help="host a local HEMS, wait for the paired peer to connect, and send one LPP limit",
    )
    _add_load_power_send_args(lpp_send, use_case="LPP", default_limit_id=1)

    lp = subparsers.add_parser("lp", help="generic Load Power commands")
    lp_subparsers = lp.add_subparsers(dest="lp_command", required=True)
    lp_bridge = lp_subparsers.add_parser(
        "bridge",
        help="receive LPC/LPP commands from one inbound peer and forward them to a connected wallbox peer",
    )
    _add_load_power_bridge_args(lp_bridge)

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


def _command_names(datagram: Any) -> list[str]:
    names: list[str] = []
    for command in extract_commands(datagram):
        names.extend(key for key in command if isinstance(key, str))
    return names


def _matches_load_control(names: list[str]) -> bool:
    return any("loadcontrol" in name.lower() or name.lower() in {"lpp", "lpc"} for name in names)


def _load_power_use_case(limit_id: Any) -> str:
    if limit_id == 0:
        return "LPC"
    if limit_id == 1:
        return "LPP"
    return "LP"


def _format_datagram_summary(datagram: Any) -> str:
    header = extract_header(datagram)
    names = _command_names(datagram)
    source = header.get("addressSource", {})
    destination = header.get("addressDestination", {})
    return " ".join(
        [
            f"cmdClassifier={header.get('cmdClassifier', '-')}",
            f"msgCounter={header.get('msgCounter', '-')}",
            f"src={source.get('device', '-')}",
            f"dst={destination.get('device', '-')}",
            f"commands={','.join(names) if names else '-'}",
        ]
    )


def _format_duration(seconds: int) -> str:
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)
    parts = ["PT"]
    if hours:
        parts.append(f"{hours}H")
    if remaining_minutes:
        parts.append(f"{remaining_minutes}M")
    if remaining_seconds or len(parts) == 1:
        parts.append(f"{remaining_seconds}S")
    return "".join(parts)


def _parse_duration(duration: str) -> int:
    if not duration.startswith("PT"):
        raise EebusError(f"unsupported duration format: {duration}")
    remainder = duration[2:]
    total = 0
    number = ""
    for char in remainder:
        if char.isdigit():
            number += char
            continue
        if not number:
            raise EebusError(f"unsupported duration format: {duration}")
        value = int(number)
        number = ""
        if char == "H":
            total += value * 3600
        elif char == "M":
            total += value * 60
        elif char == "S":
            total += value
        else:
            raise EebusError(f"unsupported duration format: {duration}")
    if number:
        raise EebusError(f"unsupported duration format: {duration}")
    return total


async def _listen_for_spine_events(client: HemsClient) -> None:
    print("listening for incoming SPINE datagrams; press Ctrl-C to stop")
    async for event in client.session.events():
        if event.kind != "datagram":
            continue
        datagram = event.payload
        names = _command_names(datagram)
        await client.handle_incoming_datagram(datagram)
        summary = _format_datagram_summary(datagram)
        if _matches_load_control(names):
            print(f"LOADCONTROL {summary}")
            continue


async def _open_live_client(args: argparse.Namespace, trace: TraceLogger) -> HemsClient:
    identity = IdentityStore.load(args.identity)
    trust = TrustStore.from_server_ski(
        args.expected_server_ski,
        verify_tls=args.verify_tls,
        trust_anchors=tuple(args.trust_anchor),
    )
    session = await ShipSession.connect(
        _build_connection_config(args),
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
    return HemsClient(
        session=session,
        service=service,
        identity=identity,
        trust=trust,
        profile=args.profile,
    )


def _build_server_runtime(
    args: argparse.Namespace,
    *,
    spine_profile: str = "default",
    send_load_power_limit_watts: int | None = None,
    send_load_power_duration_seconds: int | None = None,
    send_load_power_limit_id: int = 0,
) -> tuple[str, ShipServiceAdvertiser, ShipServer]:
    trace = TraceLogger(args.trace_jsonl)
    identity = IdentityStore.load(args.identity)
    interface_ip = args.interface_ip or detect_interface_ip()
    announcer = ShipServiceAdvertiser(
        ShipServiceAdvertisement(
            interface_ip=interface_ip,
            port=args.port,
            ski=identity.ski,
            ship_id=args.ship_id or identity.ship_id,
            device_id=args.device_id,
            instance_name=args.instance_name,
            server_name=args.server_name or f"{socket.gethostname()}.local.",
            path=args.path,
        )
    )
    listener = ShipServer(
        ShipServerConfig(
            identity=identity,
            ship_id=args.ship_id or identity.ship_id,
            bind_host=args.bind_host,
            port=args.port,
            path=args.path,
            device_id=args.device_id,
            peer_trust_anchors=tuple(args.peer_trust_anchor),
            trusted_client_skis=tuple(args.trusted_client_ski),
            ship_handshake_mode="compatibility",
            spine_profile=spine_profile,
            send_load_power_limit_watts=send_load_power_limit_watts,
            send_load_power_duration_seconds=send_load_power_duration_seconds,
            send_load_power_limit_id=send_load_power_limit_id,
        ),
        trace_logger=trace,
    )
    return interface_ip, announcer, listener


def _default_wallbox_bridge_port(source_port: int) -> int:
    return 4713 if source_port == 4712 else 4712


def _build_wallbox_bridge_runtime(
    args: argparse.Namespace,
    *,
    interface_ip: str,
) -> tuple[ShipServiceAdvertiser, ShipServer, int]:
    if not args.wallbox_identity:
        raise EebusError("--wallbox-identity is required for a separate wallbox-facing listener")
    identity = IdentityStore.load(args.wallbox_identity)
    port = args.wallbox_port or _default_wallbox_bridge_port(args.port)
    ship_id = args.wallbox_ship_id or identity.ship_id
    device_id = args.wallbox_device_id or identity.device_id
    trace = TraceLogger(args.wallbox_trace_jsonl or args.trace_jsonl)
    announcer = ShipServiceAdvertiser(
        ShipServiceAdvertisement(
            interface_ip=interface_ip,
            port=port,
            ski=identity.ski,
            ship_id=ship_id,
            device_id=device_id,
            instance_name=args.wallbox_instance_name,
            server_name=args.wallbox_server_name or args.server_name or f"{socket.gethostname()}.local.",
            path=args.wallbox_path,
        )
    )
    listener = ShipServer(
        ShipServerConfig(
            identity=identity,
            ship_id=ship_id,
            bind_host=args.wallbox_bind_host,
            port=port,
            path=args.wallbox_path,
            device_id=device_id,
            peer_trust_anchors=tuple(args.wallbox_peer_trust_anchor),
            trusted_client_skis=tuple(args.wallbox_trusted_client_ski),
            ship_handshake_mode="compatibility",
        ),
        trace_logger=trace,
    )
    return announcer, listener, port


async def _serve_server(args: argparse.Namespace) -> int:
    interface_ip, announcer, listener = _build_server_runtime(args)
    await listener.start()
    await announcer.start()
    print(
        f"serving local SHIP endpoint on {args.bind_host}:{args.port}, published as {args.device_id} via {interface_ip}",
        flush=True,
    )
    try:
        async for event in listener.events():
            if event.kind == "connected":
                print(f"INBOUND connected peer_ski={event.payload.get('peer_ski') or '-'}", flush=True)
            elif event.kind == "ready":
                print(f"INBOUND ship_ready local_ship_id={event.payload.get('ship_id')}", flush=True)
            elif event.kind == "summary":
                names = [str(name) for name in (event.payload.get("commands") or [])]
                prefix = "LOADCONTROL" if _matches_load_control(names) else "INBOUND"
                print(f"{prefix} commands={','.join(names) if names else '-'}", flush=True)
            elif event.kind == "closed":
                print(f"INBOUND closed error={event.payload.get('error')}", flush=True)
            elif event.kind == "control" and "connectionKeepAlive" in event.payload:
                print("INBOUND keepalive", flush=True)
    finally:
        await announcer.stop()
        await listener.stop()


async def _run_lp_send(args: argparse.Namespace) -> int:
    if args.watts <= 0:
        raise EebusError("--watts must be a positive integer")
    if args.duration_seconds <= 0:
        raise EebusError("--duration-seconds must be a positive integer")
    if args.confirmation_timeout <= 0:
        raise EebusError("--confirmation-timeout must be positive")
    use_case = _load_power_use_case(args.limit_id)

    interface_ip, announcer, listener = _build_server_runtime(
        args,
        send_load_power_limit_watts=args.watts,
        send_load_power_duration_seconds=args.duration_seconds,
        send_load_power_limit_id=args.limit_id,
    )
    expected_duration = _format_duration(args.duration_seconds)
    confirmation_received = False
    write_accepted = False
    events = listener.events().__aiter__()
    await listener.start()
    await announcer.start()
    print(
        f"serving local SHIP endpoint on {args.bind_host}:{args.port}, published as {args.device_id} via {interface_ip}",
        flush=True,
    )
    print(
        f"waiting for the paired peer to connect so we can send {use_case} limit {args.watts} W for {args.duration_seconds} s",
        flush=True,
    )
    try:
        while True:
            timeout = None if confirmation_received and not args.exit_after_confirmation else args.confirmation_timeout
            try:
                event = await events.__anext__() if timeout is None else await asyncio.wait_for(events.__anext__(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise EebusError(
                    f"timed out after {args.confirmation_timeout:.1f}s while waiting for the wallbox to confirm the {use_case} command"
                ) from exc

            if event.kind == "connected":
                print(f"INBOUND connected peer_ski={event.payload.get('peer_ski') or '-'}", flush=True)
                continue
            if event.kind == "ready":
                print(f"INBOUND ship_ready local_ship_id={event.payload.get('ship_id')}", flush=True)
                continue
            if event.kind == "load_power_write_sent":
                event_use_case = _load_power_use_case(event.payload.get("limit_id", args.limit_id))
                print(
                    f"{event_use_case} write sent: limitId={event.payload.get('limit_id')} value={event.payload.get('watts')}W duration={event.payload.get('duration_seconds')}s",
                    flush=True,
                )
                continue
            if event.kind == "load_power_write_result":
                error_number = event.payload.get("error_number")
                description = event.payload.get("description")
                if error_number != 0:
                    detail = f" ({description})" if description else ""
                    raise EebusError(f"wallbox rejected {use_case} write with error {error_number}{detail}")
                write_accepted = True
                print(f"{use_case} write acknowledged by peer", flush=True)
                continue
            if event.kind == "load_power_readback":
                event_use_case = _load_power_use_case(event.payload.get("limit_id", args.limit_id))
                watts = event.payload.get("watts")
                is_active = event.payload.get("is_active")
                duration = event.payload.get("duration")
                print(
                    f"{event_use_case} readback: limitId={event.payload.get('limit_id')} active={is_active} value={watts}W duration={duration or '-'}",
                    flush=True,
                )
                if watts != args.watts or is_active is not True or duration != expected_duration:
                    raise EebusError(
                        f"wallbox readback did not match the requested {use_case} command"
                    )
                if not write_accepted:
                    raise EebusError(f"wallbox returned {use_case} readback before acknowledging the write")
                confirmation_received = True
                if args.exit_after_confirmation:
                    return 0
                print(f"{use_case} confirmed; keeping the SHIP session open. Press Ctrl-C to stop.", flush=True)
                continue
            if event.kind == "summary":
                names = [str(name) for name in (event.payload.get("commands") or [])]
                if _matches_load_control(names):
                    print(f"LOADCONTROL commands={','.join(names) if names else '-'}", flush=True)
                continue
            if event.kind == "closed":
                error = event.payload.get("error")
                if confirmation_received:
                    raise EebusError(f"wallbox closed the SHIP session after confirmation: {error}")
                raise EebusError(f"wallbox closed the SHIP session before {use_case} confirmation: {error}")
    finally:
        await announcer.stop()
        await listener.stop()


async def _next_bridge_event(side: str, events: AsyncIterator[ShipServerEvent]) -> tuple[str, ShipServerEvent]:
    return side, await events.__anext__()


async def _run_lp_bridge(args: argparse.Namespace) -> int:
    wallbox_ski = normalize_ski(args.wallbox_ski)
    if wallbox_ski is None:
        raise EebusError("--wallbox-ski is invalid")
    source_peer_ski = normalize_ski(args.source_peer_ski) if args.source_peer_ski else None
    if args.source_peer_ski and source_peer_ski is None:
        raise EebusError("--source-peer-ski is invalid")

    interface_ip, announcer, listener = _build_server_runtime(args, spine_profile="cls-load-power")
    wallbox_announcer: ShipServiceAdvertiser | None = None
    wallbox_listener: ShipServer = listener
    wallbox_port = args.port
    if args.wallbox_identity:
        wallbox_announcer, wallbox_listener, wallbox_port = _build_wallbox_bridge_runtime(
            args,
            interface_ip=interface_ip,
        )

    if wallbox_announcer is not None:
        await wallbox_listener.start()
        await wallbox_announcer.start()
    await listener.start()
    await announcer.start()

    event_tasks: dict[asyncio.Task[tuple[str, ShipServerEvent]], str] = {}
    source_events = listener.events().__aiter__()
    event_tasks[asyncio.create_task(_next_bridge_event("source", source_events))] = "source"
    if wallbox_listener is not listener:
        wallbox_events = wallbox_listener.events().__aiter__()
        event_tasks[asyncio.create_task(_next_bridge_event("wallbox", wallbox_events))] = "wallbox"
    print(
        f"serving local SHIP endpoint on {args.bind_host}:{args.port}, published as {args.device_id} via {interface_ip}",
        flush=True,
    )
    if wallbox_listener is not listener:
        print(
            f"serving wallbox SHIP endpoint on {args.wallbox_bind_host}:{wallbox_port}, published as {args.wallbox_device_id or IdentityStore.load(args.wallbox_identity).device_id} via {interface_ip}",
            flush=True,
        )
    print(
        f"bridging inbound LPC/LPP writes to wallbox peer {wallbox_ski}",
        flush=True,
    )
    if source_peer_ski:
        print(f"accepting load-power source peer {source_peer_ski}", flush=True)
    else:
        print("accepting load-power source peers from any non-wallbox inbound client", flush=True)

    pending_lp_forward: dict[str, Any] | None = None

    def _forward_request_from_event(event: ShipServerEvent) -> dict[str, Any] | None:
        peer_ski = normalize_ski(event.payload.get("peer_ski"))
        if peer_ski == wallbox_ski:
            return None
        if source_peer_ski is not None and peer_ski != source_peer_ski:
            return None
        watts = event.payload.get("watts")
        if not isinstance(watts, int):
            print("ignoring inbound load-power write without integer watt value", flush=True)
            return None
        limit_id = event.payload.get("limit_id", 0)
        if not isinstance(limit_id, int):
            limit_id = 0
        is_active = event.payload.get("is_active")
        if not isinstance(is_active, bool):
            is_active = True
        duration_seconds = None
        duration = event.payload.get("duration")
        if isinstance(duration, str):
            duration_seconds = _parse_duration(duration)
        return {
            "peer_ski": peer_ski,
            "watts": watts,
            "limit_id": limit_id,
            "is_active": is_active,
            "duration": duration,
            "duration_seconds": duration_seconds,
        }

    async def _forward_to_wallbox(request: dict[str, Any], *, deferred: bool = False) -> bool:
        use_case = _load_power_use_case(request["limit_id"])
        prefix = f"forwarding pending {use_case}" if deferred else f"forwarding {use_case}"
        print(
            f"{prefix} from {request.get('peer_ski') or '-'} to wallbox {wallbox_ski}: limitId={request['limit_id']} active={request['is_active']} value={request['watts']}W duration={request.get('duration') or '-'}",
            flush=True,
        )
        try:
            await wallbox_listener.send_load_power_limit_to_peer(
                wallbox_ski,
                watts=request["watts"],
                duration_seconds=request["duration_seconds"],
                limit_id=request["limit_id"],
                is_active=request["is_active"],
            )
        except Exception as exc:
            print(f"forward deferred: {exc}", flush=True)
            return False
        return True

    try:
        while True:
            done, _pending = await asyncio.wait(event_tasks, return_when=asyncio.FIRST_COMPLETED)
            task = next(iter(done))
            side, event = task.result()
            event_tasks.pop(task, None)
            stream = source_events if side == "source" else wallbox_events
            event_tasks[asyncio.create_task(_next_bridge_event(side, stream))] = side
            prefix = "SOURCE" if side == "source" else "WALLBOX"
            if event.kind == "connected":
                print(f"{prefix} connected peer_ski={event.payload.get('peer_ski') or '-'}", flush=True)
                continue
            if event.kind == "ready":
                print(
                    f"{prefix} ship_ready peer_ski={event.payload.get('peer_ski') or '-'} remote_ship_id={event.payload.get('remote_ship_id') or '-'}",
                    flush=True,
                )
                if side == "wallbox" and pending_lp_forward is not None:
                    if await _forward_to_wallbox(pending_lp_forward, deferred=True):
                        pending_lp_forward = None
                continue
            if event.kind == "closed":
                print(
                    f"{prefix} closed peer_ski={event.payload.get('peer_ski') or '-'} error={event.payload.get('error')}",
                    flush=True,
                )
                continue
            if event.kind == "summary":
                names = [str(name) for name in (event.payload.get("commands") or [])]
                if _matches_load_control(names):
                    print(f"{prefix} LOADCONTROL commands={','.join(names) if names else '-'}", flush=True)
                if side == "wallbox" and pending_lp_forward is not None:
                    if await _forward_to_wallbox(pending_lp_forward, deferred=True):
                        pending_lp_forward = None
                continue
            if event.kind == "inbound_load_power_write":
                if side != "source" and wallbox_listener is not listener:
                    continue
                request = _forward_request_from_event(event)
                if request is None:
                    continue
                pending_lp_forward = request
                if await _forward_to_wallbox(request):
                    pending_lp_forward = None
                continue
            if event.kind == "load_power_write_sent":
                if normalize_ski(event.payload.get("peer_ski")) != wallbox_ski:
                    continue
                event_use_case = _load_power_use_case(event.payload.get("limit_id"))
                print(
                    f"WALLBOX {event_use_case} write sent: limitId={event.payload.get('limit_id')} active={event.payload.get('is_active')} value={event.payload.get('watts')}W duration={event.payload.get('duration') or '-'}",
                    flush=True,
                )
                continue
            if event.kind == "load_power_write_result":
                if normalize_ski(event.payload.get("peer_ski")) != wallbox_ski:
                    continue
                error_number = event.payload.get("error_number")
                description = event.payload.get("description")
                if error_number != 0:
                    detail = f" ({description})" if description else ""
                    print(f"WALLBOX LP write rejected: error={error_number}{detail}", flush=True)
                    continue
                print("WALLBOX LP write acknowledged", flush=True)
                continue
            if event.kind == "load_power_readback":
                if normalize_ski(event.payload.get("peer_ski")) != wallbox_ski:
                    continue
                event_use_case = _load_power_use_case(event.payload.get("limit_id"))
                print(
                    f"WALLBOX {event_use_case} readback: limitId={event.payload.get('limit_id')} active={event.payload.get('is_active')} value={event.payload.get('watts')}W duration={event.payload.get('duration') or '-'}",
                    flush=True,
                )
                continue
    finally:
        for task in event_tasks:
            task.cancel()
        await announcer.stop()
        await listener.stop()
        if wallbox_announcer is not None:
            await wallbox_announcer.stop()
            await wallbox_listener.stop()


async def _run_connect(args: argparse.Namespace) -> int:
    trace = TraceLogger(args.trace_jsonl)
    client = await _open_live_client(args, trace)
    session = client.session
    try:
        print(f"SHIP handshake complete with remote SHIP ID: {session.remote_ship_id}")
        if args.bootstrap_spine and not args.read_measurements:
            if args.profile in {"hems-reference", "cls-adapter"}:
                await client.bootstrap_spine(timeout=min(args.timeout, 1.5))
            discovery: list[dict[str, Any]] = []
            requested_remote_discovery = _should_request_remote_detailed_discovery(args, client)
            if requested_remote_discovery:
                try:
                    discovery = await client.request_remote_detailed_discovery(timeout=args.timeout)
                except asyncio.TimeoutError:
                    discovery = []
            if discovery:
                _print_json({"remote_detailed_discovery": discovery[-1]})
            elif requested_remote_discovery:
                print("no remote detailed discovery payload received", file=sys.stderr)
        if args.read_measurements:
            _print_json(await client.read_remote_measurements(timeout=max(args.timeout, 6.0)))
            return 0
        if args.listen:
            await _listen_for_spine_events(client)
            return 0
        print("connection open; press Ctrl-C to stop")
        while True:
            await asyncio.sleep(1)
    finally:
        await session.close()


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

        if args.command == "serve":
            return asyncio.run(_serve_server(args))

        if args.command in {"lpc", "lpp"} and args.lp_command == "send":
            return asyncio.run(_run_lp_send(args))

        if args.command == "lp" and args.lp_command == "bridge":
            return asyncio.run(_run_lp_bridge(args))
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
