"""Shared client-side SPINE profile helpers and payload builders."""

from __future__ import annotations

import re
from typing import Any

from ._spine_helpers import (
    entity_description,
    feature_description,
    format_duration,
    scaled_number,
    supported_partial_read,
    supported_partial_read_write,
    supported_read,
    supported_read_partial_write,
    supported_read_write,
    utc_timestamp,
)


def _sanitize_identifier(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return sanitized or "EEBUS-SDK"


class ClientProfileBaseMixin:
    __slots__ = ()

    def _uses_hems_reference_profile(self) -> bool:
        return self.profile == "hems-reference"

    def _uses_cls_adapter_profile(self) -> bool:
        return self.profile == "cls-adapter"

    def _uses_structured_server_profile(self) -> bool:
        return self.profile in {"hems-reference", "cls-adapter"}

    def _hems_reference_local_device_address(self) -> str:
        ship_id = self.identity.ship_id.replace("-", "_", 1)
        return f"d:_n:{ship_id}"

    def local_device_address(self) -> str:
        if self._uses_structured_server_profile():
            return self._hems_reference_local_device_address()
        suffix = _sanitize_identifier(self.identity.device_id)[:48]
        return f"d:_n:HEMS_PythonSDK-{suffix}"

    def build_local_detailed_discovery(self) -> dict[str, Any]:
        """Build the local SPINE detailed discovery payload exposed to the peer."""
        local_device = self.local_device_address()
        if self._uses_hems_reference_profile():
            return self._build_hems_reference_detailed_discovery(local_device)
        if self._uses_cls_adapter_profile():
            return self._build_cls_adapter_detailed_discovery(local_device)
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
                {
                    "description": {
                        "entityAddress": {"device": local_device, "entity": [0]},
                        "entityType": "DeviceInformation",
                    }
                },
                {
                    "description": {
                        "entityAddress": {"device": local_device, "entity": [1]},
                        "entityType": "CEM",
                    }
                },
            ],
            "featureInformation": [
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [0], "feature": 0},
                        "featureType": "NodeManagement",
                        "role": "special",
                        "supportedFunction": [
                            {
                                "function": "nodeManagementDetailedDiscoveryData",
                                "possibleOperations": {"read": {}},
                            },
                            {
                                "function": "nodeManagementUseCaseData",
                                "possibleOperations": {"read": {}},
                            },
                        ],
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [0], "feature": 1},
                        "featureType": "DeviceClassification",
                        "role": "server",
                        "supportedFunction": [
                            {
                                "function": "deviceClassificationManufacturerData",
                                "possibleOperations": {"read": {}},
                            }
                        ],
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 1},
                        "featureType": "ElectricalConnection",
                        "role": "client",
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 2},
                        "featureType": "Measurement",
                        "role": "client",
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 3},
                        "featureType": "LoadControl",
                        "role": "client",
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 4},
                        "featureType": "DeviceDiagnosis",
                        "role": "client",
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 5},
                        "featureType": "DeviceConfiguration",
                        "role": "client",
                    }
                },
            ],
        }

    def build_local_destination_list(self) -> dict[str, Any]:
        return {
            "nodeManagementDestinationData": [
                {
                    "deviceDescription": {
                        "deviceAddress": {"device": self.local_device_address()},
                        "deviceType": "EnergyManagementSystem",
                        "networkFeatureSet": "smart",
                    }
                }
            ]
        }

    @staticmethod
    def _profile_supported_read(function_name: str) -> dict[str, Any]:
        return supported_read(function_name)

    @staticmethod
    def _profile_supported_read_write(function_name: str) -> dict[str, Any]:
        return supported_read_write(function_name)

    @staticmethod
    def _profile_supported_read_partial_write(function_name: str) -> dict[str, Any]:
        return supported_read_partial_write(function_name)

    @staticmethod
    def _profile_supported_partial_read(function_name: str) -> dict[str, Any]:
        return supported_partial_read(function_name)

    @staticmethod
    def _profile_supported_partial_read_write(function_name: str) -> dict[str, Any]:
        return supported_partial_read_write(function_name)

    def _profile_feature_description(
        self,
        *,
        device: str,
        entity: int,
        feature: int,
        feature_type: str,
        role: str,
        supported_function: list[dict[str, Any]] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        return feature_description(
            device=device,
            entity=entity,
            feature=feature,
            feature_type=feature_type,
            role=role,
            supported_function=supported_function,
            description=description,
        )

    def _profile_entity_description(
        self,
        *,
        device: str,
        entity: int,
        entity_type: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        return entity_description(
            device=device,
            entity=entity,
            entity_type=entity_type,
            description=description,
        )

    @staticmethod
    def _utc_timestamp() -> str:
        return utc_timestamp()

    @staticmethod
    def _scaled_number(value: int, scale: int = 0) -> dict[str, int]:
        return scaled_number(value, scale)

    @staticmethod
    def _format_duration(seconds: int) -> str:
        return format_duration(seconds)

    def _ensure_profile_runtime_defaults(self) -> None:
        if self._profile_load_control_limit_payload is None:
            if self._uses_cls_adapter_profile():
                limit_data = [
                    {
                        "limitId": 0,
                        "isLimitChangeable": True,
                        "isLimitActive": False,
                        "value": self._scaled_number(4200),
                    }
                ]
                limit_data.append(
                    {
                        "limitId": 1,
                        "isLimitChangeable": True,
                        "isLimitActive": False,
                        "value": self._scaled_number(-10000),
                    }
                )
            else:
                limit_data = [
                    {
                        "limitId": 0,
                        "isLimitChangeable": True,
                        "isLimitActive": False,
                        "timePeriod": {"endTime": self._format_duration(7200)},
                        "value": self._scaled_number(4200),
                    }
                ]
                limit_data.append(
                    {
                        "limitId": 1,
                        "isLimitChangeable": True,
                        "isLimitActive": False,
                        "timePeriod": {"endTime": self._format_duration(7200)},
                        "value": self._scaled_number(10000),
                    }
                )
            self._profile_load_control_limit_payload = {"loadControlLimitData": limit_data}
        if self._profile_device_configuration_payload is None:
            if self._uses_cls_adapter_profile():
                key_values = [
                    {
                        "keyId": 0,
                        "value": {"scaledNumber": self._scaled_number(4200)},
                        "isValueChangeable": True,
                    },
                    {
                        "keyId": 1,
                        "value": {"duration": "PT7200S"},
                        "isValueChangeable": True,
                    },
                    {
                        "keyId": 2,
                        "value": {"scaledNumber": self._scaled_number(4200)},
                        "isValueChangeable": True,
                    },
                ]
            else:
                key_values = [
                    {
                        "keyId": 0,
                        "value": {"scaledNumber": self._scaled_number(4200)},
                        "isValueChangeable": True,
                    },
                    {
                        "keyId": 1,
                        "value": {"duration": "PT2H"},
                        "isValueChangeable": True,
                    },
                ]
                key_values.append(
                    {
                        "keyId": 2,
                        "value": {"scaledNumber": self._scaled_number(4200)},
                        "isValueChangeable": True,
                    }
                )
            self._profile_device_configuration_payload = {
                "deviceConfigurationKeyValueData": key_values
            }

    def _profile_device_classification_data(self) -> dict[str, Any]:
        if self._uses_cls_adapter_profile():
            ship_id = self.identity.ship_id
            vendor_name = "HEMS"
            serial_number = self.identity.device_id
            parts = ship_id.split("-", 2)
            if len(parts) == 3:
                vendor_name = parts[0]
                serial_number = parts[2]
            return {
                "deviceName": ship_id,
                "deviceCode": ship_id,
                "serialNumber": serial_number,
                "vendorName": vendor_name,
                "brandName": vendor_name,
                "manufacturerNodeIdentification": self.identity.common_name,
                "powerSource": "mains3Phase",
            }
        return {
            "brandName": "Open Source",
            "vendorName": "Open Source",
            "deviceName": self.identity.common_name,
            "deviceCode": self.identity.ship_id,
            "serialNumber": self.identity.device_id,
        }

    def _profile_use_case_data(self) -> dict[str, Any]:
        if self._uses_cls_adapter_profile():
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
            local_cem_address = {"device": self.local_device_address(), "entity": [1]}
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
        return {
            "useCaseInformation": [
                {
                    "address": {"device": self.local_device_address(), "entity": [1]},
                    "actor": "ControllableSystem",
                    "useCaseSupport": [
                        {
                            "useCaseName": "limitationOfPowerConsumption",
                            "useCaseVersion": "1.0.0",
                            "useCaseAvailable": True,
                            "scenarioSupport": [1, 2, 3, 4],
                        },
                        {
                            "useCaseName": "limitationOfPowerProduction",
                            "useCaseVersion": "1.0.0",
                            "useCaseAvailable": True,
                            "scenarioSupport": [1, 2, 3, 4],
                        },
                        {
                            "useCaseName": "monitoringOfGridConnectionPoint",
                            "useCaseVersion": "1.0.0",
                            "useCaseAvailable": True,
                            "scenarioSupport": [1],
                        },
                    ],
                }
            ]
        }

    def _profile_load_control_limit_description_data(self) -> dict[str, Any]:
        if self._uses_cls_adapter_profile():
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
                    }
                ]
            }
        return {
            "loadControlLimitDescriptionData": [
                {
                    "limitId": 0,
                    "limitType": "signDependentAbsValueLimit",
                    "limitCategory": "obligation",
                    "limitDirection": "consume",
                    "measurementId": 0,
                    "unit": "W",
                    "scopeType": "activePowerLimit",
                    "label": "Consumption Limit",
                },
                {
                    "limitId": 1,
                    "limitType": "signDependentAbsValueLimit",
                    "limitCategory": "obligation",
                    "limitDirection": "produce",
                    "measurementId": 0,
                    "unit": "W",
                    "scopeType": "activePowerLimit",
                    "label": "Production Limit",
                },
            ]
        }

    def _profile_device_configuration_key_value_description_data(
        self,
        address: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._uses_cls_adapter_profile():
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

    def _profile_device_configuration_key_value_data(
        self,
        address: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        self._ensure_profile_runtime_defaults()
        return self._profile_device_configuration_payload

    def _profile_device_diagnosis_heartbeat_data(self) -> dict[str, Any]:
        self._profile_heartbeat_counter += 1
        if self._uses_cls_adapter_profile():
            return {
                "heartbeatCounter": self._profile_heartbeat_counter,
                "heartbeatTimeout": "PT4S",
            }
        return {
            "timestamp": self._utc_timestamp(),
            "heartbeatCounter": self._profile_heartbeat_counter,
            "heartbeatTimeout": "PT2M",
        }

    def _profile_electrical_connection_characteristic_data(self) -> dict[str, Any]:
        if self._uses_cls_adapter_profile():
            return {
                "electricalConnectionCharacteristicData": [
                    {
                        "electricalConnectionId": 0,
                        "parameterId": 0,
                        "characteristicId": 0,
                        "characteristicContext": "entity",
                        "characteristicType": "contractualConsumptionNominalMax",
                        "value": self._scaled_number(32000),
                        "unit": "W",
                    },
                    {
                        "electricalConnectionId": 0,
                        "parameterId": 0,
                        "characteristicId": 1,
                        "characteristicContext": "entity",
                        "characteristicType": "contractualProductionNominalMax",
                        "value": self._scaled_number(10000),
                        "unit": "W",
                    }
                ]
            }
        return {
            "electricalConnectionCharacteristicData": [
                {
                    "electricalConnectionId": 0,
                    "parameterId": 0,
                    "characteristicId": 0,
                    "characteristicContext": "entity",
                    "characteristicType": "contractualConsumptionNominalMax",
                    "value": self._scaled_number(32000),
                    "unit": "W",
                },
                {
                    "electricalConnectionId": 0,
                    "parameterId": 0,
                    "characteristicId": 1,
                    "characteristicContext": "entity",
                    "characteristicType": "contractualProductionNominalMax",
                    "value": self._scaled_number(10000),
                    "unit": "W",
                },
            ]
        }

    @staticmethod
    def _profile_entity_id(address: dict[str, Any] | None) -> int | None:
        if not isinstance(address, dict):
            return None
        entity = address.get("entity")
        if not isinstance(entity, list) or not entity:
            return None
        first = entity[0]
        return first if isinstance(first, int) else None

    def _profile_positive_energy_direction(self, address: dict[str, Any] | None) -> str:
        if self._uses_cls_adapter_profile():
            entity = self._profile_entity_id(address)
            if entity == 3:
                return "produce"
            return "consume"
        entity = self._profile_entity_id(address)
        if entity == 3:
            return "produce"
        return "consume"

    def _profile_electrical_connection_description_data(
        self,
        address: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "electricalConnectionDescriptionData": [
                {
                    "electricalConnectionId": 0,
                    "powerSupplyType": "ac",
                    "positiveEnergyDirection": self._profile_positive_energy_direction(address),
                }
            ]
        }

    def _profile_electrical_connection_parameter_description_data(
        self,
        address: dict[str, Any] | None,
    ) -> dict[str, Any]:
        measurement_id = self._profile_measurement_id(address)
        if self._uses_cls_adapter_profile():
            return {
                "electricalConnectionParameterDescriptionData": [
                    {
                        "electricalConnectionId": 0,
                        "parameterId": 0,
                        "measurementId": measurement_id,
                        "voltageType": "ac",
                        "acMeasurementType": "real",
                        "scopeType": "acPowerTotal",
                    }
                ]
            }
        entity = self._profile_entity_id(address)
        scope_type = "acPowerTotal" if entity in {2, 3, 4, 5, 6} else "acPowerTotal"
        return {
            "electricalConnectionParameterDescriptionData": [
                {
                    "electricalConnectionId": 0,
                    "parameterId": 0,
                    "measurementId": 0,
                    "voltageType": "ac",
                    "acMeasurementType": "real",
                    "scopeType": scope_type,
                }
            ]
        }

    def _profile_measurement_id(self, address: dict[str, Any] | None) -> int:
        if self._uses_cls_adapter_profile():
            entity = self._profile_entity_id(address)
            if entity in {2, 3}:
                return 0
            return 50
        return 0

    def _profile_measurement_description_data(self, address: dict[str, Any] | None) -> dict[str, Any]:
        measurement_id = self._profile_measurement_id(address)
        return {
            "measurementDescriptionData": [
                {
                    "measurementId": measurement_id,
                    "measurementType": "power",
                    "commodityType": "electricity",
                    "unit": "W",
                    "scopeType": "acPowerTotal",
                    "description": (
                        "Production power"
                        if self._profile_positive_energy_direction(address) == "produce"
                        else "Consumption power"
                    ),
                }
            ]
        }

    def _profile_measurement_constraints_data(self, address: dict[str, Any] | None) -> dict[str, Any]:
        measurement_id = self._profile_measurement_id(address)
        return {
            "measurementConstraintsData": [
                {
                    "measurementId": measurement_id,
                    "valueRangeMin": self._scaled_number(-32000),
                    "valueRangeMax": self._scaled_number(32000),
                    "valueStepSize": self._scaled_number(1),
                }
            ]
        }

    def _profile_measurement_data(self, address: dict[str, Any] | None) -> dict[str, Any]:
        direction = self._profile_positive_energy_direction(address)
        value = -3500 if direction == "produce" else 0
        measurement_id = self._profile_measurement_id(address)
        return {
            "measurementData": [
                {
                    "measurementId": measurement_id,
                    "timestamp": self._utc_timestamp(),
                    "value": self._scaled_number(value),
                    "valueSource": "measuredValue",
                }
            ]
        }
