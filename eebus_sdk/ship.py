"""Async SHIP session handling for EEBus peers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Protocol

from .exceptions import PairingRejectedError, ShipHandshakeError
from .identity import IdentityMaterial, extract_ski_from_peer_cert, normalize_ski
from .json_codec import from_eebus_json_bytes, to_eebus_json_bytes
from .spine import SpineDatagram
from .trace import TraceLogger
from .trust import TrustStore
from .websocket import AsyncTLSWebSocketClient, WebSocketFrame

SHIP_MSG_INIT = 0
SHIP_MSG_CONTROL = 1
SHIP_MSG_DATA = 2
SHIP_MSG_END = 3


class AsyncBinaryTransport(Protocol):
    async def connect(self) -> None: ...
    async def receive_frame(self) -> WebSocketFrame: ...
    async def send_binary(self, payload: bytes) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...
    def peer_certificate_der(self) -> bytes: ...


@dataclass(slots=True)
class ShipConnectionConfig:
    host: str
    port: int
    path: str
    server_name: str
    timeout: float = 10.0
    pairing_wait_seconds: int = 60


@dataclass(slots=True)
class ShipEvent:
    kind: str
    payload: Any
    raw_hex: str | None = None


class ShipSession:
    def __init__(
        self,
        config: ShipConnectionConfig,
        identity: IdentityMaterial,
        trust: TrustStore,
        *,
        trace_logger: TraceLogger | None = None,
        transport_factory: Callable[..., AsyncBinaryTransport] = AsyncTLSWebSocketClient,
        transport: AsyncBinaryTransport | None = None,
    ) -> None:
        self.config = config
        self.identity = identity
        self.trust = trust
        self.trace = trace_logger or TraceLogger(None)
        self._transport_factory = transport_factory
        self.transport = transport
        self.remote_ship_id: str | None = None
        self.remote_server_ski: str | None = None

    @classmethod
    async def connect(
        cls,
        config: ShipConnectionConfig,
        identity: IdentityMaterial,
        trust: TrustStore,
        *,
        trace_logger: TraceLogger | None = None,
        transport_factory: Callable[..., AsyncBinaryTransport] = AsyncTLSWebSocketClient,
        transport: AsyncBinaryTransport | None = None,
    ) -> "ShipSession":
        session = cls(
            config,
            identity,
            trust,
            trace_logger=trace_logger,
            transport_factory=transport_factory,
            transport=transport,
        )
        await session.open()
        return session

    async def open(self) -> None:
        if self.transport is None:
            ssl_context = self.trust.create_client_ssl_context(self.identity)
            self.transport = self._transport_factory(
                host=self.config.host,
                port=self.config.port,
                path=self.config.path,
                server_name=self.config.server_name,
                ssl_context=ssl_context,
                timeout=self.config.timeout,
            )
        await self.transport.connect()

        peer_ski: str | None = None
        try:
            peer_der = self.transport.peer_certificate_der()
        except Exception:
            peer_der = b""
        if peer_der:
            peer_ski = extract_ski_from_peer_cert(peer_der)
        elif hasattr(self.transport, "peer_ski"):
            peer_ski = normalize_ski(getattr(self.transport, "peer_ski"))
        self.remote_server_ski = peer_ski
        self.trace.log("tls_connected", peer_ski=self.remote_server_ski)
        self.trust.validate_peer_ski(self.remote_server_ski)
        self.trace.log("trust_validated", expected_server_ski=self.trust.pins.expected_server_ski)
        await self.perform_handshake()

    async def close(self) -> None:
        if self.transport is not None:
            await self.transport.close()

    async def _send_cmi_init(self) -> None:
        payload = bytes([SHIP_MSG_INIT, 0x00])
        await self.transport.send_binary(payload)
        self.trace.log("tx_cmi", hex=payload.hex())

    async def send_control(self, payload: dict[str, Any]) -> None:
        encoded = bytes([SHIP_MSG_CONTROL]) + to_eebus_json_bytes(payload)
        await self.transport.send_binary(encoded)
        self.trace.log("tx_control", payload=payload, hex=encoded.hex())

    async def send_spine(self, payload: SpineDatagram | dict[str, Any]) -> None:
        datagram = payload if isinstance(payload, SpineDatagram) else SpineDatagram(payload=payload)
        encoded = bytes([SHIP_MSG_DATA]) + to_eebus_json_bytes(datagram.as_ship_payload())
        await self.transport.send_binary(encoded)
        self.trace.log("tx_data", payload=datagram.as_ship_payload(), hex=encoded.hex())

    async def _receive_ship_message(self, *, timeout: float | None = None) -> tuple[int, Any, bytes]:
        async def read_once() -> tuple[int, Any, bytes]:
            frame = await self.transport.receive_frame()
            if frame.opcode == 0x8:
                code = int.from_bytes(frame.payload[:2], "big") if len(frame.payload) >= 2 else None
                reason = frame.payload[2:].decode("utf-8", "replace") if len(frame.payload) > 2 else ""
                self.trace.log("rx_close", code=code, reason=reason, hex=frame.payload.hex())
                if code == 4452:
                    raise PairingRejectedError(reason or "remote node rejected local certificate at SHIP level")
                raise ShipHandshakeError(f"websocket closed by remote: code={code} reason={reason!r}")
            if frame.opcode != 0x2:
                raise ShipHandshakeError(f"unexpected websocket opcode {frame.opcode}")
            if not frame.payload:
                raise ShipHandshakeError("received empty SHIP payload")

            msg_type = frame.payload[0]
            body = frame.payload[1:]
            if msg_type == SHIP_MSG_INIT:
                self.trace.log("rx_cmi", hex=frame.payload.hex())
                return msg_type, body, frame.payload

            decoded = from_eebus_json_bytes(body)
            if msg_type == SHIP_MSG_CONTROL:
                self.trace.log("rx_control", payload=decoded, hex=frame.payload.hex())
            elif msg_type == SHIP_MSG_DATA:
                self.trace.log("rx_data", payload=decoded, hex=frame.payload.hex())
            else:
                self.trace.log("rx_other", msg_type=msg_type, payload=decoded, hex=frame.payload.hex())
            return msg_type, decoded, frame.payload

        if timeout is None:
            return await read_once()
        return await asyncio.wait_for(read_once(), timeout=timeout)

    async def _expect_cmi_ack(self) -> None:
        msg_type, body, _ = await self._receive_ship_message()
        if msg_type != SHIP_MSG_INIT or body not in (b"", b"\x00"):
            raise ShipHandshakeError(f"expected CMI ack, got msg_type={msg_type} body={body!r}")

    async def _hello(self) -> None:
        await self.send_control({"connectionHello": {"phase": "ready", "waiting": 60000}})
        loop = asyncio.get_running_loop()
        pending_deadline = loop.time() + self.config.pairing_wait_seconds

        while True:
            msg_type, payload, _ = await self._receive_ship_message()
            if msg_type != SHIP_MSG_CONTROL or "connectionHello" not in payload:
                raise ShipHandshakeError(f"expected connectionHello, got {payload!r}")
            hello = payload["connectionHello"]
            phase = hello.get("phase")
            if phase == "ready":
                return
            if phase == "aborted":
                raise ShipHandshakeError("remote aborted SHIP hello phase")
            if phase != "pending":
                raise ShipHandshakeError(f"unexpected hello phase {phase!r}")

            if loop.time() >= pending_deadline:
                raise PairingRejectedError(
                    "remote node stayed in pending state too long; PPC-side pairing or trust enrollment is still missing"
                )

            waiting_ms = hello.get("waiting", 60000)
            self.trace.log("hello_pending", waiting_ms=waiting_ms)
            wait_limit = loop.time() + max(1.0, waiting_ms / 1000.0)
            while loop.time() < wait_limit:
                remaining = max(0.1, wait_limit - loop.time())
                try:
                    msg_type, payload, _ = await self._receive_ship_message(timeout=remaining)
                except asyncio.TimeoutError:
                    continue
                if msg_type != SHIP_MSG_CONTROL or "connectionHello" not in payload:
                    raise ShipHandshakeError(f"expected connectionHello during pending state, got {payload!r}")
                hello = payload["connectionHello"]
                phase = hello.get("phase")
                if phase == "ready":
                    return
                if phase == "aborted":
                    raise ShipHandshakeError("remote aborted SHIP hello phase")
                if phase == "pending" and hello.get("prolongationRequest"):
                    await self.send_control({"connectionHello": {"phase": "pending", "waiting": 60000}})
                    break
                if phase == "pending":
                    waiting_ms = hello.get("waiting", waiting_ms)
                    self.trace.log("hello_pending", waiting_ms=waiting_ms)
                    wait_limit = loop.time() + max(1.0, waiting_ms / 1000.0)
                    continue
                raise ShipHandshakeError(f"unexpected hello phase {phase!r}")
            else:
                await self.send_control({"connectionHello": {"phase": "pending", "prolongationRequest": True}})

    async def _protocol_handshake(self) -> None:
        await self.send_control(
            {
                "messageProtocolHandshake": {
                    "handshakeType": "announceMax",
                    "version": {"major": 1, "minor": 0},
                    "formats": {"format": ["JSON-UTF8"]},
                }
            }
        )
        msg_type, payload, _ = await self._receive_ship_message()
        if msg_type != SHIP_MSG_CONTROL or "messageProtocolHandshake" not in payload:
            raise ShipHandshakeError(f"expected messageProtocolHandshake, got {payload!r}")
        selected = payload["messageProtocolHandshake"]
        if selected.get("handshakeType") != "select":
            raise ShipHandshakeError(f"unexpected handshakeType {selected!r}")
        version = selected.get("version", {})
        if version.get("major") != 1 or version.get("minor") != 0:
            raise ShipHandshakeError(f"unsupported SHIP protocol version {version!r}")
        formats = selected.get("formats", {}).get("format", [])
        if formats != ["JSON-UTF8"]:
            raise ShipHandshakeError(f"unsupported SHIP format selection {formats!r}")
        await self.send_control(
            {
                "messageProtocolHandshake": {
                    "handshakeType": "select",
                    "version": {"major": 1, "minor": 0},
                    "formats": {"format": ["JSON-UTF8"]},
                }
            }
        )

    async def _pin_handshake(self) -> None:
        await self.send_control({"connectionPinState": {"pinState": "none"}})
        msg_type, payload, _ = await self._receive_ship_message()
        if msg_type != SHIP_MSG_CONTROL or "connectionPinState" not in payload:
            raise ShipHandshakeError(f"expected connectionPinState, got {payload!r}")
        if payload["connectionPinState"].get("pinState") != "none":
            raise ShipHandshakeError(f"unsupported remote pinState {payload['connectionPinState']!r}")

    async def _access_methods_handshake(self) -> None:
        await self.send_control({"accessMethodsRequest": {}})
        got_remote_id = False
        while not got_remote_id:
            msg_type, payload, _ = await self._receive_ship_message()
            if msg_type != SHIP_MSG_CONTROL:
                raise ShipHandshakeError(f"expected SHIP control message, got {payload!r}")
            if "accessMethodsRequest" in payload:
                await self.send_control({"accessMethods": {"id": self.identity.ship_id}})
                continue
            if "accessMethods" in payload:
                remote_id = payload["accessMethods"].get("id")
                if not remote_id:
                    raise ShipHandshakeError("accessMethods response did not include a remote SHIP ID")
                self.remote_ship_id = remote_id
                got_remote_id = True
                continue
            raise ShipHandshakeError(f"unexpected access methods payload {payload!r}")

    async def perform_handshake(self) -> str:
        await self._send_cmi_init()
        await self._expect_cmi_ack()
        await self._hello()
        await self._protocol_handshake()
        await self._pin_handshake()
        await self._access_methods_handshake()
        self.trace.log("ship_handshake_complete", remote_ship_id=self.remote_ship_id)
        return self.remote_ship_id or ""

    async def receive_datagram(self, *, timeout: float | None = None) -> SpineDatagram:
        while True:
            msg_type, payload, _ = await self._receive_ship_message(timeout=timeout)
            if msg_type == SHIP_MSG_DATA:
                return SpineDatagram.from_ship_payload(payload)

    async def events(self) -> AsyncIterator[ShipEvent]:
        while True:
            msg_type, payload, raw = await self._receive_ship_message()
            if msg_type == SHIP_MSG_CONTROL:
                yield ShipEvent(kind="control", payload=payload, raw_hex=raw.hex())
            elif msg_type == SHIP_MSG_DATA:
                yield ShipEvent(kind="datagram", payload=SpineDatagram.from_ship_payload(payload), raw_hex=raw.hex())
            elif msg_type == SHIP_MSG_END:
                yield ShipEvent(kind="end", payload=payload, raw_hex=raw.hex())
