# Bumble Integration Tests

[English](README.md) | [中文](README.zh-CN.md)

These tests run the [Bumble](https://github.com/google/bumble) Bluetooth Host stack directly in pytest's asyncio event loop. Each Bumble device receives an independent BSWorld Virtual Controller and connects to it through a Unix-socket HCI transport. No Bumble source changes or firmware image are required.

The suite covers:

- legacy advertising and passive scanning;
- targeted CW interference across all three advertising channels, spatial isolation, and post-interference recovery;
- connection establishment between a Bumble central and peripheral;
- extended periodic advertising, periodic synchronization, and periodic report reception;
- encrypted Enhanced ATT (EATT): both devices expose Server Supported Features with EATT, each records Enhanced ATT bearer support in Client Supported Features, the client opens two L2CAP credit-based EATT bearers, and concurrent GATT reads and writes on those bearers return the values written by the other bearer.

Multiple Handle Value Notification is not part of this suite. Bumble 0.0.235 defines `ClientSupportedFeatures.MULTIPLE_HANDLE_VALUE_NOTIFICATIONS`, but it does not implement the ATT Multiple Handle Value Notification PDU (opcode `0x23`). Its Generic Attribute Profile service marks that procedure as unsupported.

Installing BSWorld also installs the validated Bumble release:

```sh
python -m pip install -e .
```

Run the suite after preparing BabbleSim and installing the BSWorld binary release described in the repository README:

```sh
pytest tests/integration/bumble -n auto
```

The Applications execute inside pytest, so their timers, socket operations, and timeouts use the same simulation-aware asyncio event loop as the test. The Virtual Controllers remain independent Linux processes. Per-device BTSnoop files are stored under the test's `bumble/<role>/` log directories, and RF traffic is included in the shared BabbleSim trace.
