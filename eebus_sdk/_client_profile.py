"""Client-side SPINE profile coordinator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._client_profile_base import ClientProfileBaseMixin
from ._client_profile_cls import ClsAdapterProfileMixin
from ._client_profile_hems import HemsReferenceProfileMixin
from ._client_profile_runtime import ClientProfileRuntimeMixin
from .identity import IdentityMaterial


@dataclass(slots=True)
class ClientSpineProfile(
    ClientProfileRuntimeMixin,
    ClientProfileBaseMixin,
    HemsReferenceProfileMixin,
    ClsAdapterProfileMixin,
):
    identity: IdentityMaterial
    profile: str = "default"
    _profile_heartbeat_counter: int = field(default=0, init=False, repr=False)
    _profile_load_control_limit_payload: dict[str, Any] | list[Any] | None = field(default=None, init=False, repr=False)
    _profile_device_configuration_payload: dict[str, Any] | list[Any] | None = field(default=None, init=False, repr=False)
    _profile_bindings: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _profile_subscriptions: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
