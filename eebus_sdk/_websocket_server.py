"""Small server-side WebSocket helper used by local SHIP endpoints."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import struct

from .websocket import WebSocketFrame, encode_frame


class ServerWebSocketConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._send_lock = asyncio.Lock()

    async def handshake(self, *, expected_path: str = "/ship/") -> None:
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
        path = request_line.split(" ", 2)[1]
        if path != expected_path:
            raise ConnectionError(f"unexpected websocket path {path!r}")

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
        async with self._send_lock:
            self.writer.write(encode_frame(opcode, payload, mask=False))
            await self.writer.drain()

    async def send_binary(self, payload: bytes) -> None:
        await self.send_frame(0x2, payload)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.send_frame(0x8, struct.pack("!H", 1000))
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()

    def peer_certificate_der(self) -> bytes:
        ssl_object = self.writer.get_extra_info("ssl_object")
        if ssl_object is None:
            raise RuntimeError("no SSL object on server connection")
        cert = ssl_object.getpeercert(binary_form=True)
        if cert is None:
            raise RuntimeError("client certificate not available")
        return cert
