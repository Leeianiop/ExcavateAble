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

If ``bleak`` is not installed, or ``ROBOT_MOCK`` is set, the controller falls
back to a mock mode that simulates sensor readings and accepts commands — the
same graceful-degradation approach used elsewhere in the project.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from typing import Any

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


class RobotController:
    """Thread-safe BLE controller with a mock fallback."""

    def __init__(
        self,
        device_names: tuple[str, ...] | None = None,
        force_mock: bool | None = None,
    ) -> None:
        if device_names is None:
            env_names = os.getenv("ROBOT_DEVICE_NAME", "")
            device_names = tuple(
                name.strip() for name in env_names.split(",") if name.strip()
            ) or DEFAULT_DEVICE_NAMES

        self.device_names = tuple(device_names)
        self.device_name = self.device_names[0]

        if force_mock is None:
            force_mock = os.getenv("ROBOT_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}

        self.mock = bool(force_mock) or not _BLEAK_AVAILABLE

        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any = None

        self._state: dict[str, Any] = {
            "connected": False,
            "connecting": False,
            "status": "Not connected",
            "temperature": "--",
            "humidity": "--",
            "mq8": "--",
            "mq5": "--",
            "last_message": "",
            "last_command": "",
            "mock": self.mock,
            "device_name": self.device_name,
        }

        if self.mock:
            reason = "ROBOT_MOCK set" if force_mock else "bleak not installed"
            logger.info("Robot controller running in MOCK mode (%s)", reason)
            self._set(status="Mock mode — not connected")

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set(self, **kwargs: Any) -> None:
        with self._lock:
            self._state.update(kwargs)

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

        if message.startswith("T:"):
            # Example: T:28.0C H:65.0%
            for part in message.split():
                if part.startswith("T:"):
                    updates["temperature"] = part[2:].replace("C", "")
                elif part.startswith("H:"):
                    updates["humidity"] = part[2:].replace("%", "")
        elif message.startswith("MQ8:"):
            updates["mq8"] = message.split(":", 1)[1]
        elif message.startswith("MQ5:"):
            updates["mq5"] = message.split(":", 1)[1]
        elif message.startswith("DHT:"):
            updates["temperature"] = message.split(":", 1)[1]

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

        self._set(connecting=True, status="Searching for ESP32...")

        if self.mock:
            self._fire_and_forget(self._do_connect_mock())
            return {"ok": True, "message": "Mock ESP32 starting..."}

        self._fire_and_forget(self._do_connect_ble())
        return {"ok": True, "message": "Searching for ESP32..."}

    async def _do_connect_mock(self) -> None:
        await asyncio.sleep(0.8)  # pretend to scan
        self._set(
            connected=True,
            connecting=False,
            status=f"Connected to {self.device_name} (mock)",
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
        temperature = round(random.uniform(26.0, 30.0), 1)
        humidity = round(random.uniform(55.0, 70.0), 1)
        mq8 = str(random.randint(120, 320))
        mq5 = str(random.randint(90, 260))
        self._set(
            temperature=str(temperature),
            humidity=str(humidity),
            mq8=mq8,
            mq5=mq5,
            last_message=f"T:{temperature}C H:{humidity}% MQ8:{mq8} MQ5:{mq5}",
        )

    def _pick_device(self, devices: list[Any]) -> Any | None:
        """Return the ESP32 from a scan result, tolerating firmware name drift."""
        named = {d.name.lower(): d for d in devices if d.name}

        for accepted in self.device_names:
            if accepted.lower() in named:
                return named[accepted.lower()]

        # Firmware builds have shipped under several ESP32C3_* names; if none of
        # the configured names matched, fall back to any ESP32 peripheral rather
        # than silently reporting "not found".
        for name, device in named.items():
            if name.startswith("esp32"):
                logger.info("Using ESP32 candidate %r (not an exact name match)", name)
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
            )
            logger.info("Connected to %s", device.name)

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
        self._set(connected=False, connecting=False, status="Disconnected")

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


__all__ = [
    "RobotController",
    "is_valid_command",
    "DEFAULT_DEVICE_NAMES",
    "SERVICE_UUID",
    "RX_UUID",
    "TX_UUID",
]
