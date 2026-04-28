from __future__ import annotations

import unittest
from types import SimpleNamespace

from eebus_sdk.cli import (
    _build_connection_config,
    _command_names,
    _format_datagram_summary,
    _matches_load_control,
    _should_request_remote_detailed_discovery,
    build_parser,
)
from eebus_sdk.spine import build_datagram


class CliHelpersTests(unittest.TestCase):
    def test_load_control_commands_are_detected(self) -> None:
        datagram = build_datagram(
            source={"device": "d:_i:REMOTE", "entity": [1], "feature": 3},
            destination={"device": "d:_n:HEMS", "entity": [1], "feature": 3},
            cmd_classifier="notify",
            msg_counter=42,
            commands=[{"loadControlLimitListData": {"loadControlLimitData": []}}],
        )

        names = _command_names(datagram)

        self.assertEqual(names, ["loadControlLimitListData"])
        self.assertTrue(_matches_load_control(names))
        summary = _format_datagram_summary(datagram)
        self.assertIn("cmdClassifier=notify", summary)
        self.assertIn("msgCounter=42", summary)
        self.assertIn("commands=loadControlLimitListData", summary)

    def test_non_load_control_commands_do_not_match(self) -> None:
        datagram = build_datagram(
            source={"device": "d:_i:REMOTE", "entity": [0], "feature": 0},
            destination={"device": "d:_n:HEMS", "entity": [0], "feature": 0},
            cmd_classifier="read",
            msg_counter=7,
            commands=[{"nodeManagementDetailedDiscoveryData": []}],
        )

        names = _command_names(datagram)

        self.assertEqual(names, ["nodeManagementDetailedDiscoveryData"])
        self.assertFalse(_matches_load_control(names))

    def test_connect_parser_accepts_profile_option(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "connect",
                "--identity",
                "identity.json",
                "--host",
                "127.0.0.1",
                "--port",
                "23292",
                "--server-name",
                "peer.local",
                "--profile",
                "hems-reference",
            ]
        )

        self.assertEqual(args.profile, "hems-reference")

    def test_hems_reference_connect_uses_minimal_access_methods_offer(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=23292,
            path="/ship/",
            server_name="peer.local",
            timeout=10.0,
            pairing_wait_seconds=60,
            profile="hems-reference",
        )

        config = _build_connection_config(args)

        self.assertEqual(config.access_handshake_mode, "standard")
        self.assertFalse(config.send_access_methods_response)
        self.assertTrue(config.send_local_access_methods)

    def test_cls_adapter_connect_uses_minimal_access_methods_offer(self) -> None:
        args = SimpleNamespace(
            host="127.0.0.1",
            port=23292,
            path="/ship/",
            server_name="peer.local",
            timeout=10.0,
            pairing_wait_seconds=60,
            profile="cls-adapter",
        )

        config = _build_connection_config(args)

        self.assertEqual(config.access_handshake_mode, "standard")
        self.assertFalse(config.send_access_methods_response)
        self.assertTrue(config.send_local_access_methods)

    def test_cls_adapter_connect_skips_active_remote_discovery_request(self) -> None:
        args = SimpleNamespace(profile="cls-adapter")
        client = SimpleNamespace(_remote_device_address="d:_i:REMOTE_PEER")

        self.assertFalse(_should_request_remote_detailed_discovery(args, client))

    def test_connect_parser_rejects_removed_debug_flags(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "connect",
                    "--identity",
                    "identity.json",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "23292",
                    "--server-name",
                    "peer.local",
                    "--legacy-minimal-access",
                ]
            )

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "connect",
                    "--identity",
                    "identity.json",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "23292",
                    "--server-name",
                    "peer.local",
                    "--client-profile",
                    "experimental-demo",
                ]
            )

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "connect",
                    "--identity",
                    "identity.json",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "23292",
                    "--server-name",
                    "peer.local",
                    "--websocket-ping-interval-seconds",
                    "15",
                ]
            )

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "connect",
                    "--identity",
                    "identity.json",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "23292",
                    "--server-name",
                    "peer.local",
                    "--ship-handshake-mode",
                    "compatibility",
                ]
            )

    def test_serve_parser_accepts_service_flags(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "serve",
                "--identity",
                "identity.json",
                "--interface-ip",
                "10.0.0.5",
                "--port",
                "4712",
                "--device-id",
                "TEST-SERVER",
                "--peer-trust-anchor",
                "peer.pem",
            ]
        )

        self.assertEqual(args.command, "serve")
        self.assertEqual(args.identity, "identity.json")
        self.assertEqual(args.interface_ip, "10.0.0.5")
        self.assertEqual(args.port, 4712)
        self.assertEqual(args.device_id, "TEST-SERVER")
        self.assertEqual(args.peer_trust_anchor, ["peer.pem"])

    def test_lpc_send_parser_accepts_public_lpc_flags(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "lpc",
                "send",
                "--identity",
                "identity.json",
                "--watts",
                "4000",
                "--duration-seconds",
                "600",
                "--trusted-client-ski",
                "0123",
                "--exit-after-confirmation",
            ]
        )

        self.assertEqual(args.command, "lpc")
        self.assertEqual(args.lp_command, "send")
        self.assertEqual(args.lp_use_case, "LPC")
        self.assertEqual(args.watts, 4000)
        self.assertEqual(args.duration_seconds, 600)
        self.assertEqual(args.limit_id, 0)
        self.assertEqual(args.trusted_client_ski, ["0123"])
        self.assertTrue(args.exit_after_confirmation)

    def test_lpp_send_parser_defaults_to_production_limit(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "lpp",
                "send",
                "--identity",
                "identity.json",
                "--watts",
                "2500",
                "--duration-seconds",
                "900",
            ]
        )

        self.assertEqual(args.command, "lpp")
        self.assertEqual(args.lp_command, "send")
        self.assertEqual(args.lp_use_case, "LPP")
        self.assertEqual(args.watts, 2500)
        self.assertEqual(args.duration_seconds, 900)
        self.assertEqual(args.limit_id, 1)

    def test_lp_bridge_parser_accepts_wallbox_and_source_peer_flags(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "lp",
                "bridge",
                "--identity",
                "identity.json",
                "--peer-trust-anchor",
                "wallbox.pem",
                "--peer-trust-anchor",
                "tool.pem",
                "--trusted-client-ski",
                "wallbox-ski",
                "--trusted-client-ski",
                "tool-ski",
                "--wallbox-ski",
                "abcdef",
                "--source-peer-ski",
                "012345",
                "--wallbox-identity",
                "wallbox-identity.json",
                "--wallbox-port",
                "4715",
                "--wallbox-peer-trust-anchor",
                "wallbox-peer.pem",
                "--wallbox-trusted-client-ski",
                "wallbox-ski",
                "--wallbox-ship-id",
                "i:local_u:wallbox-facing_r:HEMS",
            ]
        )

        self.assertEqual(args.command, "lp")
        self.assertEqual(args.lp_command, "bridge")
        self.assertEqual(args.peer_trust_anchor, ["wallbox.pem", "tool.pem"])
        self.assertEqual(args.trusted_client_ski, ["wallbox-ski", "tool-ski"])
        self.assertEqual(args.wallbox_ski, "abcdef")
        self.assertEqual(args.source_peer_ski, "012345")
        self.assertEqual(args.wallbox_identity, "wallbox-identity.json")
        self.assertEqual(args.wallbox_port, 4715)
        self.assertEqual(args.wallbox_peer_trust_anchor, ["wallbox-peer.pem"])
        self.assertEqual(args.wallbox_trusted_client_ski, ["wallbox-ski"])
        self.assertEqual(args.wallbox_ship_id, "i:local_u:wallbox-facing_r:HEMS")

    def test_lpc_bridge_is_no_longer_a_command(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "lpc",
                    "bridge",
                    "--identity",
                    "identity.json",
                    "--wallbox-ski",
                    "abcdef",
                ]
            )

    def test_serve_parser_rejects_one_shot_lpc_flags(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "serve",
                    "--identity",
                    "identity.json",
                    "--send-lpc-watts",
                    "4000",
                ]
            )

    def test_serve_parser_accepts_ship_id_override(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "serve",
                "--identity",
                "identity.json",
                "--ship-id",
                "Interop-HEMS-123456789",
            ]
        )

        self.assertEqual(args.command, "serve")
        self.assertEqual(args.ship_id, "Interop-HEMS-123456789")


if __name__ == "__main__":
    unittest.main()
