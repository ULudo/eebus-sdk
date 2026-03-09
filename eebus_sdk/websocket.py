"""Minimal asyncio RFC6455 client for SHIP over WebSocket/TLS."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl
import struct
from dataclasses import dataclass

from .exceptions import TransportError, WebSocketProtocolError


@dataclass(slots=True)
class WebSocketFrame:
    opcode: int
    payload: bytes
    fin: bool = True


def encode_frame(opcode: int, payload: bytes, *, mask: bool, fin: bool = True) -> bytes:
    mask_key = os.urandom(4) if mask else b""
    length = len(payload)
    header = bytearray([(0x80 if fin else 0x00) | (opcode & 0x0F)])
    if length < 126:
        header.append((0x80 if mask else 0x00) | length)
    elif length < 65536:
        header.append((0x80 if mask else 0x00) | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append((0x80 if mask else 0x00) | 127)
        header.extend(struct.pack("!Q", length))

    if not mask:
        return bytes(header) + payload
    masked = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + mask_key + masked


class AsyncTLSWebSocketClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        path: str,
        server_name: str,
        ssl_context: ssl.SSLContext,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.server_name = server_name
        self.ssl_context = ssl_context
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host,
                    self.port,
                    ssl=self.ssl_context,
                    server_hostname=self.server_name,
                ),
                timeout=self.timeout,
            )
        except Exception as exc:  # pragma: no cover - asyncio/open_connection specifics vary by platform
            raise TransportError(f"TLS connect to {self.host}:{self.port} failed: {exc}") from exc

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.server_name}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Protocol: ship\r\n"
            "\r\n"
        )
        await self._write(request.encode("ascii"))

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = await asyncio.wait_for(self.reader.read(4096), timeout=self.timeout)
            if not chunk:
                raise TransportError("websocket upgrade failed: no HTTP response")
            response += chunk
        headers = response.decode("utf-8", "replace")
        if "101 Switching Protocols" not in headers:
            raise TransportError(f"websocket upgrade failed:\n{headers}")
        accept = None
        protocol = None
        for line in headers.split("\r\n"):
            lower = line.lower()
            if lower.startswith("sec-websocket-accept:"):
                accept = line.split(":", 1)[1].strip()
            elif lower.startswith("sec-websocket-protocol:"):
                protocol = line.split(":", 1)[1].strip()
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if accept != expected:
            raise TransportError("websocket upgrade failed: invalid Sec-WebSocket-Accept")
        if protocol != "ship":
            raise TransportError(f"websocket upgrade failed: expected 'ship' subprotocol, got {protocol!r}")

    async def _readexactly(self, size: int) -> bytes:
        if self.reader is None:
            raise TransportError("websocket is not connected")
        return await asyncio.wait_for(self.reader.readexactly(size), timeout=self.timeout)

    async def _write(self, data: bytes) -> None:
        if self.writer is None:
            raise TransportError("websocket is not connected")
        self.writer.write(data)
        await asyncio.wait_for(self.writer.drain(), timeout=self.timeout)

    async def send_frame(self, opcode: int, payload: bytes, *, fin: bool = True) -> None:
        await self._write(encode_frame(opcode, payload, mask=True, fin=fin))

    async def send_binary(self, payload: bytes) -> None:
        await self.send_frame(0x2, payload)

    async def receive_frame(self) -> WebSocketFrame:
        fragments: list[bytes] = []
        fragment_opcode: int | None = None

        while True:
            header = await self._readexactly(2)
            byte1, byte2 = header
            fin = bool(byte1 & 0x80)
            opcode = byte1 & 0x0F
            masked = bool(byte2 >> 7)
            length = byte2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", await self._readexactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", await self._readexactly(8))[0]
            mask = await self._readexactly(4) if masked else b""
            payload = await self._readexactly(length)
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

            if opcode == 0x9:
                await self.send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x0:
                if fragment_opcode is None:
                    raise WebSocketProtocolError("received unexpected continuation frame")
                fragments.append(payload)
                if fin:
                    complete = b"".join(fragments)
                    return WebSocketFrame(opcode=fragment_opcode, payload=complete, fin=True)
                continue
            if opcode in (0x1, 0x2) and not fin:
                fragment_opcode = opcode
                fragments = [payload]
                continue
            return WebSocketFrame(opcode=opcode, payload=payload, fin=fin)

    def peer_certificate_der(self) -> bytes:
        if self.writer is None:
            raise TransportError("websocket is not connected")
        ssl_object = self.writer.get_extra_info("ssl_object")
        if ssl_object is None:
            raise TransportError("peer certificate is not available")
        cert = ssl_object.getpeercert(binary_form=True)
        if cert is None:
            raise TransportError("peer certificate is not available")
        return cert

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.writer is None:
            return
        try:
            await self.send_frame(0x8, struct.pack("!H", code) + reason.encode("utf-8"))
        except Exception:
            pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass
        self.writer = None
        self.reader = None
