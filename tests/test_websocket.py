from __future__ import annotations

import unittest

from eebus_sdk.websocket import encode_frame


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
