"""HEMS-reference client-side SPINE profile discovery payloads."""

from __future__ import annotations

from typing import Any


class HemsReferenceProfileMixin:
    __slots__ = ()

    def _build_hems_reference_detailed_discovery(self, local_device: str) -> dict[str, Any]:
        entity_information = [
            self._profile_entity_description(device=local_device, entity=0, entity_type="DeviceInformation"),
            self._profile_entity_description(device=local_device, entity=1, entity_type="CEM"),
            self._profile_entity_description(
                device=local_device,
                entity=2,
                entity_type="EVSE",
                description="Wallbox 22kW",
            ),
            self._profile_entity_description(
                device=local_device,
                entity=3,
                entity_type="Inverter",
                description="PV Inverter 10kW",
            ),
            self._profile_entity_description(
                device=local_device,
                entity=4,
                entity_type="Battery",
                description="Battery 14kWh",
            ),
            self._profile_entity_description(
                device=local_device,
                entity=5,
                entity_type="Compressor",
                description="Heat Pump 8kW",
            ),
            self._profile_entity_description(
                device=local_device,
                entity=6,
                entity_type="GridConnectionPointOfPremises",
                description="Grid Connection Point",
            ),
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
                    self._profile_supported_read_write("loadControlLimitListData"),
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
                    self._profile_supported_read_write("deviceConfigurationKeyValueListData"),
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
                supported_function=[self._profile_supported_read("electricalConnectionCharacteristicListData")],
                description="ElectricalConnection Server",
            ),
        ]

        for entity in (2, 3, 4, 5):
            feature_information.append(
                self._profile_feature_description(
                    device=local_device,
                    entity=entity,
                    feature=1,
                    feature_type="ElectricalConnection",
                    role="server",
                    supported_function=[
                        self._profile_supported_read("electricalConnectionDescriptionListData"),
                        self._profile_supported_read("electricalConnectionParameterDescriptionListData"),
                    ],
                    description="ElectricalConnection Server",
                )
            )
            feature_information.append(
                self._profile_feature_description(
                    device=local_device,
                    entity=entity,
                    feature=2,
                    feature_type="Measurement",
                    role="server",
                    supported_function=[
                        self._profile_supported_read("measurementDescriptionListData"),
                        self._profile_supported_read("measurementConstraintsListData"),
                        self._profile_supported_read("measurementListData"),
                    ],
                    description="Measurement Server",
                )
            )

        feature_information.extend(
            [
                self._profile_feature_description(
                    device=local_device,
                    entity=6,
                    feature=1,
                    feature_type="DeviceConfiguration",
                    role="server",
                    supported_function=[
                        self._profile_supported_read("deviceConfigurationKeyValueDescriptionListData"),
                        self._profile_supported_read_write("deviceConfigurationKeyValueListData"),
                    ],
                    description="DeviceConfiguration Server",
                ),
                self._profile_feature_description(
                    device=local_device,
                    entity=6,
                    feature=2,
                    feature_type="ElectricalConnection",
                    role="server",
                    supported_function=[
                        self._profile_supported_read("electricalConnectionDescriptionListData"),
                        self._profile_supported_read("electricalConnectionParameterDescriptionListData"),
                    ],
                    description="ElectricalConnection Server",
                ),
                self._profile_feature_description(
                    device=local_device,
                    entity=6,
                    feature=3,
                    feature_type="Measurement",
                    role="server",
                    supported_function=[
                        self._profile_supported_read("measurementDescriptionListData"),
                        self._profile_supported_read("measurementConstraintsListData"),
                        self._profile_supported_read("measurementListData"),
                    ],
                    description="Measurement Server",
                ),
            ]
        )

        return {
            "specificationVersionList": {"specificationVersion": ["1.3.0"]},
            "deviceInformation": {
                "description": {
                    "deviceAddress": {"device": local_device},
                    "deviceType": "EnergyManagementSystem",
                }
            },
            "entityInformation": entity_information,
            "featureInformation": feature_information,
        }
