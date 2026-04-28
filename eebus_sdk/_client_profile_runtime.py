"""Client-side SPINE read/write/subscription runtime helpers."""

from __future__ import annotations

from typing import Any

from ._spine_helpers import merge_keyed_list_payload, normalize_feature_address


class ClientProfileRuntimeMixin:
    __slots__ = ()

    def _profile_reply_payload_for_read(
        self,
        command_name: str,
        address: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self._uses_structured_server_profile():
            return None
        self._ensure_profile_runtime_defaults()
        reply_map = {
            "nodeManagementDetailedDiscoveryData": self.build_local_detailed_discovery(),
            "nodeManagementUseCaseData": self._profile_use_case_data(),
            "nodeManagementDestinationListData": self.build_local_destination_list(),
            "nodeManagementSubscriptionData": {"subscriptionEntry": list(self._profile_subscriptions)},
            "nodeManagementBindingData": {"bindingEntry": list(self._profile_bindings)},
            "deviceClassificationManufacturerData": self._profile_device_classification_data(),
            "loadControlLimitDescriptionListData": self._profile_load_control_limit_description_data(),
            "loadControlLimitListData": self._profile_load_control_limit_payload,
            "deviceConfigurationKeyValueDescriptionListData": self._profile_device_configuration_key_value_description_data(address),
            "deviceConfigurationKeyValueListData": self._profile_device_configuration_key_value_data(address),
            "deviceDiagnosisHeartbeatData": self._profile_device_diagnosis_heartbeat_data(),
            "electricalConnectionCharacteristicListData": self._profile_electrical_connection_characteristic_data(),
            "electricalConnectionDescriptionListData": self._profile_electrical_connection_description_data(address),
            "electricalConnectionParameterDescriptionListData": self._profile_electrical_connection_parameter_description_data(address),
            "measurementDescriptionListData": self._profile_measurement_description_data(address),
            "measurementConstraintsListData": self._profile_measurement_constraints_data(address),
            "measurementListData": self._profile_measurement_data(address),
        }
        payload = reply_map.get(command_name)
        if payload is None:
            return None
        return {command_name: payload}

    @staticmethod
    def _merge_keyed_list_payload(
        current: dict[str, Any] | list[Any] | None,
        incoming: Any,
        *,
        list_key: str,
        id_key: str,
    ) -> Any:
        return merge_keyed_list_payload(current, incoming, list_key=list_key, id_key=id_key)

    def _record_profile_write_data(self, commands: list[dict[str, Any]]) -> None:
        self._ensure_profile_runtime_defaults()
        for command in commands:
            if "loadControlLimitListData" in command:
                self._profile_load_control_limit_payload = self._merge_keyed_list_payload(
                    self._profile_load_control_limit_payload,
                    command["loadControlLimitListData"],
                    list_key="loadControlLimitData",
                    id_key="limitId",
                )
            elif "deviceConfigurationKeyValueListData" in command:
                self._profile_device_configuration_payload = self._merge_keyed_list_payload(
                    self._profile_device_configuration_payload,
                    command["deviceConfigurationKeyValueListData"],
                    list_key="deviceConfigurationKeyValueData",
                    id_key="keyId",
                )

    def _normalize_feature_address(
        self,
        address: dict[str, Any],
        *,
        default_device: str | None = None,
    ) -> dict[str, Any]:
        return normalize_feature_address(address, default_device=default_device)

    def _record_profile_node_management_call(self, commands: list[dict[str, Any]], source_device: str | None) -> None:
        for command in commands:
            if "nodeManagementSubscriptionRequestCall" in command:
                request = command["nodeManagementSubscriptionRequestCall"]
                if isinstance(request, dict):
                    entry = request.get("subscriptionRequest")
                    if isinstance(entry, dict):
                        normalized = {
                            "subscriptionId": len(self._profile_subscriptions),
                            "clientAddress": self._normalize_feature_address(
                                entry.get("clientAddress", {}), default_device=source_device
                            ),
                            "serverAddress": self._normalize_feature_address(
                                entry.get("serverAddress", {}), default_device=self.local_device_address()
                            ),
                        }
                        self._profile_subscriptions.append(normalized)
            elif "nodeManagementBindingRequestCall" in command:
                request = command["nodeManagementBindingRequestCall"]
                if isinstance(request, dict):
                    entry = request.get("bindingRequest")
                    if isinstance(entry, dict):
                        normalized = {
                            "bindingId": len(self._profile_bindings),
                            "clientAddress": self._normalize_feature_address(
                                entry.get("clientAddress", {}), default_device=source_device
                            ),
                            "serverAddress": self._normalize_feature_address(
                                entry.get("serverAddress", {}), default_device=self.local_device_address()
                            ),
                        }
                        self._profile_bindings.append(normalized)
            elif "nodeManagementSubscriptionDeleteCall" in command:
                self._profile_subscriptions.clear()
            elif "nodeManagementBindingDeleteCall" in command:
                self._profile_bindings.clear()

    def _profile_notify_commands_for_feature_type(
        self,
        feature_type: str,
        server_address: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self._ensure_profile_runtime_defaults()
        commands: list[dict[str, Any]] = []
        if feature_type == "LoadControl" and self._profile_load_control_limit_payload is not None:
            commands.append({"loadControlLimitListData": self._profile_load_control_limit_payload})
        elif feature_type == "DeviceConfiguration":
            payload = self._profile_device_configuration_key_value_data(server_address)
            if payload is not None:
                commands.append({"deviceConfigurationKeyValueListData": payload})
        elif feature_type == "DeviceDiagnosis":
            commands.append({"deviceDiagnosisHeartbeatData": self._profile_device_diagnosis_heartbeat_data()})
        elif feature_type == "Measurement":
            commands.append({"measurementListData": self._profile_measurement_data(server_address)})
        elif feature_type == "ElectricalConnection":
            entity = self._profile_entity_id(server_address)
            feature = server_address.get("feature") if isinstance(server_address, dict) else None
            if self._uses_cls_adapter_profile() and entity == 1 and feature == 5:
                functions = ["electricalConnectionCharacteristicListData"]
            else:
                functions = [
                    "electricalConnectionDescriptionListData",
                    "electricalConnectionParameterDescriptionListData",
                ]
                if entity == 1 and feature == 5:
                    functions.append("electricalConnectionCharacteristicListData")
            for function_name in functions:
                payload = self._profile_reply_payload_for_read(function_name, server_address)
                if payload is not None:
                    commands.append(payload)
        return commands
