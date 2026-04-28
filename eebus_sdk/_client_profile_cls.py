"""CLS-adapter client-side SPINE profile discovery payloads."""

from __future__ import annotations

from typing import Any


class ClsAdapterProfileMixin:
    __slots__ = ()

    def _build_cls_adapter_detailed_discovery(self, local_device: str) -> dict[str, Any]:
        entity_information = [
            self._profile_entity_description(device=local_device, entity=0, entity_type="DeviceInformation"),
            self._profile_entity_description(device=local_device, entity=1, entity_type="CEM"),
        ]

        feature_information = [
            self._profile_feature_description(
                device=local_device,
                entity=0,
                feature=0,
                feature_type="NodeManagement",
                role="special",
                supported_function=[
                    self._profile_supported_read("nodeManagementDetailedDiscoveryData"),
                    self._profile_supported_read("nodeManagementUseCaseData"),
                    self._profile_supported_read("nodeManagementSubscriptionData"),
                    {"function": "nodeManagementSubscriptionRequestCall"},
                    {"function": "nodeManagementSubscriptionDeleteCall"},
                    self._profile_supported_read("nodeManagementDestinationListData"),
                    self._profile_supported_read("nodeManagementBindingData"),
                    {"function": "nodeManagementBindingRequestCall"},
                    {"function": "nodeManagementBindingDeleteCall"},
                ],
            ),
            self._profile_feature_description(
                device=local_device,
                entity=0,
                feature=1,
                feature_type="DeviceClassification",
                role="server",
                supported_function=[self._profile_supported_read("deviceClassificationManufacturerData")],
            ),
            self._profile_feature_description(
                device=local_device,
                entity=1,
                feature=1,
                feature_type="DeviceDiagnosis",
                role="client",
                description="DeviceDiagnosis Client",
            ),
            self._profile_feature_description(
                device=local_device,
                entity=1,
                feature=2,
                feature_type="LoadControl",
                role="server",
                supported_function=[
                    self._profile_supported_read("loadControlLimitDescriptionListData"),
                    self._profile_supported_read_partial_write("loadControlLimitListData"),
                ],
                description="LoadControl Server",
            ),
            self._profile_feature_description(
                device=local_device,
                entity=1,
                feature=3,
                feature_type="DeviceConfiguration",
                role="server",
                supported_function=[
                    self._profile_supported_read("deviceConfigurationKeyValueDescriptionListData"),
                    self._profile_supported_read_partial_write("deviceConfigurationKeyValueListData"),
                ],
                description="DeviceConfiguration Server",
            ),
            self._profile_feature_description(
                device=local_device,
                entity=1,
                feature=4,
                feature_type="DeviceDiagnosis",
                role="server",
                supported_function=[self._profile_supported_read("deviceDiagnosisHeartbeatData")],
                description="DeviceDiagnosis Server",
            ),
            self._profile_feature_description(
                device=local_device,
                entity=1,
                feature=5,
                feature_type="ElectricalConnection",
                role="server",
                supported_function=[
                    self._profile_supported_read("electricalConnectionCharacteristicListData"),
                ],
                description="ElectricalConnection Server",
            ),
            self._profile_feature_description(
                device=local_device,
                entity=1,
                feature=6,
                feature_type="LoadControl",
                role="client",
                description="LoadControl Client",
            ),
            self._profile_feature_description(
                device=local_device,
                entity=1,
                feature=7,
                feature_type="DeviceConfiguration",
                role="client",
                description="DeviceConfiguration Client",
            ),
            self._profile_feature_description(
                device=local_device,
                entity=1,
                feature=8,
                feature_type="ElectricalConnection",
                role="client",
                description="ElectricalConnection Client",
            ),
            self._profile_feature_description(
                device=local_device,
                entity=1,
                feature=9,
                feature_type="Measurement",
                role="client",
                description="Measurement Client",
            ),
        ]

        return {
            "specificationVersionList": {"specificationVersion": ["1.3.0"]},
            "deviceInformation": {
                "description": {
                    "deviceAddress": {"device": local_device},
                    "deviceType": "EnergyManagementSystem",
                    "networkFeatureSet": "smart",
                }
            },
            "entityInformation": entity_information,
            "featureInformation": feature_information,
        }
