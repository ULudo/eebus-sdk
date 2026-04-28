"""Server-side SPINE profile state and payload builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._spine_helpers import (
    entity_description,
    feature_description,
    scaled_number,
    supported_partial_read,
    supported_partial_read_write,
    supported_read,
    supported_read_partial_write,
    utc_timestamp,
)
from .identity import IdentityMaterial


@dataclass(slots=True)
class ServerSpineProfile:
    identity: IdentityMaterial
    ship_id: str
    device_id: str
    profile: str = "default"
    heartbeat_timeout: str = "PT4S"
    heartbeat_counter: int = 0
    load_control_limit_payload: dict[str, Any] = field(init=False)
    device_configuration_payload: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        if self.profile not in {"default", "cls-load-power"}:
            raise ValueError(f"unsupported server SPINE profile: {self.profile}")
        self.load_control_limit_payload = self.default_load_control_limit_payload()
        self.device_configuration_payload = self.default_device_configuration_payload()

    def local_device(self) -> str:
        return f"d:_n:{self.ship_id.replace('-', '_', 1)}"

    def detailed_discovery(self) -> dict[str, Any]:
        local_device = self.local_device()
        if self.profile == "cls-load-power":
            return self._cls_load_power_detailed_discovery(local_device)
        return {
            "specificationVersionList": {"specificationVersion": ["1.3.0"]},
            "deviceInformation": {
                "description": {
                    "deviceAddress": {"device": local_device},
                    "deviceType": "EnergyManagementSystem",
                    "networkFeatureSet": "smart",
                }
            },
            "entityInformation": [
                entity_description(device=local_device, entity=0, entity_type="DeviceInformation"),
                entity_description(device=local_device, entity=1, entity_type="CEM"),
            ],
            "featureInformation": [
                feature_description(
                    device=local_device,
                    entity=0,
                    feature=0,
                    feature_type="NodeManagement",
                    role="special",
                    supported_function=[
                        supported_read("nodeManagementDetailedDiscoveryData"),
                        supported_read("nodeManagementUseCaseData"),
                        supported_read("nodeManagementSubscriptionData"),
                        {"function": "nodeManagementSubscriptionRequestCall"},
                        {"function": "nodeManagementSubscriptionDeleteCall"},
                        supported_read("nodeManagementDestinationListData"),
                        supported_read("nodeManagementBindingData"),
                        {"function": "nodeManagementBindingRequestCall"},
                        {"function": "nodeManagementBindingDeleteCall"},
                    ],
                ),
                feature_description(
                    device=local_device,
                    entity=0,
                    feature=1,
                    feature_type="DeviceClassification",
                    role="server",
                    supported_function=[supported_read("deviceClassificationManufacturerData")],
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=1,
                    feature_type="DeviceDiagnosis",
                    role="client",
                    description="DeviceDiagnosis Client",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=2,
                    feature_type="LoadControl",
                    role="server",
                    supported_function=[
                        supported_read("loadControlLimitDescriptionListData"),
                        supported_read_partial_write("loadControlLimitListData"),
                    ],
                    description="LoadControl Server",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=3,
                    feature_type="DeviceConfiguration",
                    role="server",
                    supported_function=[
                        supported_read("deviceConfigurationKeyValueDescriptionListData"),
                        supported_read_partial_write("deviceConfigurationKeyValueListData"),
                    ],
                    description="DeviceConfiguration Server",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=4,
                    feature_type="DeviceDiagnosis",
                    role="server",
                    supported_function=[supported_read("deviceDiagnosisHeartbeatData")],
                    description="DeviceDiagnosis Server",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=5,
                    feature_type="ElectricalConnection",
                    role="server",
                    supported_function=[supported_read("electricalConnectionCharacteristicListData")],
                    description="ElectricalConnection Server",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=6,
                    feature_type="LoadControl",
                    role="client",
                    description="LoadControl Client",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=7,
                    feature_type="DeviceConfiguration",
                    role="client",
                    description="DeviceConfiguration Client",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=8,
                    feature_type="ElectricalConnection",
                    role="client",
                    description="ElectricalConnection Client",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=9,
                    feature_type="Measurement",
                    role="client",
                    description="Measurement Client",
                ),
            ],
        }

    def use_case_data(self) -> dict[str, Any]:
        lpc_lpp_support = [
            {
                "useCaseName": "limitationOfPowerConsumption",
                "useCaseVersion": "1.0.0",
                "useCaseAvailable": True,
                "scenarioSupport": [1, 2, 3, 4],
                "useCaseDocumentSubRevision": "release",
            },
            {
                "useCaseName": "limitationOfPowerProduction",
                "useCaseVersion": "1.0.0",
                "useCaseAvailable": True,
                "scenarioSupport": [1, 2, 3, 4],
                "useCaseDocumentSubRevision": "release",
            },
        ]
        if self.profile == "cls-load-power":
            return {
                "useCaseInformation": [
                    {
                        "address": {"device": self.local_device(), "entity": [1]},
                        "actor": "ControllableSystem",
                        "useCaseSupport": list(lpc_lpp_support),
                    },
                    {
                        "address": {"device": self.local_device(), "entity": [6]},
                        "actor": "GridConnectionPoint",
                        "useCaseSupport": [
                            {
                                "useCaseName": "monitoringOfGridConnectionPoint",
                                "useCaseVersion": "1.0.0",
                                "useCaseAvailable": True,
                                "scenarioSupport": [1, 2, 3, 4, 5, 6, 7],
                                "useCaseDocumentSubRevision": "release",
                            }
                        ],
                    },
                ]
            }
        local_cem_address = {"device": self.local_device(), "entity": [1]}
        return {
            "useCaseInformation": [
                {
                    "address": local_cem_address,
                    "actor": "ControllableSystem",
                    "useCaseSupport": list(lpc_lpp_support),
                },
                {
                    "address": local_cem_address,
                    "actor": "EnergyGuard",
                    "useCaseSupport": list(lpc_lpp_support),
                },
                {
                    "address": local_cem_address,
                    "actor": "MonitoringAppliance",
                    "useCaseSupport": [
                        {
                            "useCaseName": "monitoringOfGridConnectionPoint",
                            "useCaseVersion": "1.0.0",
                            "useCaseAvailable": True,
                            "scenarioSupport": [1, 2, 3, 4, 5, 6, 7],
                            "useCaseDocumentSubRevision": "release",
                        }
                    ],
                },
                {
                    "address": local_cem_address,
                    "actor": "CEM",
                    "useCaseSupport": [
                        {
                            "useCaseName": "visualizationOfAggregatedBatteryData",
                            "useCaseVersion": "1.0.1",
                            "useCaseAvailable": True,
                            "scenarioSupport": [1, 2, 3, 4],
                            "useCaseDocumentSubRevision": "RC1",
                        },
                        {
                            "useCaseName": "visualizationOfAggregatedPhotovoltaicData",
                            "useCaseVersion": "1.0.1",
                            "useCaseAvailable": True,
                            "scenarioSupport": [1, 2, 3],
                            "useCaseDocumentSubRevision": "RC1",
                        },
                    ],
                },
            ]
        }

    @staticmethod
    def _cls_load_power_detailed_discovery(local_device: str) -> dict[str, Any]:
        return {
            "specificationVersionList": {"specificationVersion": ["1.3.0"]},
            "deviceInformation": {
                "description": {
                    "deviceAddress": {"device": local_device},
                    "deviceType": "EnergyManagementSystem",
                    "networkFeatureSet": "smart",
                }
            },
            "entityInformation": [
                entity_description(device=local_device, entity=0, entity_type="DeviceInformation"),
                entity_description(device=local_device, entity=1, entity_type="CEM"),
                entity_description(
                    device=local_device,
                    entity=6,
                    entity_type="GridConnectionPointOfPremises",
                    description="Grid Connection Point",
                ),
            ],
            "featureInformation": [
                feature_description(
                    device=local_device,
                    entity=0,
                    feature=0,
                    feature_type="NodeManagement",
                    role="special",
                    supported_function=[
                        supported_read("nodeManagementDetailedDiscoveryData"),
                        supported_read("nodeManagementUseCaseData"),
                        supported_read("nodeManagementSubscriptionData"),
                        {"function": "nodeManagementSubscriptionRequestCall"},
                        {"function": "nodeManagementSubscriptionDeleteCall"},
                        supported_read("nodeManagementDestinationListData"),
                        supported_read("nodeManagementBindingData"),
                        {"function": "nodeManagementBindingRequestCall"},
                        {"function": "nodeManagementBindingDeleteCall"},
                    ],
                ),
                feature_description(
                    device=local_device,
                    entity=0,
                    feature=1,
                    feature_type="DeviceClassification",
                    role="server",
                    supported_function=[supported_read("deviceClassificationManufacturerData")],
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=1,
                    feature_type="DeviceDiagnosis",
                    role="client",
                    description="DeviceDiagnosis Client",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=2,
                    feature_type="LoadControl",
                    role="server",
                    supported_function=[
                        supported_partial_read("loadControlLimitDescriptionListData"),
                        supported_partial_read_write("loadControlLimitListData"),
                    ],
                    description="LoadControl Server",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=3,
                    feature_type="DeviceConfiguration",
                    role="server",
                    supported_function=[
                        supported_partial_read("deviceConfigurationKeyValueDescriptionListData"),
                        supported_partial_read_write("deviceConfigurationKeyValueListData"),
                    ],
                    description="DeviceConfiguration Server",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=4,
                    feature_type="DeviceDiagnosis",
                    role="server",
                    supported_function=[supported_read("deviceDiagnosisHeartbeatData")],
                    description="DeviceDiagnosis Server",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=5,
                    feature_type="ElectricalConnection",
                    role="server",
                    supported_function=[supported_partial_read("electricalConnectionCharacteristicListData")],
                    description="ElectricalConnection Server",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=7,
                    feature_type="DeviceConfiguration",
                    role="client",
                    description="DeviceConfiguration Client",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=8,
                    feature_type="ElectricalConnection",
                    role="client",
                    description="ElectricalConnection Client",
                ),
                feature_description(
                    device=local_device,
                    entity=1,
                    feature=9,
                    feature_type="Measurement",
                    role="client",
                    description="Measurement Client",
                ),
                feature_description(
                    device=local_device,
                    entity=6,
                    feature=1,
                    feature_type="DeviceConfiguration",
                    role="server",
                    supported_function=[
                        supported_partial_read("deviceConfigurationKeyValueDescriptionListData"),
                        supported_partial_read_write("deviceConfigurationKeyValueListData"),
                    ],
                    description="DeviceConfiguration Server",
                ),
                feature_description(
                    device=local_device,
                    entity=6,
                    feature=2,
                    feature_type="ElectricalConnection",
                    role="server",
                    supported_function=[
                        supported_partial_read("electricalConnectionDescriptionListData"),
                        supported_partial_read("electricalConnectionParameterDescriptionListData"),
                    ],
                    description="ElectricalConnection Server",
                ),
                feature_description(
                    device=local_device,
                    entity=6,
                    feature=3,
                    feature_type="Measurement",
                    role="server",
                    supported_function=[
                        supported_partial_read("measurementDescriptionListData"),
                        supported_partial_read("measurementConstraintsListData"),
                        supported_partial_read("measurementListData"),
                    ],
                    description="Measurement Server",
                ),
            ],
        }

    def device_classification_data(self) -> dict[str, Any]:
        return {
            "brandName": "Open Source",
            "vendorName": "Open Source",
            "deviceName": self.identity.common_name,
            "deviceCode": self.ship_id,
            "serialNumber": self.device_id,
        }

    @staticmethod
    def load_control_limit_description_data() -> dict[str, Any]:
        return {
            "loadControlLimitDescriptionData": [
                {
                    "limitId": 0,
                    "limitType": "signDependentAbsValueLimit",
                    "limitCategory": "obligation",
                    "limitDirection": "consume",
                    "measurementId": 50,
                    "unit": "W",
                    "scopeType": "activePowerLimit",
                },
                {
                    "limitId": 1,
                    "limitType": "signDependentAbsValueLimit",
                    "limitCategory": "obligation",
                    "limitDirection": "produce",
                    "measurementId": 50,
                    "unit": "W",
                    "scopeType": "activePowerLimit",
                },
            ]
        }

    @staticmethod
    def default_load_control_limit_payload() -> dict[str, Any]:
        return {
            "loadControlLimitData": [
                {
                    "limitId": 0,
                    "isLimitChangeable": True,
                    "isLimitActive": False,
                    "value": scaled_number(4200),
                },
                {
                    "limitId": 1,
                    "isLimitChangeable": True,
                    "isLimitActive": False,
                    "value": scaled_number(-10000),
                },
            ]
        }

    @staticmethod
    def device_configuration_description_data() -> dict[str, Any]:
        return {
            "deviceConfigurationKeyValueDescriptionData": [
                {
                    "keyId": 0,
                    "keyName": "failsafeConsumptionActivePowerLimit",
                    "valueType": "scaledNumber",
                    "unit": "W",
                },
                {
                    "keyId": 1,
                    "keyName": "failsafeDurationMinimum",
                    "valueType": "duration",
                },
                {
                    "keyId": 2,
                    "keyName": "failsafeProductionActivePowerLimit",
                    "valueType": "scaledNumber",
                    "unit": "W",
                },
            ]
        }

    @staticmethod
    def default_device_configuration_payload() -> dict[str, Any]:
        return {
            "deviceConfigurationKeyValueData": [
                {
                    "keyId": 0,
                    "value": {"scaledNumber": scaled_number(4200)},
                    "isValueChangeable": True,
                },
                {
                    "keyId": 1,
                    "value": {"duration": "PT7200S"},
                    "isValueChangeable": True,
                },
                {
                    "keyId": 2,
                    "value": {"scaledNumber": scaled_number(4200)},
                    "isValueChangeable": True,
                },
            ]
        }

    def device_diagnosis_heartbeat_data(self) -> dict[str, Any]:
        self.heartbeat_counter += 1
        return {
            "timestamp": utc_timestamp(),
            "heartbeatCounter": self.heartbeat_counter,
            "heartbeatTimeout": self.heartbeat_timeout,
        }

    @staticmethod
    def electrical_connection_description_data() -> dict[str, Any]:
        return {
            "electricalConnectionDescriptionData": [
                {
                    "electricalConnectionId": 0,
                    "powerSupplyType": "ac",
                    "positiveEnergyDirection": "consume",
                }
            ]
        }

    @staticmethod
    def electrical_connection_parameter_description_data() -> dict[str, Any]:
        return {
            "electricalConnectionParameterDescriptionData": [
                {
                    "electricalConnectionId": 0,
                    "parameterId": 0,
                    "measurementId": 50,
                    "voltageType": "ac",
                    "acMeasurementType": "real",
                    "scopeType": "acPowerTotal",
                }
            ]
        }

    @staticmethod
    def electrical_connection_characteristic_data() -> dict[str, Any]:
        return {
            "electricalConnectionCharacteristicData": [
                {
                    "electricalConnectionId": 0,
                    "parameterId": 0,
                    "characteristicId": 0,
                    "characteristicContext": "entity",
                    "characteristicType": "contractualConsumptionNominalMax",
                    "value": scaled_number(32000),
                    "unit": "W",
                },
                {
                    "electricalConnectionId": 0,
                    "parameterId": 0,
                    "characteristicId": 1,
                    "characteristicContext": "entity",
                    "characteristicType": "contractualProductionNominalMax",
                    "value": scaled_number(10000),
                    "unit": "W",
                },
            ]
        }

    @staticmethod
    def measurement_description_data() -> dict[str, Any]:
        return {
            "measurementDescriptionData": [
                {
                    "measurementId": 50,
                    "measurementType": "power",
                    "commodityType": "electricity",
                    "unit": "W",
                    "scopeType": "acPowerTotal",
                }
            ]
        }

    @staticmethod
    def measurement_constraints_data() -> dict[str, Any]:
        return {
            "measurementConstraintsData": [
                {
                    "measurementId": 50,
                    "valueRangeMin": scaled_number(-32000),
                    "valueRangeMax": scaled_number(32000),
                    "valueStepSize": scaled_number(1),
                }
            ]
        }

    @staticmethod
    def measurement_data() -> dict[str, Any]:
        return {
            "measurementData": [
                {
                    "measurementId": 50,
                    "timestamp": utc_timestamp(),
                    "value": scaled_number(0),
                    "valueSource": "measuredValue",
                }
            ]
        }

    @staticmethod
    def subscription_data(subscriptions: list[dict[str, Any]]) -> dict[str, Any]:
        return {"subscriptionEntry": list(subscriptions)}

    def destination_list(self) -> dict[str, Any]:
        return {
            "nodeManagementDestinationData": [
                {
                    "deviceDescription": {
                        "deviceAddress": {"device": self.local_device()},
                        "deviceType": "EnergyManagementSystem",
                        "networkFeatureSet": "smart",
                    }
                }
            ]
        }

    @staticmethod
    def binding_data(bindings: list[dict[str, Any]]) -> dict[str, Any]:
        return {"bindingEntry": list(bindings)}
