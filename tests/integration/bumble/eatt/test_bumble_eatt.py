# Copyright 2026 Xiaomi Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Bumble central and peripheral Enhanced ATT bearer test."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path

from bumble.att import EATT_PSM, is_enhanced_bearer
from bumble.core import UUID
from bumble.device import Device, DeviceConfiguration
from bumble.gatt import (
    GATT_CLIENT_SUPPORTED_FEATURES_CHARACTERISTIC,
    GATT_GENERIC_ATTRIBUTE_SERVICE,
    GATT_SERVER_SUPPORTED_FEATURES_CHARACTERISTIC,
    Characteristic,
    ClientSupportedFeatures,
    ServerSupportedFeatures,
    Service,
)
from bumble.gatt_client import Client
from bumble.hci import Address
from bumble.transport import open_transport
from tests.integration.bumble.common import (
    ADVERTISER_ADDRESS,
    ADVERTISER_NAME,
    OBSERVER_ADDRESS,
    TIMEOUT,
    AdvertisingType,
    OwnAddressType,
    advertising_data,
)

# Bumble 0.0.235 defines ClientSupportedFeatures.MULTIPLE_HANDLE_VALUE_NOTIFICATIONS,
# but att.Opcode has no Multiple Handle Value Notification PDU (opcode 0x23) and
# GenericAttributeProfileService marks that procedure as unsupported. This test
# therefore does not exercise it.

_SERVICE_UUID = UUID("f0f1f2f3-f4f5-4000-8000-0000000000e1")
_ALPHA_UUID = UUID("f0f1f2f3-f4f5-4000-8000-0000000000e2")
_BETA_UUID = UUID("f0f1f2f3-f4f5-4000-8000-0000000000e3")
_ALPHA_INITIAL = b"alpha-init"
_BETA_INITIAL = b"beta-init"
_ALPHA_UPDATED = b"alpha-eatt"
_BETA_UPDATED = b"beta-eatt"
_EATT_BEARER_COUNT = 2


def _eatt_service() -> Service:
    return Service(
        _SERVICE_UUID,
        [
            Characteristic(
                _ALPHA_UUID,
                Characteristic.Properties.READ | Characteristic.Properties.WRITE,
                Characteristic.Permissions.READABLE | Characteristic.Permissions.WRITEABLE,
                _ALPHA_INITIAL,
            ),
            Characteristic(
                _BETA_UUID,
                Characteristic.Properties.READ | Characteristic.Properties.WRITE,
                Characteristic.Permissions.READABLE | Characteristic.Permissions.WRITEABLE,
                _BETA_INITIAL,
            ),
        ],
    )


async def _open_eatt_device(
    stack: AsyncExitStack,
    name: str,
    address: str,
    hci_socket: str | Path,
    *,
    services: list[Service] | None = None,
) -> Device:
    transport = await stack.enter_async_context(await open_transport(f"unix:{hci_socket}"))
    device = Device.from_config_with_hci(
        DeviceConfiguration(name=name, address=Address(address), eatt_enabled=True),
        transport.source,
        transport.sink,
    )
    for service in services or []:
        device.add_service(service)
    await device.power_on()
    return device


async def _characteristic(client: Client, service_uuid: UUID, characteristic_uuid: UUID):
    service = (await client.discover_service(service_uuid))[0]
    characteristic = (
        await service.discover_characteristics([characteristic_uuid])
    )[0]
    return characteristic


def _has_eatt_support(value: bytes) -> bool:
    return bool(value) and bool(value[0] & int(ServerSupportedFeatures.EATT_SUPPORTED))


async def test_bumble_client_uses_concurrent_eatt_bearers(topo):
    peripheral_connected = asyncio.get_running_loop().create_future()
    central_encrypted = asyncio.Event()
    peripheral_encrypted = asyncio.Event()
    eatt_supported = asyncio.Event()
    eatt_features_recorded = asyncio.Event()
    eatt_bearers_open = asyncio.Event()
    initial_values_read = asyncio.Event()
    cross_bearer_values_read = asyncio.Event()

    async with AsyncExitStack() as stack:
        peripheral_ctl = topo.add_controller(
            public_address=ADVERTISER_ADDRESS,
            log_subdir=Path("bumble") / "eatt_peripheral",
        )
        central_ctl = topo.add_controller(
            public_address=OBSERVER_ADDRESS,
            log_subdir=Path("bumble") / "eatt_central",
        )
        topo.set_channel_attenuation(default_db=45.0)
        await topo.start()

        peripheral = await _open_eatt_device(
            stack,
            "eatt_peripheral",
            ADVERTISER_ADDRESS,
            peripheral_ctl.hci_sock,
            services=[_eatt_service()],
        )
        central = await _open_eatt_device(
            stack,
            "eatt_central",
            OBSERVER_ADDRESS,
            central_ctl.hci_sock,
        )
        peripheral.on(
            "connection",
            lambda connection: peripheral_connected.set_result(connection)
            if not peripheral_connected.done()
            else None,
        )

        await peripheral.start_advertising(
            advertising_type=AdvertisingType.UNDIRECTED_CONNECTABLE_SCANNABLE,
            own_address_type=OwnAddressType.PUBLIC,
            advertising_data=advertising_data(ADVERTISER_NAME),
            advertising_interval_min=20.0,
            advertising_interval_max=20.0,
        )
        central_connection = None
        try:
            central_connection = await central.connect(
                peripheral.public_address,
                timeout=TIMEOUT,
            )
            peripheral_connection = await asyncio.wait_for(peripheral_connected, timeout=TIMEOUT)
            assert central_connection.handle is not None

            central_connection.on(
                central_connection.EVENT_CONNECTION_ENCRYPTION_CHANGE,
                lambda: central_encrypted.set() if central_connection.is_encrypted else None,
            )
            peripheral_connection.on(
                peripheral_connection.EVENT_CONNECTION_ENCRYPTION_CHANGE,
                lambda: peripheral_encrypted.set() if peripheral_connection.is_encrypted else None,
            )
            await central_connection.pair()
            await asyncio.wait_for(central_encrypted.wait(), timeout=TIMEOUT)
            await asyncio.wait_for(peripheral_encrypted.wait(), timeout=TIMEOUT)

            central_client = central_connection.gatt_client
            peripheral_client = peripheral_connection.gatt_client
            central_server_features, peripheral_server_features = await asyncio.gather(
                _characteristic(
                    central_client,
                    GATT_GENERIC_ATTRIBUTE_SERVICE,
                    GATT_SERVER_SUPPORTED_FEATURES_CHARACTERISTIC,
                ),
                _characteristic(
                    peripheral_client,
                    GATT_GENERIC_ATTRIBUTE_SERVICE,
                    GATT_SERVER_SUPPORTED_FEATURES_CHARACTERISTIC,
                ),
            )
            central_supported, peripheral_supported = await asyncio.gather(
                central_server_features.read_value(),
                peripheral_server_features.read_value(),
            )
            if _has_eatt_support(central_supported) and _has_eatt_support(peripheral_supported):
                eatt_supported.set()
            await asyncio.wait_for(eatt_supported.wait(), timeout=TIMEOUT)

            central_client_features, peripheral_client_features = await asyncio.gather(
                _characteristic(
                    central_client,
                    GATT_GENERIC_ATTRIBUTE_SERVICE,
                    GATT_CLIENT_SUPPORTED_FEATURES_CHARACTERISTIC,
                ),
                _characteristic(
                    peripheral_client,
                    GATT_GENERIC_ATTRIBUTE_SERVICE,
                    GATT_CLIENT_SUPPORTED_FEATURES_CHARACTERISTIC,
                ),
            )
            enhanced_att = bytes([int(ClientSupportedFeatures.ENHANCED_ATT_BEARER)])
            await asyncio.gather(
                central_client_features.write_value(enhanced_att, with_response=True),
                peripheral_client_features.write_value(enhanced_att, with_response=True),
            )
            central_recorded, peripheral_recorded = await asyncio.gather(
                central_client_features.read_value(),
                peripheral_client_features.read_value(),
            )
            if central_recorded == enhanced_att and peripheral_recorded == enhanced_att:
                eatt_features_recorded.set()
            await asyncio.wait_for(eatt_features_recorded.wait(), timeout=TIMEOUT)

            alpha = await _characteristic(central_client, _SERVICE_UUID, _ALPHA_UUID)
            beta = await _characteristic(central_client, _SERVICE_UUID, _BETA_UUID)
            bearers = await Client.connect_eatt(central_connection, count=_EATT_BEARER_COUNT)
            source_cids = [bearer.bearer.source_cid for bearer in bearers]
            if (
                len(bearers) == _EATT_BEARER_COUNT
                and len(set(source_cids)) == _EATT_BEARER_COUNT
                and all(
                    is_enhanced_bearer(bearer.bearer) and bearer.bearer.psm == EATT_PSM
                    for bearer in bearers
                )
            ):
                eatt_bearers_open.set()
            await asyncio.wait_for(eatt_bearers_open.wait(), timeout=TIMEOUT)

            first_bearer, second_bearer = bearers
            initial_alpha, initial_beta = await asyncio.gather(
                first_bearer.read_value(alpha.handle),
                second_bearer.read_value(beta.handle),
            )
            if initial_alpha == _ALPHA_INITIAL and initial_beta == _BETA_INITIAL:
                initial_values_read.set()
            await asyncio.wait_for(initial_values_read.wait(), timeout=TIMEOUT)

            await asyncio.gather(
                first_bearer.write_value(alpha.handle, _ALPHA_UPDATED, with_response=True),
                second_bearer.write_value(beta.handle, _BETA_UPDATED, with_response=True),
            )
            updated_alpha, updated_beta = await asyncio.gather(
                second_bearer.read_value(alpha.handle),
                first_bearer.read_value(beta.handle),
            )
            if updated_alpha == _ALPHA_UPDATED and updated_beta == _BETA_UPDATED:
                cross_bearer_values_read.set()
            await asyncio.wait_for(cross_bearer_values_read.wait(), timeout=TIMEOUT)
        finally:
            if central_connection is not None:
                await central_connection.disconnect()
            await peripheral.stop_advertising()
