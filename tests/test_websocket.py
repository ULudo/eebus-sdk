from __future__ import annotations

import asyncio
import ssl
import unittest

from eebus_sdk.exceptions import TransportError
from eebus_sdk.websocket import AsyncTLSWebSocketClient, encode_frame


class WebSocketEncodingTests(unittest.TestCase):
    def test_unmasked_small_frame(self) -> None:
        frame = encode_frame(0x2, b"abc", mask=False)
        self.assertEqual(frame, b"\x82\x03abc")

    def test_masked_frame_has_mask_bit_and_length(self) -> None:
        frame = encode_frame(0x2, b"abc", mask=True)
        self.assertEqual(frame[0], 0x82)
        self.assertEqual(frame[1], 0x83)
        self.assertEqual(len(frame), 2 + 4 + 3)

    def test_extended_length_frame(self) -> None:
        payload = b"x" * 200
        frame = encode_frame(0x2, payload, mask=False)
        self.assertEqual(frame[0], 0x82)
        self.assertEqual(frame[1], 126)
        self.assertEqual(frame[2:4], b"\x00\xc8")
        self.assertEqual(frame[4:], payload)


class _IncompleteReadReader:
    async def readexactly(self, size: int) -> bytes:
        raise asyncio.IncompleteReadError(partial=b"", expected=size)


class _BrokenWriter:
    def write(self, data: bytes) -> None:
        raise ConnectionResetError("boom")

    async def drain(self) -> None:
        return None


class WebSocketTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_readexactly_translates_incomplete_read_to_transport_error(self) -> None:
        client = AsyncTLSWebSocketClient(
            host="127.0.0.1",
            port=4711,
            path="/ship/",
            server_name="peer.local",
            ssl_context=ssl.create_default_context(),
        )
        client.reader = _IncompleteReadReader()

        with self.assertRaises(TransportError) as ctx:
            await client._readexactly(2)

        self.assertIn("closed while reading 2 bytes", str(ctx.exception))

    async def test_write_translates_connection_reset_to_transport_error(self) -> None:
        client = AsyncTLSWebSocketClient(
            host="127.0.0.1",
            port=4711,
            path="/ship/",
            server_name="peer.local",
            ssl_context=ssl.create_default_context(),
        )
        client.writer = _BrokenWriter()

        with self.assertRaises(TransportError) as ctx:
            await client._write(b"hello")

        self.assertIn("websocket write failed", str(ctx.exception))
