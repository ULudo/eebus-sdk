"""Async SHIP session handling for EEBus peers."""

from __future__ import annotations

import asyncio
import contextlib
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
    async def send_ping(self, payload: bytes = b"") -> None: ...
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
    websocket_ping_interval_seconds: float | None = None
    keepalive_interval_seconds: float | None = None
    keepalive_timeout_millis: int | None = None
    send_access_methods_response: bool = True
    send_local_access_methods: bool = True
    send_local_access_methods_mdns: bool = False
    access_handshake_mode: str = "standard"
    compatibility_access_send_alternative_methods: bool = True
    compatibility_access_send_proactive_request: bool = True
    compatibility_access_send_method_selection: bool = True
    compatibility_access_send_local_id: bool = False
    compatibility_access_select_delay_seconds: float = 0.2
    compatibility_access_user_trust_key: str = "level"

    def __post_init__(self) -> None:
        if self.access_handshake_mode not in {"standard", "compatibility"}:
            raise ValueError(f"unsupported SHIP handshake mode: {self.access_handshake_mode}")


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
        self._access_confirmed = False
        self._ping_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._compatibility_methods_select_task: asyncio.Task[None] | None = None

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
        if self._compatibility_methods_select_task is not None:
            self._compatibility_methods_select_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._compatibility_methods_select_task
            self._compatibility_methods_select_task = None
        if self._ping_task is not None:
            self._ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ping_task
            self._ping_task = None
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._keepalive_task
            self._keepalive_task = None
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
                    "remote node stayed in pending state too long; peer-side trust enrollment is likely still missing"
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

    @staticmethod
    def _control_payload_as_mapping(payload: Any) -> dict[str, Any]:
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _extract_request_id(payload: Any) -> str | int | None:
        mapping = payload if isinstance(payload, dict) else {}
        request_id = mapping.get("requestId")
        if isinstance(request_id, (str, int)):
            return request_id
        return None

    @classmethod
    def _extract_user_trust_levels(cls, payload: Any) -> list[int]:
        mapping = cls._control_payload_as_mapping(payload)
        methods = mapping.get("methods", mapping)
        candidates = methods if isinstance(methods, list) else [methods]
        levels: list[int] = []

        for candidate in candidates:
            candidate_mapping = cls._control_payload_as_mapping(candidate)
            user_trust = candidate_mapping.get("userTrust", candidate_mapping)
            trust_entries = user_trust if isinstance(user_trust, list) else [user_trust]
            for trust_entry in trust_entries:
                trust_mapping = cls._control_payload_as_mapping(trust_entry)
                raw_levels = trust_mapping.get("levels")
                if isinstance(raw_levels, list):
                    levels.extend(level for level in raw_levels if isinstance(level, int))
                raw_level = trust_mapping.get("level")
                if isinstance(raw_level, int):
                    levels.append(raw_level)

        deduplicated: list[int] = []
        for level in levels:
            if level not in deduplicated:
                deduplicated.append(level)
        return deduplicated

    def _build_access_methods_response(self, request_payload: Any) -> dict[str, Any]:
        response: dict[str, Any] = {"methods": {"userTrust": {"levels": [1]}}}
        request_id = self._extract_request_id(request_payload)
        if request_id is not None:
            response["requestId"] = request_id
        return {"accessMethodsResponse": response}

    def _build_local_access_methods(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.identity.ship_id}
        if self.config.send_local_access_methods_mdns:
            payload["dnsSd_mDns"] = {}
        return {"accessMethods": payload}

    def _build_access_request(self, response_payload: Any) -> dict[str, Any] | None:
        levels = self._extract_user_trust_levels(response_payload)
        if not levels:
            return None

        request: dict[str, Any] = {"methods": {"userTrust": {"level": min(levels)}}}
        request_id = self._extract_request_id(response_payload)
        if request_id is not None:
            request["requestId"] = request_id
        return {"accessRequest": request}

    @staticmethod
    def _normalize_compatibility_levels(levels: Any) -> list[int]:
        if isinstance(levels, int):
            candidates = [levels]
        elif isinstance(levels, list):
            candidates = [level for level in levels if isinstance(level, int)]
        else:
            candidates = []

        if not candidates:
            candidates = [1]

        normalized: list[int] = []
        for level in candidates:
            if level not in normalized:
                normalized.append(level)
        return normalized

    @classmethod
    def _compose_compatibility_user_trust_entry(
        cls,
        levels: Any,
        *,
        key: str = "level",
        allow_scalar: bool = False,
    ) -> tuple[list[int], dict[str, Any]]:
        normalized = cls._normalize_compatibility_levels(levels)
        if key == "levels":
            value: Any = list(normalized)
        elif allow_scalar and len(normalized) == 1:
            value = normalized[0]
        else:
            value = list(normalized)
        return normalized, {key: value}

    @classmethod
    def _build_compatibility_methods_block(
        cls,
        levels: Any,
        *,
        include_second_factor: bool = False,
        container: str = "list",
        user_trust_key: str = "level",
        allow_scalar: bool = False,
    ) -> tuple[Any, list[int]]:
        normalized, user_trust_entry = cls._compose_compatibility_user_trust_entry(
            levels,
            key=user_trust_key,
            allow_scalar=allow_scalar,
        )

        if container == "object":
            methods_value: Any = {"userTrust": [user_trust_entry]}
            if include_second_factor:
                methods_value.setdefault("secondFactor", [{"pin": {}}])
            return methods_value, normalized

        methods_list: list[dict[str, Any]] = [{"userTrust": [user_trust_entry]}]
        if include_second_factor:
            methods_list.append({"secondFactor": [{"pin": {}}]})
        return methods_list, normalized

    @classmethod
    def _build_compatibility_access_methods_response(
        cls,
        request_payload: Any,
        *,
        user_trust_key: str = "level",
    ) -> dict[str, Any]:
        methods_value, _ = cls._build_compatibility_methods_block(
            [1],
            container="list",
            user_trust_key=user_trust_key,
            allow_scalar=False,
        )
        response: dict[str, Any] = {"methods": methods_value}
        request_id = cls._extract_request_id(request_payload)
        if request_id is not None:
            response["requestId"] = request_id
        return {"accessMethodsResponse": [response]}

    @classmethod
    def _build_compatibility_access_methods_offer(
        cls,
        request_payload: Any,
        *,
        user_trust_key: str = "level",
    ) -> dict[str, Any]:
        methods_value, _ = cls._build_compatibility_methods_block(
            [1],
            container="list",
            user_trust_key=user_trust_key,
            allow_scalar=False,
        )
        response: dict[str, Any] = {"methods": methods_value}
        request_id = cls._extract_request_id(request_payload)
        if request_id is not None:
            response["requestId"] = request_id
        return {"accessMethods": [response]}

    @classmethod
    def _build_compatibility_access_request(
        cls,
        request_payload: Any,
        *,
        user_trust_key: str = "level",
    ) -> dict[str, Any]:
        methods_value, _ = cls._build_compatibility_methods_block(
            [1],
            container="list",
            user_trust_key=user_trust_key,
            allow_scalar=False,
        )
        response: dict[str, Any] = {"methods": methods_value}
        request_id = cls._extract_request_id(request_payload)
        if request_id is not None:
            response["requestId"] = request_id
        return {"accessRequest": [response]}

    @classmethod
    def _build_compatibility_methods_select(cls, request_payload: Any) -> dict[str, Any]:
        _, user_trust_entry = cls._compose_compatibility_user_trust_entry(
            [1],
            key="level",
            allow_scalar=True,
        )
        response: dict[str, Any] = {"userTrust": [user_trust_entry]}
        request_id = cls._extract_request_id(request_payload)
        if request_id is not None:
            response["requestId"] = request_id
        return {"methodsSelect": [response]}

    def _schedule_compatibility_methods_select(self, request_payload: Any) -> None:
        if not self.config.compatibility_access_send_method_selection:
            return
        if self._compatibility_methods_select_task is not None and not self._compatibility_methods_select_task.done():
            self._compatibility_methods_select_task.cancel()

        async def _send_later() -> None:
            delay = max(0.0, self.config.compatibility_access_select_delay_seconds)
            if delay:
                await asyncio.sleep(delay)
            await self.send_control(self._build_compatibility_methods_select(request_payload))

        self._compatibility_methods_select_task = asyncio.create_task(_send_later())

    async def _send_compatibility_access_sequence(self, request_payload: Any) -> None:
        user_trust_key = self.config.compatibility_access_user_trust_key
        await self.send_control(
            self._build_compatibility_access_methods_response(
                request_payload,
                user_trust_key=user_trust_key,
            )
        )
        if self.config.compatibility_access_send_alternative_methods:
            await self.send_control(
                self._build_compatibility_access_methods_offer(
                    request_payload,
                    user_trust_key=user_trust_key,
                )
            )
        if self.config.compatibility_access_send_proactive_request:
            await self.send_control(
                self._build_compatibility_access_request(
                    request_payload,
                    user_trust_key=user_trust_key,
                )
            )
        self._schedule_compatibility_methods_select(request_payload)

    @staticmethod
    def _access_status_allows_keepalive(payload: Any) -> bool:
        mapping = payload if isinstance(payload, dict) else {}
        for key in ("status", "accessStatus", "state", "result"):
            value = mapping.get(key)
            if isinstance(value, str) and value.lower() in {"granted", "accepted", "confirmed", "ready", "ok"}:
                return True
            if value is True:
                return True
        return False

    def _mark_access_confirmed(self) -> None:
        if self._access_confirmed:
            return
        self._access_confirmed = True
        self.trace.log("access_confirmed", remote_ship_id=self.remote_ship_id)

    def _keepalive_timeout_millis(self) -> int:
        if self.config.keepalive_timeout_millis is not None:
            return self.config.keepalive_timeout_millis
        interval = self.config.keepalive_interval_seconds or 15.0
        return int((interval + 20.0) * 1000)

    async def _run_ping_loop(self) -> None:
        interval = self.config.websocket_ping_interval_seconds
        if interval is None:
            return

        try:
            while True:
                await asyncio.sleep(interval)
                if self.transport is None:
                    return
                await self.transport.send_ping()
                self.trace.log("tx_ping")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.trace.log("ping_failed", error=str(exc))

    def _start_ping_loop(self) -> None:
        if self.config.websocket_ping_interval_seconds is None:
            return
        if self._ping_task is not None and not self._ping_task.done():
            return
        self.trace.log("ping_started", interval_seconds=self.config.websocket_ping_interval_seconds)
        self._ping_task = asyncio.create_task(self._run_ping_loop())

    async def _run_keepalive_loop(self) -> None:
        interval = self.config.keepalive_interval_seconds
        if interval is None:
            return

        try:
            while True:
                await asyncio.sleep(interval)
                if self.transport is None:
                    return
                await self._send_cmi_init()
                await self.send_control({"connectionKeepAlive": {"timeout": self._keepalive_timeout_millis()}})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.trace.log("keepalive_failed", error=str(exc))

    def _start_keepalive_loop(self) -> None:
        if not self._access_confirmed or self.config.keepalive_interval_seconds is None:
            return
        if self._keepalive_task is not None and not self._keepalive_task.done():
            return
        self.trace.log(
            "keepalive_started",
            interval_seconds=self.config.keepalive_interval_seconds,
            timeout_millis=self._keepalive_timeout_millis(),
        )
        self._keepalive_task = asyncio.create_task(self._run_keepalive_loop())

    async def _access_methods_handshake(self) -> None:
        await self.send_control({"accessMethodsRequest": {}})
        sent_local_access_methods = False
        while True:
            msg_type, payload, _ = await self._receive_ship_message()
            if msg_type != SHIP_MSG_CONTROL:
                raise ShipHandshakeError(f"expected SHIP control message, got {payload!r}")
            if "accessMethodsRequest" in payload:
                if self.config.access_handshake_mode == "compatibility":
                    await self._send_compatibility_access_sequence(payload["accessMethodsRequest"])
                    if self.config.compatibility_access_send_local_id and not sent_local_access_methods:
                        await self.send_control(self._build_local_access_methods())
                        sent_local_access_methods = True
                    continue
                if self.config.send_access_methods_response:
                    await self.send_control(self._build_access_methods_response(payload["accessMethodsRequest"]))
                if self.config.send_local_access_methods and not sent_local_access_methods:
                    await self.send_control(self._build_local_access_methods())
                    sent_local_access_methods = True
                continue
            if "accessMethodsResponse" in payload:
                access_request = self._build_access_request(payload["accessMethodsResponse"])
                if access_request is not None:
                    await self.send_control(access_request)
                continue
            if "accessRequest" in payload:
                if self.config.send_local_access_methods and not sent_local_access_methods:
                    await self.send_control(self._build_local_access_methods())
                    sent_local_access_methods = True
                continue
            if "accessStatus" in payload:
                if self._access_status_allows_keepalive(payload["accessStatus"]):
                    self._mark_access_confirmed()
                continue
            if "accessMethods" in payload:
                access_methods = self._control_payload_as_mapping(payload["accessMethods"])
                remote_id = access_methods.get("id")
                if not remote_id:
                    raise ShipHandshakeError("accessMethods response did not include a remote SHIP ID")
                self.remote_ship_id = remote_id
                self._mark_access_confirmed()
                return
            raise ShipHandshakeError(f"unexpected access methods payload {payload!r}")

    async def perform_handshake(self) -> str:
        await self._send_cmi_init()
        await self._expect_cmi_ack()
        await self._hello()
        await self._protocol_handshake()
        await self._pin_handshake()
        await self._access_methods_handshake()
        self._start_ping_loop()
        self._start_keepalive_loop()
        self.trace.log("ship_handshake_complete", remote_ship_id=self.remote_ship_id)
        return self.remote_ship_id or ""

    async def receive_datagram(self, *, timeout: float | None = None) -> SpineDatagram:
        while True:
            msg_type, payload, _ = await self._receive_ship_message(timeout=timeout)
            if msg_type == SHIP_MSG_DATA:
                return SpineDatagram.from_ship_payload(payload)

    async def events(self) -> AsyncIterator[ShipEvent]:
        while True:
            try:
                msg_type, payload, raw = await self._receive_ship_message()
            except asyncio.TimeoutError:
                self.trace.log("idle_timeout")
                continue
            if msg_type == SHIP_MSG_CONTROL:
                yield ShipEvent(kind="control", payload=payload, raw_hex=raw.hex())
            elif msg_type == SHIP_MSG_DATA:
                yield ShipEvent(kind="datagram", payload=SpineDatagram.from_ship_payload(payload), raw_hex=raw.hex())
            elif msg_type == SHIP_MSG_END:
                yield ShipEvent(kind="end", payload=payload, raw_hex=raw.hex())
