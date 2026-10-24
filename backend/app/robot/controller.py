"""
ESP32-C3 robot controller over BLE.

Ported from the standalone Flask prototype (``websensor+control.py``) so the same
capabilities are available through the main backend API:

  * BLE connect / disconnect to the ``ESP32C3_*`` peripheral
  * live sensor telemetry (temperature, humidity, MQ-8, MQ-5)
  * movement commands (F/B/L/R/S) with a hold-to-drive watchdog refresh
  * servo commands (U/D plus per-channel ``A<ch>:<angle>`` / ``P:`` / ``Q:``)
  * rainbow LED control (RAINBOW_ON / RAINBOW_OFF)

The BLE client runs on its own asyncio event loop in a background thread so the
connection stays alive independently of HTTP request handling.

Several boards can be connected at the same time — one :class:`RobotController`
per *role* (``servo`` for actuators, ``sensor`` for telemetry), owned by
:class:`RobotManager`.

If ``bleak`` is not installed, or ``ROBOT_MOCK`` is set, the controller falls
back to a mock mode that simulates sensor readings and accepts commands — the
same graceful-degradation approach used elsewhere in the project.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import threading
import time
from typing import Any, Callable

try:  # optional dependency — the backend must import without it
    from bleak import BleakClient, BleakScanner  # type: ignore

    _BLEAK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on host environment
    BleakClient = None  # type: ignore[assignment]
    BleakScanner = None  # type: ignore[assignment]
    _BLEAK_AVAILABLE = False

logger = logging.getLogger(__name__)

# Advertising names the ESP32-C3 firmware has used. Firmware builds differ
# (the standalone prototype was written against "ESP32C3_Sensors", but the
# board advertises "ESP32C3_Servo"), so accept any of them.
DEFAULT_DEVICE_NAMES = ("ESP32C3_Sensors", "ESP32C3_Servo")

# Roles, so two boards can be connected at the same time. The servo board takes
# actuator commands, the sensor board streams telemetry.
SERVO_ROLE = "servo"
SENSOR_ROLE = "sensor"

# Advertising name(s) per role. Each role keeps its own BLE link.
DEFAULT_ROLES: dict[str, tuple[str, ...]] = {
    SERVO_ROLE: ("ESP32C3_Servo",),
    SENSOR_ROLE: ("ESP32C3_Sensors",),
}

# Stand-in telemetry refresh interval. Only the MQ-8 module is simulated — the
# DHT11 and MQ-5 are not wired up on this rig, so the backend leaves them "--".
SIM_INTERVAL_S = 2.0

# Simulated MQ-8 behaviour. A clean room sits at a low, slowly drifting count;
# the module only climbs once it actually smells gas. The digital output drops
# to LOW above the trigger point, which is how the real DO pin behaves.
MQ8_IDLE_LOW = 180.0
MQ8_IDLE_HIGH = 520.0
MQ8_DRIFT_STEP = 25.0
MQ8_TRIGGER_COUNTS = 1500.0

SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

# Commands the firmware understands without extra arguments.
STATIC_COMMANDS = {"F", "B", "L", "R", "S", "U", "D", "RAINBOW_ON", "RAINBOW_OFF"}


def is_valid_command(command: str) -> bool:
    """Mirror the firmware's accepted command grammar."""
    if command in STATIC_COMMANDS:
        return True

    # Individual servo: A<channel>:<angle> where channel is 0..3, angle 0..180.
    if command.startswith("A") and ":" in command:
        parts = command.split(":")
        if len(parts) == 2:
            channel_text, angle_text = parts[0][1:], parts[1]
            if channel_text.isdigit() and angle_text.isdigit():
                if 0 <= int(channel_text) <= 3 and 0 <= int(angle_text) <= 180:
                    return True

    # Paired servo channels: P:<angle> or Q:<angle>, angle 0..180.
    if command.startswith(("P:", "Q:")):
        parts = command.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            if 0 <= int(parts[1]) <= 180:
                return True

    return False


def _normalize_gas(value: str) -> str:
    """Force an MQ-8/MQ-5 payload to HIGH or LOW.

    Both modules are read through their digital output, but firmware builds have
    reported either ``HIGH``/``LOW`` or the raw pin level, so numeric payloads
    are mapped with ``digitalRead`` semantics: 0 is LOW, anything else HIGH.
    """
    text = value.strip().upper()
    if text in ("HIGH", "LOW"):
        return text

    try:
        return "LOW" if float(text) == 0 else "HIGH"
    except ValueError:
        return text  # unrecognised payload — show it verbatim


def _extract_legacy_field(message: str, label: str) -> str | None:
    """Extract ``label: value`` from legacy single-line telemetry payloads."""
    marker = f"{label}:"
    start = message.find(marker)
    if start < 0:
        return None

    start += len(marker)
    while start < len(message) and message[start] == " ":
        start += 1
    end = message.find(" ", start)
    if end < 0:
        end = len(message)
    return message[start:end].strip()


def _extract_gas_field(message: str, name: str) -> str | None:
    """Extract MQ telemetry from either modern or legacy formats.

    Supported examples:
    - ``MQ8:LOW``
    - ``MQ8:LOW MQ5:HIGH``
    - ``MQ8 DO: LOW``
    - ``Temperature: DHT11 read failed MQ8 DO: LOW MQ5 DO: LOW``
    """
    match = re.search(rf"\b{name}(?:\s+DO)?\s*:\s*([A-Za-z0-9.]+)", message, re.IGNORECASE)
    if match is None:
        return None
    return match.group(1)


class RobotController:
    """Thread-safe BLE controller for one board, with a mock fallback."""

    def __init__(
        self,
        device_names: tuple[str, ...] | None = None,
        force_mock: bool | None = None,
        role: str = "",
        exclude: Callable[[], set[str]] | None = None,
        other_names: tuple[str, ...] = (),
    ) -> None:
        if device_names is None:
            env_names = os.getenv("ROBOT_DEVICE_NAME", "")
            device_names = tuple(
                name.strip() for name in env_names.split(",") if name.strip()
            ) or DEFAULT_DEVICE_NAMES

        self.device_names = tuple(device_names)
        self.device_name = self.device_names[0]
        self.role = role

        # Addresses held by sibling roles, and the names those roles match, so
        # this controller never claims another board's hardware.
        self._exclude = exclude
        self._other_names = tuple(other_names)

        if force_mock is None:
            force_mock = os.getenv("ROBOT_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}

        self.mock = bool(force_mock) or not _BLEAK_AVAILABLE

        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any = None

        # Current simulated MQ-8 analog count, so mock readings drift instead
        # of jumping to a fresh random value each time.
        self._mq8_analog = random.uniform(MQ8_IDLE_LOW, MQ8_IDLE_HIGH)

        self._state: dict[str, Any] = {
            "connected": False,
            "connecting": False,
            "status": "Not connected",
            "temperature": "--",
            "humidity": "--",
            "mq8": "--",
            "mq5": "--",
            "mq8_analog": "--",
            "mq5_analog": "--",
            "last_message": "",
            "last_command": "",
            "mock": self.mock,
            "device_name": self.device_name,
            "role": self.role,
            "address": "",
        }

        if self.mock:
            reason = "ROBOT_MOCK set" if force_mock else "bleak not installed"
            logger.info("Robot controller running in MOCK mode (%s)", reason)
            self._set(status="Not connected")

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set(self, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)

    def apply_readings(self, **readings: Any) -> None:
        """Overwrite telemetry fields from outside this controller.

        Used by :class:`RobotManager` to fill in the stand-in MQ-8 reading.
        """
        self._set(**readings)

    # ------------------------------------------------------------------
    # BLE worker loop
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _ensure_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            while self._loop is None:
                time.sleep(0.01)

    async def _submit(self, coro: Any, timeout: float = 15.0) -> Any:
        self._ensure_worker()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)

    def _fire_and_forget(self, coro: Any) -> None:
        self._ensure_worker()
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ------------------------------------------------------------------
    # Telemetry parsing
    # ------------------------------------------------------------------
    def _parse_telemetry(self, message: str) -> None:
        message = message.strip()
        updates: dict[str, Any] = {"last_message": message}

        mq8 = _extract_gas_field(message, "MQ8")
        mq5 = _extract_gas_field(message, "MQ5")
        if mq8 is not None:
            updates["mq8"] = _normalize_gas(mq8)
        if mq5 is not None:
            updates["mq5"] = _normalize_gas(mq5)

        if message.startswith("T:"):
            # Example: T:28.0C H:65.0%
            for part in message.split():
                if part.startswith("T:"):
                    updates["temperature"] = part[2:].replace("C", "")
                elif part.startswith("H:"):
                    updates["humidity"] = part[2:].replace("%", "")
        elif message.startswith("DHT:"):
            updates["temperature"] = message.split(":", 1)[1]
        else:
            # Legacy firmware sometimes emits one combined status line like:
            # "Temperature: DHT11 read failed MQ8 DO: LOW MQ5 DO: LOW"
            pass

        self._set(**updates)

    def _on_notification(self, _sender: Any, data: bytearray) -> None:
        try:
            message = data.decode("utf-8", errors="ignore")
            logger.debug("ESP32: %s", message)
            self._parse_telemetry(message)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Robot notification error: %s", exc)

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------
    async def connect(self) -> dict[str, Any]:
        current = self.state()
        if current["connected"]:
            return {"ok": True, "message": "Already connected"}
        if current["connecting"]:
            return {"ok": True, "message": "Connection in progress"}

        self._set(connecting=True)

        if self.mock:
            # Simulated boards come up on the first click — no scan delay.
            self._fire_and_forget(self._do_connect_mock())
            return {"ok": True, "message": "Connecting..."}

        self._set(status="Searching for ESP32...")
        self._fire_and_forget(self._do_connect_ble())
        return {"ok": True, "message": "Searching for ESP32..."}

    async def _do_connect_mock(self) -> None:
        self._set(
            connected=True,
            connecting=False,
            status=f"Connected to {self.device_name}",
        )
        self._emit_mock_reading()
        self._start_mock_telemetry()

    def _start_mock_telemetry(self) -> None:
        def run() -> None:
            while self.state()["connected"]:
                time.sleep(2.0)
                if not self.state()["connected"]:
                    break
                self._emit_mock_reading()

        threading.Thread(target=run, daemon=True).start()

    def _emit_mock_reading(self) -> None:
        # A clean room keeps the MQ-8 near its idle baseline, so the count just
        # drifts a little instead of leaping around. Only the MQ-8 is simulated;
        # the DHT11 and MQ-5 are not wired up and stay at "--".
        self._mq8_analog = min(
            MQ8_IDLE_HIGH,
            max(
                MQ8_IDLE_LOW,
                self._mq8_analog + random.uniform(-MQ8_DRIFT_STEP, MQ8_DRIFT_STEP),
            ),
        )
        counts = int(self._mq8_analog)
        # Digital output drops when the module is triggered, hence the inversion.
        mq8 = "LOW" if self._mq8_analog >= MQ8_TRIGGER_COUNTS else "HIGH"
        self._set(
            mq8=mq8,
            mq8_analog=str(counts),
            last_message=f"MQ8:{mq8} AO8:{counts}",
        )

    def _pick_device(self, devices: list[Any]) -> Any | None:
        """Return the ESP32 from a scan result, tolerating firmware name drift."""
        blocked = self._exclude() if self._exclude is not None else set()
        available = [
            d for d in devices if getattr(d, "address", "") not in blocked
        ]
        named = {d.name.lower(): d for d in available if d.name}

        for accepted in self.device_names:
            if accepted.lower() in named:
                return named[accepted.lower()]

        # Firmware builds have shipped under several ESP32C3_* names; if none of
        # the configured names matched, fall back to any ESP32 peripheral rather
        # than silently reporting "not found". Names belonging to a sibling role
        # are skipped so two boards are never assigned to the same role.
        reserved = {name.lower() for name in self._other_names}
        for name, device in named.items():
            if name.startswith("esp32") and name not in reserved:
                logger.info(
                    "Using ESP32 candidate %r for role %r (not an exact name match)",
                    name,
                    self.role,
                )
                return device

        return None

    @staticmethod
    def _log_gatt_tree(client: Any) -> None:
        """Log the peripheral's GATT tree — invaluable when UUIDs don't match."""
        try:
            for service in client.services:
                logger.warning("  service %s", service.uuid)
                for char in service.characteristics:
                    logger.warning(
                        "    char %s [%s]", char.uuid, ",".join(char.properties)
                    )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Could not enumerate GATT services: %s", exc)

    async def _do_connect_ble(self) -> None:
        client = None
        try:
            device = None
            attempts = 3
            for attempt in range(1, attempts + 1):
                self._set(status=f"Scanning for ESP32… (attempt {attempt}/{attempts})")
                logger.info("Scanning for %s (attempt %d)", self.device_names, attempt)

                devices = await BleakScanner.discover(timeout=8)
                device = self._pick_device(devices)

                if device is None:
                    logger.warning("ESP32 not found on attempt %d", attempt)
                    continue

                try:
                    client = BleakClient(device)
                    await client.connect()
                except Exception as exc:
                    # The first WinRT connect after a scan is often flaky
                    # ("Unreachable" / GATT discovery timeout); retry.
                    logger.warning("Connect attempt %d failed: %s", attempt, exc)
                    client = None
                    await asyncio.sleep(1.5)
                    continue

                if client.is_connected:
                    break

                client = None

            if device is None:
                self._set(
                    connecting=False,
                    status="ESP32 not found. Check power and BLE advertising.",
                )
                logger.warning("ESP32 %s not found", self.device_names)
                return

            if client is None or not client.is_connected:
                self._set(connecting=False, status="Connection failed")
                return

            self._client = client
            try:
                await client.start_notify(TX_UUID, self._on_notification)
            except Exception:
                # Some firmware builds implement the Nordic UART RX (write)
                # characteristic but never send telemetry, so no TX/notify
                # characteristic exists. Movement, servo and LED commands still
                # work — only the sensor readout is unavailable.
                logger.warning(
                    "Peripheral %s exposes no notify characteristic (%s); "
                    "continuing in command-only mode (no sensor telemetry)",
                    device.name,
                    TX_UUID,
                )
                self._log_gatt_tree(client)

            self._set(
                connected=True,
                connecting=False,
                status=f"Connected to {device.name}",
                device_name=device.name,
                address=getattr(device, "address", ""),
            )
            logger.info("Connected to %s (role=%s)", device.name, self.role or "-")

            # Keep the link alive until the peripheral drops or we disconnect.
            while client.is_connected:
                await asyncio.sleep(0.5)

        except Exception as exc:
            logger.warning("BLE connection error: %s", exc)
            self._set(status=f"BLE error: {exc}")
        finally:
            if client is not None and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            self._client = None

            status = self.state()["status"]
            self._set(
                connected=False,
                connecting=False,
                address="",
                status="Disconnected" if status.startswith("Connected") else status,
            )

    async def disconnect(self) -> dict[str, Any]:
        try:
            await self._submit(self._do_disconnect(), timeout=10)
            return {"ok": True, "message": "Disconnected"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def _do_disconnect(self) -> None:
        client = self._client

        if client is not None and getattr(client, "is_connected", False):
            try:
                # Stop the motors before dropping the link.
                await client.write_gatt_char(RX_UUID, b"S", response=False)
                await client.disconnect()
            except Exception as exc:
                logger.warning("Robot disconnect error: %s", exc)

        self._client = None
        self._set(connected=False, connecting=False, address="", status="Disconnected")

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    async def send(self, command: str) -> dict[str, Any]:
        try:
            if self.mock:
                await asyncio.sleep(0.02)
            else:
                await self._submit(self._do_send(command), timeout=10)

            self._set(last_command=command)
            return {"ok": True, "command": command}

        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def _do_send(self, command: str) -> None:
        client = self._client

        if client is None or not getattr(client, "is_connected", False):
            raise RuntimeError("ESP32 is not connected")

        await client.write_gatt_char(RX_UUID, command.encode("utf-8"), response=False)


def command_role(command: str) -> str:
    """Return the role that handles a command.

    Every command in the grammar drives an actuator (movement, servo or LED),
    so they all belong to the servo board. The sensor board is telemetry-only.
    """
    return SERVO_ROLE


class RobotManager:
    """Owns one :class:`RobotController` per role so several boards run at once.

    Boards are connected one at a time. The Windows WinRT backend is noticeably
    flakier when two GATT connections are opened in parallel, and connecting in
    sequence keeps name-to-role assignment deterministic.
    """

    def __init__(
        self,
        roles: dict[str, tuple[str, ...]] | None = None,
        force_mock: bool | None = None,
        simulate_telemetry: bool = True,
    ) -> None:
        self._roles: dict[str, tuple[str, ...]] = dict(roles or DEFAULT_ROLES)
        self._controllers: dict[str, RobotController] = {}
        self._last_command = ""
        self._connecting_thread: threading.Thread | None = None

        for role, names in self._roles.items():
            other_names = tuple(
                name
                for other_role, other in self._roles.items()
                if other_role != role
                for name in other
            )
            self._controllers[role] = RobotController(
                device_names=tuple(names),
                force_mock=force_mock,
                role=role,
                exclude=self._claimed_addresses,
                other_names=other_names,
            )

        # The MQ-8 module is only being simulated, so nothing will ever report
        # it over BLE. Fabricate a plausible value once a board connects.
        if simulate_telemetry:
            threading.Thread(target=self._sim_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(self._controllers)

    def get(self, role: str) -> RobotController | None:
        return self._controllers.get(role)

    def _claimed_addresses(self) -> set[str]:
        """Addresses already held by a connected role.

        Without this, a role whose exact name is missing could claim the *other*
        board through the generic ``esp32*`` fallback in ``_pick_device``.
        """
        claimed: set[str] = set()
        for controller in self._controllers.values():
            state = controller.state()
            address = state.get("address") or ""
            if address and state.get("connected"):
                claimed.add(address)
        return claimed

    # ------------------------------------------------------------------
    # Simulated telemetry
    # ------------------------------------------------------------------
    def _sim_loop(self) -> None:
        """Feed a stand-in MQ-8 reading while a board is connected.

        The MQ-8 module only exists in simulation, so without this the dashboard
        would sit on ``--`` forever. Room air keeps the count near its idle
        baseline, and it is only written when the board has not reported its own
        — real telemetry always wins.
        """
        analog = random.uniform(MQ8_IDLE_LOW, MQ8_IDLE_HIGH)

        while True:
            time.sleep(SIM_INTERVAL_S)

            # The fabricated reading does not come from the sensor board, so feed
            # it to whichever board is actually connected — the sensor board when
            # present, otherwise the servo board in a single-ESP setup.
            sensor = self._controllers.get(SENSOR_ROLE)
            if sensor is None or not sensor.state()["connected"]:
                sensor = next(
                    (
                        c
                        for c in self._controllers.values()
                        if c.state()["connected"]
                    ),
                    None,
                )
            if sensor is None:
                continue

            state = sensor.state()
            if state.get("mq8", "--") != "--":
                continue  # the board is reporting this sensor for real

            analog = min(
                MQ8_IDLE_HIGH, max(MQ8_IDLE_LOW, analog + random.uniform(-MQ8_DRIFT_STEP, MQ8_DRIFT_STEP))
            )

            # The module's digital output drops when it is triggered, so a high
            # analog reading pairs with LOW.
            sensor.apply_readings(
                mq8="LOW" if analog >= MQ8_TRIGGER_COUNTS else "HIGH",
                mq8_analog=str(int(analog)),
            )

    # ------------------------------------------------------------------
    # Aggregated state
    # ------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        devices: list[dict[str, Any]] = []
        for role, controller in self._controllers.items():
            state = controller.state()
            devices.append(
                {
                    "role": role,
                    "device_name": state.get("device_name", ""),
                    "address": state.get("address", ""),
                    "connected": bool(state.get("connected")),
                    "connecting": bool(state.get("connecting")),
                    "status": state.get("status", "Not connected"),
                    "temperature": state.get("temperature", "--"),
                    "humidity": state.get("humidity", "--"),
                    "mq8": state.get("mq8", "--"),
                    "mq5": state.get("mq5", "--"),
                    "mq8_analog": state.get("mq8_analog", "--"),
                    "mq5_analog": state.get("mq5_analog", "--"),
                }
            )

        connected = [d for d in devices if d["connected"]]
        if connected:
            status = f"{len(connected)}/{len(devices)} devices connected"
        else:
            # Surface the most informative per-device message ("not found",
            # "BLE error: …") instead of a bare "Not connected".
            status = next(
                (
                    d["status"]
                    for d in devices
                    if d["status"] and d["status"] not in {"Not connected", "Disconnected"}
                ),
                "Not connected",
            )

        # Telemetry prefers the sensor board, but falls back to any board that is
        # actually reporting (a single-ESP setup, or the servo sketch's DHT11).
        ordered = sorted(devices, key=lambda d: d["role"] != SENSOR_ROLE)

        def pick(field: str) -> str:
            for device in ordered:
                value = device.get(field)
                if device["connected"] and value and value != "--":
                    return str(value)
            return "--"

        names = [d["device_name"] for d in connected if d["device_name"]]

        return {
            "connected": bool(connected),
            "connecting": any(d["connecting"] for d in devices),
            "status": status,
            "temperature": pick("temperature"),
            "humidity": pick("humidity"),
            "mq8": pick("mq8"),
            "mq5": pick("mq5"),
            "mq8_analog": pick("mq8_analog"),
            "mq5_analog": pick("mq5_analog"),
            "last_message": "",
            "last_command": self._last_command,
            "mock": all(c.mock for c in self._controllers.values()),
            "device_name": ", ".join(names),
            "devices": devices,
        }

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------
    def connect_all(self) -> dict[str, Any]:
        """Connect every role. Returns immediately — poll ``state()`` for progress."""
        if self._connecting_thread is not None and self._connecting_thread.is_alive():
            return {"ok": True, "message": "Connection already in progress"}

        self._connecting_thread = threading.Thread(
            target=lambda: asyncio.run(self._connect_all_async()), daemon=True
        )
        self._connecting_thread.start()
        return {
            "ok": True,
            "message": f"Connecting {len(self._controllers)} boards…",
        }

    async def _connect_all_async(self) -> None:
        for controller in self._controllers.values():
            state = controller.state()
            if state["connected"] or state["connecting"]:
                continue
            await controller.connect()
            await self._wait_settled(controller)

    @staticmethod
    async def _wait_settled(controller: RobotController, timeout: float = 75.0) -> None:
        """Block until a controller stops scanning, so the next role starts its
        scan only after this one has claimed (or missed) its board."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not controller.state()["connecting"]:
                return
            await asyncio.sleep(0.25)

    def disconnect_all(self) -> dict[str, Any]:
        """Drop every link. Returns immediately — each server-side write is async."""
        threading.Thread(
            target=lambda: asyncio.run(self._disconnect_all_async()), daemon=True
        ).start()
        return {"ok": True, "message": "Disconnecting…"}

    async def _disconnect_all_async(self) -> None:
        for role, controller in self._controllers.items():
            try:
                await controller.disconnect()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Disconnect failed for role %s: %s", role, exc)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def resolve_target(
        self, command: str, role: str | None = None
    ) -> RobotController | None:
        """Pick the board a command should go to."""
        if role is not None:
            controller = self._controllers.get(role)
            if controller is None or not controller.state()["connected"]:
                return None
            return controller

        controller = self._controllers.get(command_role(command))
        if controller is not None and controller.state()["connected"]:
            return controller

        # Single-board setups keep working: if the servo board is offline, send
        # to whatever is connected rather than refusing outright.
        for candidate in self._controllers.values():
            if candidate.state()["connected"]:
                logger.info(
                    "Routing %r to role %r (its default role is offline)",
                    command,
                    candidate.role,
                )
                return candidate

        return None

    async def send(self, command: str, role: str | None = None) -> dict[str, Any]:
        target = self.resolve_target(command, role)
        if target is None:
            if role is not None:
                return {"ok": False, "error": f"'{role}' device is not connected"}
            return {"ok": False, "error": "No ESP32 is connected"}

        result = await target.send(command)
        if result.get("ok"):
            self._last_command = command
        return result


__all__ = [
    "RobotController",
    "RobotManager",
    "is_valid_command",
    "command_role",
    "DEFAULT_DEVICE_NAMES",
    "DEFAULT_ROLES",
    "SERVO_ROLE",
    "SENSOR_ROLE",
    "SERVICE_UUID",
    "RX_UUID",
    "TX_UUID",
]
