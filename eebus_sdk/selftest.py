"""Local loopback self-test for SHIP transport and SPINE application roles."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import ssl
import struct
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .identity import IdentityMaterial, IdentityStore, extract_ski_from_peer_cert
from .json_codec import from_eebus_json_bytes, to_eebus_json_bytes
from .ship import SHIP_MSG_CONTROL, SHIP_MSG_DATA, SHIP_MSG_INIT, ShipConnectionConfig, ShipSession
from .spine import SpineDatagram, extract_discovery_payloads
from .trace import TraceLogger
from .trust import TrustStore
from .websocket import WebSocketFrame, encode_frame


@dataclass(slots=True)
class LoopbackResult:
    server_port: int
    client_ship_id: str
    server_ship_id: str
    server_ski: str
    peer_client_ski: str
    received_discovery_payloads: int
    trace_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "server_port": self.server_port,
            "client_ship_id": self.client_ship_id,
            "server_ship_id": self.server_ship_id,
            "server_ski": self.server_ski,
            "peer_client_ski": self.peer_client_ski,
            "received_discovery_payloads": self.received_discovery_payloads,
            "trace_path": self.trace_path,
        }


class _ServerWebSocketConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    async def handshake(self) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = await self.reader.read(4096)
            if not chunk:
                raise ConnectionError("websocket request ended before headers completed")
            request += chunk
        headers = request.decode("utf-8", "replace").split("\r\n")
        request_line = headers[0]
        if not request_line.startswith("GET "):
            raise ConnectionError(f"unexpected websocket request line: {request_line!r}")

        sec_key = None
        subprotocol = None
        for line in headers[1:]:
            lower = line.lower()
            if lower.startswith("sec-websocket-key:"):
                sec_key = line.split(":", 1)[1].strip()
            elif lower.startswith("sec-websocket-protocol:"):
                subprotocol = line.split(":", 1)[1].strip()
        if sec_key is None:
            raise ConnectionError("missing Sec-WebSocket-Key")
        if subprotocol != "ship":
            raise ConnectionError(f"unexpected WebSocket subprotocol {subprotocol!r}")

        accept = base64.b64encode(
            hashlib.sha1((sec_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "Sec-WebSocket-Protocol: ship\r\n"
            "\r\n"
        )
        self.writer.write(response.encode("ascii"))
        await self.writer.drain()

    async def receive_frame(self) -> WebSocketFrame:
        fragments: list[bytes] = []
        fragment_opcode: int | None = None

        while True:
            header = await self.reader.readexactly(2)
            byte1, byte2 = header
            fin = bool(byte1 & 0x80)
            opcode = byte1 & 0x0F
            masked = bool(byte2 >> 7)
            length = byte2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            mask_key = await self.reader.readexactly(4) if masked else b""
            payload = await self.reader.readexactly(length)
            if masked:
                payload = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))

            if opcode == 0x9:
                await self.send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x0:
                if fragment_opcode is None:
                    raise ConnectionError("unexpected continuation frame")
                fragments.append(payload)
                if fin:
                    return WebSocketFrame(opcode=fragment_opcode, payload=b"".join(fragments), fin=True)
                continue
            if opcode in (0x1, 0x2) and not fin:
                fragment_opcode = opcode
                fragments = [payload]
                continue
            return WebSocketFrame(opcode=opcode, payload=payload, fin=fin)

    async def send_frame(self, opcode: int, payload: bytes) -> None:
        self.writer.write(encode_frame(opcode, payload, mask=False))
        await self.writer.drain()

    async def send_binary(self, payload: bytes) -> None:
        await self.send_frame(0x2, payload)

    async def close(self) -> None:
        try:
            await self.send_frame(0x8, struct.pack("!H", 1000))
        except Exception:
            pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass

    def peer_certificate_der(self) -> bytes:
        ssl_object = self.writer.get_extra_info("ssl_object")
        if ssl_object is None:
            raise RuntimeError("no SSL object on server connection")
        cert = ssl_object.getpeercert(binary_form=True)
        if cert is None:
            raise RuntimeError("client certificate not available")
        return cert


class MockShipPeer:
    def __init__(
        self,
        *,
        identity: IdentityMaterial,
        trusted_client_cert: str,
        trace_logger: TraceLogger | None = None,
    ) -> None:
        self.identity = identity
        self.trusted_client_cert = trusted_client_cert
        self.trace = trace_logger or TraceLogger(None)
        self.server: asyncio.AbstractServer | None = None
        self.port: int | None = None
        self.client_ski: str | None = None

    async def start(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.identity.cert_path, self.identity.key_path)
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cafile=self.trusted_client_cert)
        self.server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0, ssl=context)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()
        self.server = None

    async def _send_control(self, connection: _ServerWebSocketConnection, payload: dict[str, Any]) -> None:
        encoded = bytes([SHIP_MSG_CONTROL]) + to_eebus_json_bytes(payload)
        await connection.send_binary(encoded)
        self.trace.log("server_tx_control", payload=payload)

    async def _send_data(self, connection: _ServerWebSocketConnection, payload: dict[str, Any]) -> None:
        encoded = bytes([SHIP_MSG_DATA]) + to_eebus_json_bytes(payload)
        await connection.send_binary(encoded)
        self.trace.log("server_tx_data", payload=payload)

    async def _receive_message(self, connection: _ServerWebSocketConnection) -> tuple[int, Any]:
        frame = await connection.receive_frame()
        if frame.opcode == 0x8:
            raise ConnectionError("client closed the loopback session")
        if frame.opcode != 0x2 or not frame.payload:
            raise ConnectionError(f"unexpected websocket frame opcode={frame.opcode}")
        msg_type = frame.payload[0]
        body = frame.payload[1:]
        if msg_type == SHIP_MSG_INIT:
            return msg_type, body
        return msg_type, from_eebus_json_bytes(body)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = _ServerWebSocketConnection(reader, writer)
        await connection.handshake()
        self.client_ski = extract_ski_from_peer_cert(connection.peer_certificate_der())
        self.trace.log("server_tls_connected", peer_ski=self.client_ski)

        msg_type, body = await self._receive_message(connection)
        if msg_type != SHIP_MSG_INIT:
            raise ConnectionError("expected initial CMI message")
        await connection.send_binary(bytes([SHIP_MSG_INIT, 0x00]))

        msg_type, payload = await self._receive_message(connection)
        if msg_type != SHIP_MSG_CONTROL or "connectionHello" not in payload:
            raise ConnectionError("expected connectionHello from client")
        await self._send_control(connection, {"connectionHello": {"phase": "ready", "waiting": 60000}})

        msg_type, payload = await self._receive_message(connection)
        if msg_type != SHIP_MSG_CONTROL or "messageProtocolHandshake" not in payload:
            raise ConnectionError("expected messageProtocolHandshake announce")
        await self._send_control(
            connection,
            {
                "messageProtocolHandshake": {
                    "handshakeType": "select",
                    "version": {"major": 1, "minor": 0},
                    "formats": {"format": ["JSON-UTF8"]},
                }
            },
        )

        msg_type, payload = await self._receive_message(connection)
        if msg_type != SHIP_MSG_CONTROL or "messageProtocolHandshake" not in payload:
            raise ConnectionError("expected messageProtocolHandshake select")

        msg_type, payload = await self._receive_message(connection)
        if msg_type != SHIP_MSG_CONTROL or "connectionPinState" not in payload:
            raise ConnectionError("expected connectionPinState")
        await self._send_control(connection, {"connectionPinState": {"pinState": "none"}})

        msg_type, payload = await self._receive_message(connection)
        if msg_type != SHIP_MSG_CONTROL or "accessMethodsRequest" not in payload:
            raise ConnectionError("expected accessMethodsRequest")
        await self._send_control(connection, {"accessMethodsRequest": {}})

        msg_type, payload = await self._receive_message(connection)
        if msg_type != SHIP_MSG_CONTROL or "accessMethods" not in payload:
            raise ConnectionError("expected accessMethods from client")
        await self._send_control(connection, {"accessMethods": {"id": self.identity.ship_id}})

        await self._send_data(
            connection,
            SpineDatagram(
                payload={
                    "datagram": {
                        "payload": {
                            "cmd": [
                                {
                                    "nodeManagementDetailedDiscoveryData": {
                                        "node": [{"id": 0}],
                                        "description": "loopback-selftest",
                                    }
                                }
                            ]
                        }
                    }
                }
            ).as_ship_payload(),
        )

        try:
            while True:
                msg_type, payload = await self._receive_message(connection)
                if msg_type == SHIP_MSG_DATA:
                    await self._send_data(connection, payload)
        except Exception:
            await connection.close()


async def run_loopback_selftest(
    *,
    work_dir: str | None = None,
    verify_tls: bool = True,
    trace_path: str | None = None,
) -> LoopbackResult:
    context = nullcontext(Path(work_dir)) if work_dir is not None else tempfile.TemporaryDirectory()
    with context as root:
        base = Path(root)
        base.mkdir(parents=True, exist_ok=True)
        server_identity = IdentityStore.create(base / "server", device_id="LOOPBACK-SERVER", overwrite=True)
        client_identity = IdentityStore.create(base / "client", device_id="LOOPBACK-CLIENT", overwrite=True)

        server_trace = TraceLogger(None)
        peer = MockShipPeer(
            identity=server_identity,
            trusted_client_cert=client_identity.cert_path,
            trace_logger=server_trace,
        )
        await peer.start()
        try:
            trust = TrustStore.from_server_ski(
                server_identity.ski,
                verify_tls=verify_tls,
                trust_anchors=(server_identity.cert_path,) if verify_tls else (),
            )
            session = await ShipSession.connect(
                ShipConnectionConfig(
                    host="127.0.0.1",
                    port=peer.port or 0,
                    path="/ship/",
                    server_name=server_identity.common_name,
                    timeout=10.0,
                    pairing_wait_seconds=10,
                ),
                client_identity,
                trust,
                trace_logger=TraceLogger(trace_path),
            )
            try:
                datagram = await session.receive_datagram(timeout=5.0)
            finally:
                await session.close()
        finally:
            await peer.stop()

        return LoopbackResult(
            server_port=peer.port or 0,
            client_ship_id=client_identity.ship_id,
            server_ship_id=server_identity.ship_id,
            server_ski=server_identity.ski,
            peer_client_ski=peer.client_ski or "",
            received_discovery_payloads=len(extract_discovery_payloads(datagram)),
            trace_path=trace_path,
        )
