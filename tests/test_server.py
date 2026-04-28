from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from eebus_sdk.advertisement import build_ship_txt_properties
from eebus_sdk.identity import IdentityStore
from eebus_sdk.json_codec import to_eebus_json_bytes
from eebus_sdk.server import _ActivePeerSession
from eebus_sdk.server import ShipServer, ShipServerConfig
from eebus_sdk.ship import ShipConnectionConfig, ShipSession
from eebus_sdk.spine import build_datagram, extract_commands, extract_header
from eebus_sdk.trace import TraceLogger
from eebus_sdk.trust import TrustStore


class ShipServerTests(unittest.IsolatedAsyncioTestCase):
    def test_build_ship_txt_properties_has_expected_shape(self) -> None:
        txt = build_ship_txt_properties(
            ski="11223344556677889900aabbccddeeff00112233",
            ship_id="Interop-HEMS-123456789",
        )

        self.assertEqual(txt[b"txtvers"], b"1")
        self.assertEqual(txt[b"path"], b"/ship/")
        self.assertEqual(txt[b"id"], b"Interop-HEMS-123456789")
        self.assertEqual(txt[b"ski"], b"11223344556677889900aabbccddeeff00112233")
        self.assertEqual(txt[b"register"], b"true")

    def test_ship_server_advertises_load_control_and_use_cases(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                ),
                trace_logger=TraceLogger(None),
            )

            discovery = listener._build_local_detailed_discovery()
            features = discovery["featureInformation"]
            feature_descriptions = {
                (
                    feature["description"]["featureAddress"]["entity"][0],
                    feature["description"]["featureAddress"]["feature"],
                ): feature["description"]
                for feature in features
            }
            self.assertEqual(feature_descriptions[(1, 1)]["featureType"], "DeviceDiagnosis")
            self.assertEqual(feature_descriptions[(1, 2)]["featureType"], "LoadControl")
            self.assertEqual(feature_descriptions[(1, 3)]["featureType"], "DeviceConfiguration")
            self.assertEqual(feature_descriptions[(1, 5)]["featureType"], "ElectricalConnection")
            self.assertEqual(feature_descriptions[(1, 6)]["featureType"], "LoadControl")
            self.assertEqual(feature_descriptions[(1, 9)]["featureType"], "Measurement")
            self.assertFalse(
                any(
                    feature["description"]["featureType"] == "Measurement"
                    and feature["description"]["role"] == "server"
                    for feature in features
                )
            )
            load_control = feature_descriptions[(1, 2)]
            supported = {
                entry["function"] for entry in load_control["supportedFunction"]
            }
            self.assertIn("loadControlLimitDescriptionListData", supported)
            self.assertIn("loadControlLimitListData", supported)
            load_control_operations = {
                entry["function"]: entry["possibleOperations"]
                for entry in load_control["supportedFunction"]
            }
            self.assertEqual(
                load_control_operations["loadControlLimitListData"],
                {"read": {}, "write": {"partial": {}}},
            )
            node_management = next(
                feature for feature in features if feature["description"]["featureType"] == "NodeManagement"
            )
            node_management_functions = {
                entry["function"] for entry in node_management["description"]["supportedFunction"]
            }
            self.assertIn("nodeManagementSubscriptionData", node_management_functions)
            self.assertIn("nodeManagementBindingData", node_management_functions)
            self.assertIn("nodeManagementDestinationListData", node_management_functions)
            self.assertTrue(
                any(
                    feature["description"]["featureType"] == "DeviceDiagnosis"
                    and feature["description"]["role"] == "client"
                    for feature in features
                )
            )
            self.assertTrue(
                any(
                    feature["description"]["featureType"] == "LoadControl"
                    and feature["description"]["role"] == "client"
                    for feature in features
                )
            )
            limit_descriptions = listener._build_local_load_control_limit_description_data()[
                "loadControlLimitDescriptionData"
            ]
            self.assertEqual(limit_descriptions[0]["measurementId"], 50)
            self.assertEqual(limit_descriptions[1]["measurementId"], 50)
            self.assertNotIn("label", limit_descriptions[0])
            limit_payload = listener._build_default_load_control_limit_payload()["loadControlLimitData"]
            self.assertEqual(limit_payload[1]["value"]["number"], -10000)
            config_payload = listener._build_default_device_configuration_payload()[
                "deviceConfigurationKeyValueData"
            ]
            self.assertEqual(config_payload[1]["value"]["duration"], "PT7200S")
            self.assertEqual(config_payload[2]["value"]["scaledNumber"]["number"], 4200)

            use_cases = listener._build_local_use_case_data()["useCaseInformation"]
            self.assertEqual(
                {entry["actor"] for entry in use_cases},
                {"ControllableSystem", "EnergyGuard", "MonitoringAppliance", "CEM"},
            )
            self.assertTrue(all(entry["address"]["entity"] == [1] for entry in use_cases))
            use_cases_by_actor = {entry["actor"]: entry for entry in use_cases}
            controllable_use_case_names = {
                entry["useCaseName"] for entry in use_cases_by_actor["ControllableSystem"]["useCaseSupport"]
            }
            self.assertEqual(
                controllable_use_case_names,
                {"limitationOfPowerConsumption", "limitationOfPowerProduction"},
            )
            monitoring_use_case_names = {
                entry["useCaseName"] for entry in use_cases_by_actor["MonitoringAppliance"]["useCaseSupport"]
            }
            self.assertEqual(monitoring_use_case_names, {"monitoringOfGridConnectionPoint"})

    def test_cls_load_power_profile_keeps_robotron_use_cases_separated(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                    spine_profile="cls-load-power",
                ),
                trace_logger=TraceLogger(None),
            )

            discovery = listener._build_local_detailed_discovery()
            entity_descriptions = {
                entity["description"]["entityAddress"]["entity"][0]: entity["description"]
                for entity in discovery["entityInformation"]
            }
            self.assertEqual(entity_descriptions[1]["entityType"], "CEM")
            self.assertEqual(entity_descriptions[6]["entityType"], "GridConnectionPointOfPremises")

            feature_descriptions = {
                (
                    feature["description"]["featureAddress"]["entity"][0],
                    feature["description"]["featureAddress"]["feature"],
                ): feature["description"]
                for feature in discovery["featureInformation"]
            }
            load_control = feature_descriptions[(1, 2)]
            self.assertEqual(load_control["featureType"], "LoadControl")
            load_control_operations = {
                entry["function"]: entry["possibleOperations"]
                for entry in load_control["supportedFunction"]
            }
            self.assertEqual(
                load_control_operations["loadControlLimitDescriptionListData"],
                {"read": {"partial": {}}},
            )
            self.assertEqual(
                load_control_operations["loadControlLimitListData"],
                {"read": {"partial": {}}, "write": {"partial": {}}},
            )
            self.assertEqual(feature_descriptions[(6, 1)]["featureType"], "DeviceConfiguration")
            self.assertEqual(feature_descriptions[(6, 2)]["featureType"], "ElectricalConnection")
            self.assertEqual(feature_descriptions[(6, 3)]["featureType"], "Measurement")

            use_cases = listener._build_local_use_case_data()["useCaseInformation"]
            use_cases_by_actor = {entry["actor"]: entry for entry in use_cases}
            self.assertEqual(set(use_cases_by_actor), {"ControllableSystem", "GridConnectionPoint"})
            self.assertEqual(use_cases_by_actor["ControllableSystem"]["address"]["entity"], [1])
            self.assertEqual(use_cases_by_actor["GridConnectionPoint"]["address"]["entity"], [6])
            controllable_use_case_names = {
                entry["useCaseName"] for entry in use_cases_by_actor["ControllableSystem"]["useCaseSupport"]
            }
            self.assertEqual(
                controllable_use_case_names,
                {"limitationOfPowerConsumption", "limitationOfPowerProduction"},
            )
            grid_use_case_names = {
                entry["useCaseName"] for entry in use_cases_by_actor["GridConnectionPoint"]["useCaseSupport"]
            }
            self.assertEqual(grid_use_case_names, {"monitoringOfGridConnectionPoint"})

    def test_node_management_calls_request_ack_and_include_server_feature_type(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                ),
                trace_logger=TraceLogger(None),
            )

            destination = {"device": "d:_i:REMOTE", "entity": [0], "feature": 0}
            client_address = {"device": "d:_n:LOCAL", "entity": [1], "feature": 7}
            server_address = {"entity": [1], "feature": 10}

            binding = listener._build_binding_request_call(
                destination=destination,
                client_address=client_address,
                server_address=server_address,
                server_feature_type="LoadControl",
            )
            subscription = listener._build_subscription_request_call(
                destination=destination,
                client_address=listener._local_nm_address(),
                server_address=destination,
                server_feature_type="NodeManagement",
            )

            binding_header = binding.payload["datagram"]["header"]
            self.assertTrue(binding_header["ackRequest"])
            binding_request = binding.payload["datagram"]["payload"]["cmd"][0]["nodeManagementBindingRequestCall"][
                "bindingRequest"
            ]
            self.assertEqual(binding_request["serverFeatureType"], "LoadControl")

            subscription_header = subscription.payload["datagram"]["header"]
            self.assertTrue(subscription_header["ackRequest"])
            subscription_request = subscription.payload["datagram"]["payload"]["cmd"][0][
                "nodeManagementSubscriptionRequestCall"
            ]["subscriptionRequest"]
            self.assertEqual(subscription_request["serverFeatureType"], "NodeManagement")

    def test_local_source_and_normalized_feature_addresses_keep_device_first_on_wire(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                ),
                trace_logger=TraceLogger(None),
            )

            remote_destination = {
                "entity": [0],
                "feature": 0,
                "device": "d:_i:REMOTE",
            }
            local_source = listener._local_source_for_destination(remote_destination)
            self.assertEqual(list(local_source.keys())[:3], ["device", "entity", "feature"])
            self.assertEqual(local_source["device"], listener._local_device())

            normalized = listener._normalize_feature_address(
                {"entity": [1], "feature": 7},
                default_device=listener._local_device(),
            )
            self.assertEqual(list(normalized.keys())[:3], ["device", "entity", "feature"])

            datagram = build_datagram(
                source=local_source,
                destination=remote_destination,
                msg_counter=1,
                cmd_classifier="reply",
                commands=[{"nodeManagementDetailedDiscoveryData": {}}],
            )
            wire = to_eebus_json_bytes(
                {"data": {"header": {"protocolId": "ee1.0"}, "payload": datagram.payload}}
            ).decode("utf-8")
            source_start = wire.index('"addressSource"')
            source_end = wire.index('"addressDestination"', source_start)
            source_wire = wire[source_start:source_end]
            self.assertLess(source_wire.index('"device"'), source_wire.index('"entity"'))
            self.assertLess(source_wire.index('"entity"'), source_wire.index('"feature"'))

    def test_remote_bootstrap_prefers_top_level_evse_features_and_defers_use_case_read(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                ),
                trace_logger=TraceLogger(None),
            )

            session_state = SimpleNamespace(
                binding_bootstrap_sent=False,
                remote_node_management_address={"device": "d:_i:REMOTE", "entity": [0], "feature": 0},
                last_remote_discovery={
                    "featureInformation": [
                        {
                            "description": {
                                "featureAddress": {"entity": [1], "feature": 10},
                                "featureType": "LoadControl",
                                "role": "server",
                            }
                        },
                        {
                            "description": {
                                "featureAddress": {"entity": [1, 1], "feature": 10},
                                "featureType": "LoadControl",
                                "role": "server",
                            }
                        },
                        {
                            "description": {
                                "featureAddress": {"entity": [1], "feature": 1000},
                                "featureType": "DeviceDiagnosis",
                                "role": "server",
                            }
                        },
                        {
                            "description": {
                                "featureAddress": {"entity": [1, 1], "feature": 5},
                                "featureType": "DeviceDiagnosis",
                                "role": "server",
                            }
                        },
                    ]
                },
                pending_binding_requests={},
            )

            outgoing = listener._bootstrap_from_remote_discovery(session_state)
            command_payloads = [datagram.payload["datagram"]["payload"]["cmd"][0] for datagram in outgoing]

            binding_requests = [
                command["nodeManagementBindingRequestCall"]["bindingRequest"]
                for command in command_payloads
                if "nodeManagementBindingRequestCall" in command
            ]
            self.assertEqual(binding_requests[0]["serverAddress"]["entity"], [1])
            self.assertEqual(binding_requests[0]["serverFeatureType"], "DeviceDiagnosis")
            self.assertEqual(binding_requests[1]["serverAddress"]["entity"], [1])
            self.assertEqual(binding_requests[1]["serverFeatureType"], "LoadControl")

            command_names = [next(iter(command)) for command in command_payloads]
            self.assertIn("nodeManagementBindingData", command_names)
            self.assertNotIn("nodeManagementUseCaseData", command_names)
            self.assertEqual(
                set(session_state.pending_binding_requests.values()),
                {"DeviceDiagnosis", "LoadControl"},
            )

    def test_binding_success_bootstraps_load_control_reads_without_binding_table_entries(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                ),
                trace_logger=TraceLogger(None),
            )

            session_state = SimpleNamespace(
                post_binding_bootstrap_sent=False,
                remote_node_management_address={"device": "d:_i:REMOTE", "entity": [0], "feature": 0},
                last_remote_discovery={
                    "featureInformation": [
                        {
                            "description": {
                                "featureAddress": {"entity": [1], "feature": 10},
                                "featureType": "LoadControl",
                                "role": "server",
                            }
                        }
                    ]
                },
                binding_feature_bootstrap_sent=set(),
            )

            outgoing = listener._bootstrap_common_after_binding(session_state)
            outgoing.extend(listener._bootstrap_feature_after_binding(session_state, "LoadControl"))
            command_payloads = [datagram.payload["datagram"]["payload"]["cmd"][0] for datagram in outgoing]
            command_names = [next(iter(command)) for command in command_payloads]

            self.assertIn("nodeManagementSubscriptionRequestCall", command_names)
            self.assertIn("loadControlLimitDescriptionListData", command_names)
            self.assertIn("loadControlLimitListData", command_names)
            self.assertIn("LoadControl", session_state.binding_feature_bootstrap_sent)

    def test_build_load_power_write_payload_uses_requested_limit_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                    send_load_power_limit_watts=4000,
                    send_load_power_duration_seconds=600,
                ),
                trace_logger=TraceLogger(None),
            )

            payload = listener._build_load_power_write_payload_for_values(
                watts=4000,
                duration_seconds=600,
                limit_id=0,
                is_active=True,
            )

            self.assertEqual(
                payload,
                {
                    "loadControlLimitListData": {
                        "loadControlLimitData": [
                            {
                                "limitId": 0,
                                "isLimitActive": True,
                                "timePeriod": {"endTime": "PT10M"},
                                "value": {"number": 4000, "scale": 0},
                            }
                        ]
                    }
                },
            )

            datagram = listener._build_load_power_write_datagram({"device": "REMOTE", "entity": [1], "feature": 10})
            wire = to_eebus_json_bytes({"data": {"header": {"protocolId": "ee1.0"}, "payload": datagram.payload}}).decode(
                "utf-8"
            )
            self.assertLess(wire.index('"limitId"'), wire.index('"isLimitActive"'))
            self.assertLess(wire.index('"isLimitActive"'), wire.index('"timePeriod"'))
            self.assertLess(wire.index('"timePeriod"'), wire.index('"value"'))

    def test_lpc_config_aliases_populate_load_power_fields(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            config = ShipServerConfig(
                identity=identity,
                ship_id="Interop-HEMS-123456789",
                send_lpc_limit_watts=2500,
                send_lpc_duration_seconds=300,
                send_lpc_limit_id=1,
            )

            self.assertEqual(config.send_load_power_limit_watts, 2500)
            self.assertEqual(config.send_load_power_duration_seconds, 300)
            self.assertEqual(config.send_load_power_limit_id, 1)

    def test_load_control_state_extraction_marks_lpc_and_lpp_directions(self) -> None:
        payload = {
            "loadControlLimitData": [
                {
                    "limitId": 0,
                    "isLimitActive": True,
                    "value": {"number": 4200, "scale": 0},
                },
                {
                    "limitId": 1,
                    "isLimitActive": True,
                    "value": {"number": -10000, "scale": 0},
                },
            ]
        }

        states = ShipServer._extract_load_control_limit_states(payload)

        self.assertEqual(states[0]["direction"], "consume")
        self.assertEqual(states[1]["direction"], "produce")
        self.assertEqual(states[1]["protocol_watts"], -10000)
        self.assertEqual(states[1]["watts"], 10000)

    async def test_load_power_events_report_write_ack_and_readback(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                    send_load_power_limit_watts=4000,
                    send_load_power_duration_seconds=600,
                ),
                trace_logger=TraceLogger(None),
            )

            session_state = SimpleNamespace(
                peer_ski="remote-ski",
                subscriptions=[],
                bindings=[],
                last_remote_discovery=None,
                remote_node_management_address=None,
                remote_discovery_requested=False,
                binding_bootstrap_sent=False,
                post_binding_bootstrap_sent=False,
                pending_binding_requests={},
                binding_feature_bootstrap_sent=set(),
                load_power_command_sent=False,
                load_power_write_msg_counter=None,
                load_power_readback_msg_counter=None,
                pending_load_power_writes={},
                pending_load_power_readbacks={},
            )
            sent_datagrams: list[object] = []

            async def fake_send_data(_connection: object, payload: object) -> None:
                sent_datagrams.append(payload)

            listener._send_data = fake_send_data  # type: ignore[method-assign]
            event_stream = listener.events().__aiter__()
            remote_load_control = {"device": "d:_i:REMOTE", "entity": [1], "feature": 10}

            initial_reply = build_datagram(
                source=remote_load_control,
                destination=listener._local_load_control_client_address(),
                cmd_classifier="reply",
                msg_counter=41,
                msg_counter_reference=40,
                commands=[
                    {
                        "loadControlLimitListData": {
                            "loadControlLimitData": [
                                {
                                    "limitId": 0,
                                    "isLimitActive": False,
                                    "value": {"number": 0, "scale": 0},
                                }
                            ]
                        }
                    }
                ],
            )

            await listener._handle_spine_data(object(), initial_reply, session_state=session_state)
            initial_events = [await event_stream.__anext__() for _ in range(3)]
            self.assertEqual([event.kind for event in initial_events], ["datagram", "load_power_write_sent", "summary"])
            load_power_sent = initial_events[1].payload
            self.assertEqual(load_power_sent["watts"], 4000)
            self.assertEqual(load_power_sent["duration_seconds"], 600)
            self.assertEqual(len(sent_datagrams), 2)

            result_datagram = build_datagram(
                source=remote_load_control,
                destination=listener._local_load_control_client_address(),
                cmd_classifier="result",
                msg_counter=42,
                msg_counter_reference=session_state.load_power_write_msg_counter,
                commands=[{"resultData": {"errorNumber": 0}}],
            )
            await listener._handle_spine_data(object(), result_datagram, session_state=session_state)
            result_events = [await event_stream.__anext__() for _ in range(3)]
            self.assertEqual(result_events[1].kind, "load_power_write_result")
            self.assertEqual(result_events[1].payload["error_number"], 0)

            readback_reply = build_datagram(
                source=remote_load_control,
                destination=listener._local_load_control_client_address(),
                cmd_classifier="reply",
                msg_counter=43,
                msg_counter_reference=session_state.load_power_readback_msg_counter,
                commands=[
                    {
                        "loadControlLimitListData": {
                            "loadControlLimitData": [
                                {
                                    "limitId": 0,
                                    "isLimitActive": True,
                                    "timePeriod": {"endTime": "PT10M"},
                                    "value": {"number": 4000, "scale": 0},
                                }
                            ]
                        }
                    }
                ],
            )
            await listener._handle_spine_data(object(), readback_reply, session_state=session_state)
            readback_events = [await event_stream.__anext__() for _ in range(3)]
            self.assertEqual(readback_events[1].kind, "load_power_readback")
            self.assertEqual(readback_events[1].payload["watts"], 4000)
            self.assertEqual(readback_events[1].payload["duration"], "PT10M")
            self.assertTrue(readback_events[1].payload["is_active"])

    async def test_send_load_power_limit_to_peer_tracks_pending_write_and_readback(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                ),
                trace_logger=TraceLogger(None),
            )
            sent_datagrams: list[object] = []

            async def fake_send_data(_connection: object, payload: object) -> None:
                sent_datagrams.append(payload)

            listener._send_data = fake_send_data  # type: ignore[method-assign]
            event_stream = listener.events().__aiter__()

            session_state = SimpleNamespace(
                peer_ski="wallbox-ski",
                subscriptions=[],
                bindings=[],
                last_remote_discovery={
                    "featureInformation": [
                        {
                            "description": {
                                "featureAddress": {"device": "d:_i:WALLBOX", "entity": [1], "feature": 10},
                                "featureType": "LoadControl",
                                "role": "server",
                            }
                        }
                    ]
                },
                remote_node_management_address={"device": "d:_i:WALLBOX", "entity": [0], "feature": 0},
                remote_discovery_requested=True,
                binding_bootstrap_sent=True,
                post_binding_bootstrap_sent=True,
                pending_binding_requests={},
                binding_feature_bootstrap_sent=set(),
                load_power_command_sent=False,
                load_power_write_msg_counter=None,
                load_power_readback_msg_counter=None,
                pending_load_power_writes={},
                pending_load_power_readbacks={},
            )
            listener._active_sessions["wallbox-ski"] = _ActivePeerSession(
                peer_ski="wallbox-ski",
                connection=object(),  # type: ignore[arg-type]
                session_state=session_state,  # type: ignore[arg-type]
            )

            metadata = await listener.send_load_power_limit_to_peer(
                "wallbox-ski",
                watts=8000,
                duration_seconds=600,
                limit_id=1,
                is_active=True,
            )

            write_commands = extract_commands(sent_datagrams[0])
            sent_limit = write_commands[0]["loadControlLimitListData"]["loadControlLimitData"][0]
            self.assertEqual(sent_limit["limitId"], 1)
            self.assertEqual(sent_limit["value"]["number"], -8000)
            self.assertEqual(metadata["watts"], 8000)
            self.assertEqual(metadata["limit_id"], 1)
            self.assertEqual(metadata["duration"], "PT10M")
            self.assertEqual(len(sent_datagrams), 2)
            self.assertIn(metadata["msg_counter"], session_state.pending_load_power_writes)
            self.assertIn(metadata["readback_msg_counter"], session_state.pending_load_power_readbacks)

            sent_event = await event_stream.__anext__()
            self.assertEqual(sent_event.kind, "load_power_write_sent")
            self.assertEqual(sent_event.payload["peer_ski"], "wallbox-ski")
            self.assertEqual(sent_event.payload["watts"], 8000)

    async def test_partial_remote_discovery_keeps_top_level_load_control_for_forwarded_load_power(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            identity = IdentityStore.create(base / "server", device_id="DEMO_HEMS", overwrite=True)
            listener = ShipServer(
                ShipServerConfig(
                    identity=identity,
                    ship_id="Interop-HEMS-123456789",
                    device_id="Interop_HEMS-123456789",
                ),
                trace_logger=TraceLogger(None),
            )
            sent_datagrams: list[object] = []

            async def fake_send_data(_connection: object, payload: object) -> None:
                sent_datagrams.append(payload)

            listener._send_data = fake_send_data  # type: ignore[method-assign]

            session_state = SimpleNamespace(
                peer_ski="wallbox-ski",
                subscriptions=[],
                bindings=[],
                last_remote_discovery={
                    "entityInformation": [
                        {"description": {"entityAddress": {"entity": [1]}, "entityType": "EVSE"}},
                    ],
                    "featureInformation": [
                        {
                            "description": {
                                "featureAddress": {"device": "d:_i:WALLBOX", "entity": [1], "feature": 10},
                                "featureType": "LoadControl",
                                "role": "server",
                            }
                        }
                    ],
                },
                remote_node_management_address={"device": "d:_i:WALLBOX", "entity": [0], "feature": 0},
                remote_discovery_requested=True,
                binding_bootstrap_sent=True,
                post_binding_bootstrap_sent=True,
                pending_binding_requests={},
                binding_feature_bootstrap_sent=set(),
                load_power_command_sent=False,
                load_power_write_msg_counter=None,
                load_power_readback_msg_counter=None,
                pending_load_power_writes={},
                pending_load_power_readbacks={},
            )
            listener._active_sessions["wallbox-ski"] = _ActivePeerSession(
                peer_ski="wallbox-ski",
                connection=object(),  # type: ignore[arg-type]
                session_state=session_state,  # type: ignore[arg-type]
            )

            partial_discovery = build_datagram(
                source={"device": "d:_i:WALLBOX", "entity": [0], "feature": 0},
                destination=listener._local_nm_address(),
                cmd_classifier="notify",
                msg_counter=44,
                commands=[
                    {
                        "nodeManagementDetailedDiscoveryData": {
                            "entityInformation": [
                                {"description": {"entityAddress": {"entity": [1, 1]}, "entityType": "EV"}},
                            ],
                            "featureInformation": [
                                {
                                    "description": {
                                        "featureAddress": {"entity": [1, 1], "feature": 10},
                                        "featureType": "LoadControl",
                                        "role": "server",
                                    }
                                }
                            ],
                        }
                    }
                ],
            )

            await listener._handle_spine_data(
                object(),  # type: ignore[arg-type]
                partial_discovery,
                session_state=session_state,  # type: ignore[arg-type]
            )
            await listener.send_load_power_limit_to_peer(
                "wallbox-ski",
                watts=4200,
                duration_seconds=None,
                limit_id=0,
                is_active=False,
            )

            self.assertGreaterEqual(len(sent_datagrams), 2)
            write_header = extract_header(sent_datagrams[-2])
            self.assertEqual(write_header["addressDestination"]["entity"], [1])
            self.assertEqual(write_header["addressDestination"]["feature"], 10)

    async def test_ship_server_accepts_ship_client_handshake(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            server_identity = IdentityStore.create(base / "server", device_id="LOOPBACK-SERVER", overwrite=True)
            client_identity = IdentityStore.create(base / "client", device_id="LOOPBACK-CLIENT", overwrite=True)

            listener = ShipServer(
                ShipServerConfig(
                    identity=server_identity,
                    ship_id="i:local_u:server_r:HEMS",
                    bind_host="127.0.0.1",
                    port=0,
                    peer_trust_anchors=(client_identity.cert_path,),
                    trusted_client_skis=(client_identity.ski,),
                ),
                trace_logger=TraceLogger(None),
            )
            await listener.start()
            try:
                port = listener.server.sockets[0].getsockname()[1]
                session = await ShipSession.connect(
                    ShipConnectionConfig(
                        host="127.0.0.1",
                        port=port,
                        path="/ship/",
                        server_name="127.0.0.1",
                        timeout=5.0,
                    ),
                    client_identity,
                    TrustStore.from_server_ski(server_identity.ski, verify_tls=False),
                    trace_logger=TraceLogger(None),
                )
                try:
                    self.assertEqual(session.remote_ship_id, "i:local_u:server_r:HEMS")
                finally:
                    await session.close()
            finally:
                await listener.stop()

    async def test_ship_server_bootstraps_remote_discovery_after_peer_reads_local_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            server_identity = IdentityStore.create(base / "server", device_id="LOOPBACK-SERVER", overwrite=True)
            client_identity = IdentityStore.create(base / "client", device_id="LOOPBACK-CLIENT", overwrite=True)

            listener = ShipServer(
                ShipServerConfig(
                    identity=server_identity,
                    ship_id="i:local_u:server_r:HEMS",
                    bind_host="127.0.0.1",
                    port=0,
                    peer_trust_anchors=(client_identity.cert_path,),
                    trusted_client_skis=(client_identity.ski,),
                ),
                trace_logger=TraceLogger(None),
            )
            await listener.start()
            try:
                port = listener.server.sockets[0].getsockname()[1]
                session = await ShipSession.connect(
                    ShipConnectionConfig(
                        host="127.0.0.1",
                        port=port,
                        path="/ship/",
                        server_name="127.0.0.1",
                        timeout=5.0,
                    ),
                    client_identity,
                    TrustStore.from_server_ski(server_identity.ski, verify_tls=False),
                    trace_logger=TraceLogger(None),
                )
                try:
                    await session.send_spine(
                        build_datagram(
                            source={"device": "LOOPBACK-CLIENT", "entity": [0], "feature": 0},
                            destination={"device": "LOOPBACK-SERVER", "entity": [0], "feature": 0},
                            cmd_classifier="read",
                            msg_counter=1,
                            ack_request=True,
                            commands=[{"nodeManagementDetailedDiscoveryData": []}],
                        )
                    )

                    saw_bootstrap_read = False
                    for _ in range(4):
                        datagram = await session.receive_datagram(timeout=1.0)
                        header = datagram.payload["datagram"]["header"]
                        command_names = {key for command in extract_commands(datagram) for key in command}
                        if header.get("cmdClassifier") == "read" and "nodeManagementDetailedDiscoveryData" in command_names:
                            saw_bootstrap_read = True
                            break

                    self.assertTrue(saw_bootstrap_read)
                finally:
                    await session.close()
            finally:
                await listener.stop()

    async def test_ship_server_skips_result_for_discovery_read_with_ack(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            server_identity = IdentityStore.create(base / "server", device_id="LOOPBACK-SERVER", overwrite=True)
            client_identity = IdentityStore.create(base / "client", device_id="LOOPBACK-CLIENT", overwrite=True)

            listener = ShipServer(
                ShipServerConfig(
                    identity=server_identity,
                    ship_id="i:local_u:server_r:HEMS",
                    bind_host="127.0.0.1",
                    port=0,
                    peer_trust_anchors=(client_identity.cert_path,),
                    trusted_client_skis=(client_identity.ski,),
                ),
                trace_logger=TraceLogger(None),
            )
            await listener.start()
            try:
                port = listener.server.sockets[0].getsockname()[1]
                session = await ShipSession.connect(
                    ShipConnectionConfig(
                        host="127.0.0.1",
                        port=port,
                        path="/ship/",
                        server_name="127.0.0.1",
                        timeout=5.0,
                    ),
                    client_identity,
                    TrustStore.from_server_ski(server_identity.ski, verify_tls=False),
                    trace_logger=TraceLogger(None),
                )
                try:
                    await session.send_spine(
                        build_datagram(
                            source={"device": "LOOPBACK-CLIENT", "entity": [0], "feature": 0},
                            destination={"device": "LOOPBACK-SERVER", "entity": [0], "feature": 0},
                            cmd_classifier="read",
                            msg_counter=1,
                            ack_request=True,
                            commands=[{"nodeManagementDetailedDiscoveryData": []}],
                        )
                    )

                    received = [await session.receive_datagram(timeout=1.0) for _ in range(2)]
                    classifiers = [message.payload["datagram"]["header"]["cmdClassifier"] for message in received]
                    command_names = [
                        next(iter(extract_commands(message)[0]))
                        for message in received
                    ]

                    self.assertIn("reply", classifiers)
                    self.assertIn("read", classifiers)
                    self.assertIn("nodeManagementDetailedDiscoveryData", command_names)
                    self.assertNotIn("result", classifiers)
                finally:
                    await session.close()
            finally:
                await listener.stop()

    async def test_ship_server_sends_periodic_device_diagnosis_heartbeat_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            server_identity = IdentityStore.create(base / "server", device_id="LOOPBACK-SERVER", overwrite=True)
            client_identity = IdentityStore.create(base / "client", device_id="LOOPBACK-CLIENT", overwrite=True)

            listener = ShipServer(
                ShipServerConfig(
                    identity=server_identity,
                    ship_id="i:local_u:server_r:HEMS",
                    bind_host="127.0.0.1",
                    port=0,
                    peer_trust_anchors=(client_identity.cert_path,),
                    trusted_client_skis=(client_identity.ski,),
                    heartbeat_interval_seconds=0.1,
                    heartbeat_timeout="PT10S",
                ),
                trace_logger=TraceLogger(None),
            )
            await listener.start()
            try:
                port = listener.server.sockets[0].getsockname()[1]
                session = await ShipSession.connect(
                    ShipConnectionConfig(
                        host="127.0.0.1",
                        port=port,
                        path="/ship/",
                        server_name="127.0.0.1",
                        timeout=5.0,
                    ),
                    client_identity,
                    TrustStore.from_server_ski(server_identity.ski, verify_tls=False),
                    trace_logger=TraceLogger(None),
                )
                try:
                    await session.send_spine(
                        build_datagram(
                            source={"device": "LOOPBACK-CLIENT", "entity": [0], "feature": 0},
                            destination={"device": "LOOPBACK-SERVER", "entity": [0], "feature": 0},
                            cmd_classifier="call",
                            msg_counter=1,
                            commands=[
                                {
                                    "nodeManagementSubscriptionRequestCall": {
                                        "subscriptionRequest": {
                                            "clientAddress": {
                                                "device": "LOOPBACK-CLIENT",
                                                "entity": [1],
                                                "feature": 1,
                                            },
                                            "serverAddress": {
                                                "device": "LOOPBACK-SERVER",
                                                "entity": [1],
                                                "feature": 3,
                                            },
                                            "serverFeatureType": "DeviceDiagnosis",
                                        }
                                    }
                                }
                            ],
                        )
                    )

                    seen_result = False
                    heartbeat: dict[str, object] | None = None
                    for _ in range(3):
                        datagram = await session.receive_datagram(timeout=1.0)
                        command_names = {key for command in extract_commands(datagram) for key in command}
                        if "resultData" in command_names:
                            seen_result = True
                        if "deviceDiagnosisHeartbeatData" in command_names:
                            notify_commands = extract_commands(datagram)
                            heartbeat = notify_commands[0]["deviceDiagnosisHeartbeatData"]
                        if seen_result and heartbeat is not None:
                            break

                    self.assertTrue(seen_result)
                    self.assertIsNotNone(heartbeat)
                    assert heartbeat is not None
                    self.assertEqual(heartbeat["heartbeatTimeout"], "PT10S")
                    self.assertEqual(heartbeat["heartbeatCounter"], 1)
                finally:
                    await session.close()
            finally:
                await listener.stop()


if __name__ == "__main__":
    unittest.main()
