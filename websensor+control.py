
import asyncio
import threading
import time

from flask import Flask, jsonify, request, render_template_string
from bleak import BleakClient, BleakScanner

app = Flask(__name__)

# ==================================================
# BLE SETTINGS
# ==================================================
DEVICE_NAME = "ESP32C3_Sensors"

SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

# ==================================================
# SHARED STATE
# ==================================================
ble_loop = None
ble_thread = None
ble_client = None

ble_lock = threading.Lock()

state = {
    "connected": False,
    "connecting": False,
    "status": "Not connected",
    "temperature": "--",
    "humidity": "--",
    "mq8": "--",
    "mq5": "--",
    "last_message": "",
    "last_command": ""
}


# ==================================================
# BLE EVENT LOOP
# ==================================================
def ble_worker():
    global ble_loop

    ble_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ble_loop)
    ble_loop.run_forever()


def ensure_ble_worker():
    global ble_thread

    if ble_thread is None or not ble_thread.is_alive():
        ble_thread = threading.Thread(
            target=ble_worker,
            daemon=True
        )
        ble_thread.start()

        while ble_loop is None:
            time.sleep(0.01)


def submit_ble(coro, timeout=15):
    ensure_ble_worker()

    future = asyncio.run_coroutine_threadsafe(
        coro,
        ble_loop
    )

    return future.result(timeout=timeout)


# ==================================================
# SENSOR TELEMETRY
# ==================================================
def parse_telemetry(message):
    message = message.strip()

    with ble_lock:
        state["last_message"] = message

        # Example: T:28.0C H:65.0%
        if message.startswith("T:"):
            try:
                parts = message.split()

                for part in parts:
                    if part.startswith("T:"):
                        state["temperature"] = (
                            part[2:].replace("C", "")
                        )

                    elif part.startswith("H:"):
                        state["humidity"] = (
                            part[2:].replace("%", "")
                        )

            except (ValueError, IndexError):
                pass

        elif message.startswith("MQ8:"):
            state["mq8"] = message.split(":", 1)[1]

        elif message.startswith("MQ5:"):
            state["mq5"] = message.split(":", 1)[1]

        elif message.startswith("DHT:"):
            state["temperature"] = message.split(":", 1)[1]


def notification_handler(sender, data):
    try:
        message = data.decode(
            "utf-8",
            errors="ignore"
        )

        parse_telemetry(message)
        print("ESP32:", message)

    except Exception as exc:
        print("Notification error:", exc)


# ==================================================
# BLE CONNECTION
# ==================================================
async def find_and_connect():
    global ble_client

    with ble_lock:
        state["connecting"] = True
        state["status"] = "Searching for ESP32..."

    client = None

    try:
        print("Scanning for", DEVICE_NAME)

        devices = await BleakScanner.discover(
            timeout=8
        )

        device = next(
            (
                d for d in devices
                if d.name == DEVICE_NAME
            ),
            None
        )

        if device is None:
            with ble_lock:
                state["status"] = (
                    "ESP32 not found. Check power and BLE advertising."
                )

            print("ESP32 not found.")
            return

        client = BleakClient(device)
        await client.connect()

        if not client.is_connected:
            with ble_lock:
                state["status"] = "Connection failed"
            return

        ble_client = client

        await client.start_notify(
            TX_UUID,
            notification_handler
        )

        with ble_lock:
            state["connected"] = True
            state["status"] = "Connected to " + DEVICE_NAME

        print("Connected to", DEVICE_NAME)

        # Keep the connection alive.
        while client.is_connected:
            await asyncio.sleep(0.5)

    except Exception as exc:
        print("BLE connection error:", exc)

        with ble_lock:
            state["status"] = "BLE error: " + str(exc)

    finally:
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass

        ble_client = None

        with ble_lock:
            state["connected"] = False
            state["connecting"] = False

            if state["status"].startswith("Connected"):
                state["status"] = "Disconnected"


async def disconnect_ble():
    global ble_client

    client = ble_client

    if client is not None and client.is_connected:
        try:
            # Stop the motors before disconnecting.
            await client.write_gatt_char(
                RX_UUID,
                b"S",
                response=False
            )

            await client.disconnect()

        except Exception as exc:
            print("Disconnect error:", exc)

    with ble_lock:
        state["connected"] = False
        state["status"] = "Disconnected"


async def send_ble_command(command):
    client = ble_client

    if client is None or not client.is_connected:
        raise RuntimeError(
            "ESP32 is not connected"
        )

    await client.write_gatt_char(
        RX_UUID,
        command.encode("utf-8"),
        response=False
    )


# ==================================================
# FLASK ROUTES
# ==================================================
@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/state")
def api_state():
    with ble_lock:
        return jsonify(state)


@app.route("/api/connect", methods=["POST"])
def api_connect():
    with ble_lock:
        if state["connected"]:
            return jsonify(
                ok=True,
                message="Already connected"
            )

        if state["connecting"]:
            return jsonify(
                ok=True,
                message="Connection in progress"
            )

        state["connecting"] = True
        state["status"] = "Searching for ESP32..."

    ensure_ble_worker()

    asyncio.run_coroutine_threadsafe(
        find_and_connect(),
        ble_loop
    )

    return jsonify(
        ok=True,
        message="Searching for ESP32..."
    )


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    try:
        submit_ble(
            disconnect_ble(),
            timeout=10
        )

        return jsonify(
            ok=True,
            message="Disconnected"
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 500


@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json(silent=True) or {}

    command = str(
        data.get("command", "")
    ).strip()

    # Original movement and servo commands.
    valid = command in {
        "F", "B", "L", "R", "S", "U", "D",
        "RAINBOW_ON", "RAINBOW_OFF"
    }

    # Optional individual servo commands.
    try:
        if command.startswith("A") and ":" in command:
            parts = command.split(":")

            if len(parts) == 2:
                channel_text = parts[0][1:]
                angle_text = parts[1]

                if channel_text.isdigit() and angle_text.isdigit():
                    channel = int(channel_text)
                    angle = int(angle_text)

                    if 0 <= channel <= 3 and 0 <= angle <= 180:
                        valid = True

        # Optional paired servo commands.
        if command.startswith(("P:", "Q:")):
            parts = command.split(":")

            if len(parts) == 2 and parts[1].isdigit():
                angle = int(parts[1])

                if 0 <= angle <= 180:
                    valid = True

    except (ValueError, IndexError):
        valid = False

    if not valid:
        return jsonify(
            ok=False,
            error="Invalid command"
        ), 400

    try:
        submit_ble(
            send_ble_command(command),
            timeout=10
        )

        with ble_lock:
            state["last_command"] = command

        return jsonify(
            ok=True,
            command=command
        )

    except Exception as exc:
        return jsonify(
            ok=False,
            error=str(exc)
        ), 500


# ==================================================
# WEB PAGE
# ==================================================
HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>ESP32 Robot Controller</title>

<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family: Arial, sans-serif;
    background: #101522;
    color: #eef2ff;
}

header {
    text-align: center;
    padding: 22px 12px;
    background: #1b2436;
}

header h1 {
    margin: 0 0 8px;
}

header p {
    color: #b9c6df;
    margin: 0;
}

main {
    max-width: 950px;
    margin: auto;
    padding: 16px;
}

.panel {
    background: #1b2436;
    border: 1px solid #303d55;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 16px;
}

h2 {
    margin-top: 0;
    font-size: 20px;
}

button {
    border: 0;
    border-radius: 10px;
    padding: 13px 17px;
    color: white;
    background: #365de8;
    font-size: 15px;
    cursor: pointer;
    touch-action: manipulation;
}

button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
}

button.stop {
    background: #d8394b;
}

button.secondary {
    background: #43516b;
}

.status {
    padding: 11px;
    border-radius: 8px;
    background: #101522;
    margin: 12px 0;
    overflow-wrap: anywhere;
}

.button-row {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 10px;
    margin: 12px 0;
}

.drive-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(70px, 100px));
    gap: 10px;
    justify-content: center;
}

.drive-grid button {
    min-height: 60px;
}

.sensor-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 10px;
}

.sensor {
    background: #101522;
    border-radius: 10px;
    padding: 14px;
    overflow-wrap: anywhere;
}

.sensor .value {
    font-size: 21px;
    font-weight: bold;
    margin-top: 8px;
}

.note {
    color: #b9c6df;
    font-size: 13px;
    line-height: 1.5;
}

#toast {
    min-height: 20px;
    color: #a9c0ff;
    margin-top: 10px;
    overflow-wrap: anywhere;
}

@media (max-width: 450px) {
    main {
        padding: 10px;
    }

    .panel {
        padding: 14px;
    }
}
</style>
</head>

<body>

<header>
    <h1>🤖 Robot Controller</h1>
    <p>ESP32-C3 SuperMini · BLE Control</p>
</header>

<main>

    <!-- CONNECTION -->
    <section class="panel">
        <h2>Connection</h2>

        <div id="status" class="status">
            Not connected
        </div>

        <div class="button-row">
            <button id="connectBtn" onclick="connectRobot()">
                Connect
            </button>

            <button class="secondary" onclick="disconnectRobot()">
                Disconnect
            </button>
        </div>

        <div id="toast"></div>
    </section>

    <!-- SENSORS -->
    <section class="panel">
        <h2>Sensor Readings</h2>

        <div class="sensor-grid">

            <div class="sensor">
                Temperature
                <div class="value">
                    <span id="temperature">--</span> °C
                </div>
            </div>

            <div class="sensor">
                Humidity
                <div class="value">
                    <span id="humidity">--</span> %
                </div>
            </div>

            <div class="sensor">
                MQ-8
                <div class="value" id="mq8">--</div>
            </div>

            <div class="sensor">
                MQ-5
                <div class="value" id="mq5">--</div>
            </div>

        </div>

        <p class="note">
            Gas readings use LOW on the digital output as triggered.
            Verify this behavior for your modules.
        </p>
    </section>

    <!-- MOVEMENT -->
    <section class="panel">
        <h2>Movement</h2>

        <div class="drive-grid">

            <span></span>

            <button
                onpointerdown="driveStart('F')"
                onpointerup="driveStop()"
                onpointerleave="driveStop()"
                onpointercancel="driveStop()">
                ▲
            </button>

            <span></span>

            <button
                onpointerdown="driveStart('L')"
                onpointerup="driveStop()"
                onpointerleave="driveStop()"
                onpointercancel="driveStop()">
                ◀
            </button>

            <button class="stop" onclick="driveStop()">
                STOP
            </button>

            <button
                onpointerdown="driveStart('R')"
                onpointerup="driveStop()"
                onpointerleave="driveStop()"
                onpointercancel="driveStop()">
                ▶
            </button>

            <span></span>

            <button
                onpointerdown="driveStart('B')"
                onpointerup="driveStop()"
                onpointerleave="driveStop()"
                onpointercancel="driveStop()">
                ▼
            </button>

            <span></span>

        </div>

        <p class="note">
            Hold a direction to move. Release to stop.
        </p>
    </section>

    <!-- ORIGINAL TWO SERVO BUTTONS -->
    <section class="panel">
        <h2>Servo Control</h2>

        <p class="note">
            Use Lift to move the servo to its lifting position.
            Use Lower to return it to the home position.
        </p>

        <div class="button-row">
            <button onclick="sendCommand('U')">
                ⬆ Lift
            </button>

            <button class="secondary" onclick="sendCommand('D')">
                ⬇ Lower
            </button>
        </div>
    </section>

    <!-- RAINBOW LEDS -->
    <section class="panel">
        <h2>Rainbow LEDs</h2>

        <div class="button-row">
            <button onclick="sendCommand('RAINBOW_ON')">
                Rainbow ON
            </button>

            <button class="secondary" onclick="sendCommand('RAINBOW_OFF')">
                Rainbow OFF
            </button>
        </div>
    </section>

</main>

<script>
let connected = false;
let driveTimer = null;
let lastDriveCommand = null;

function showToast(message) {
    document.getElementById("toast").textContent = message;
}

async function sendCommand(command) {
    if (!connected) {
        showToast("Connect to the ESP32 first.");
        return false;
    }

    try {
        const response = await fetch("/api/command", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                command: command
            })
        });

        const result = await response.json();

        if (!response.ok || !result.ok) {
            showToast(result.error || "Command failed");
            return false;
        }

        showToast("Sent: " + command);
        return true;

    } catch (error) {
        showToast("Communication error: " + error.message);
        return false;
    }
}

async function connectRobot() {
    showToast("Searching for ESP32...");

    try {
        const response = await fetch("/api/connect", {
            method: "POST"
        });

        const result = await response.json();
        showToast(result.message || "Connecting...");

        await refreshState();

    } catch (error) {
        showToast("Connection request failed");
    }
}

async function disconnectRobot() {
    stopDriveTimer();
    lastDriveCommand = null;

    try {
        await fetch("/api/disconnect", {
            method: "POST"
        });
    } catch (error) {
        showToast("Disconnect request failed");
    }

    await refreshState();
}

function driveStart(command) {
    if (!connected) {
        showToast("Connect to the ESP32 first.");
        return;
    }

    lastDriveCommand = command;

    sendCommand(command);

    stopDriveTimer();

    // Refresh the firmware's 1-second motor watchdog.
    driveTimer = setInterval(() => {
        if (lastDriveCommand === command && connected) {
            sendCommand(command);
        }
    }, 250);
}

function driveStop() {
    if (lastDriveCommand === null) {
        return;
    }

    lastDriveCommand = null;
    stopDriveTimer();

    sendCommand("S");
}

function stopDriveTimer() {
    if (driveTimer !== null) {
        clearInterval(driveTimer);
        driveTimer = null;
    }
}

async function refreshState() {
    try {
        const response = await fetch("/api/state");
        const s = await response.json();

        connected = s.connected;

        document.getElementById("status").textContent =
            s.status;

        document.getElementById("connectBtn").disabled =
            s.connected || s.connecting;

        document.getElementById("temperature").textContent =
            s.temperature;

        document.getElementById("humidity").textContent =
            s.humidity;

        document.getElementById("mq8").textContent =
            s.mq8;

        document.getElementById("mq5").textContent =
            s.mq5;

        if (!connected) {
            lastDriveCommand = null;
            stopDriveTimer();
        }

    } catch (error) {
        document.getElementById("status").textContent =
            "Cannot reach Python server";
    }
}

setInterval(refreshState, 1000);
refreshState();
</script>

</body>
</html>
"""


# ==================================================
# START SERVER
# ==================================================
if __name__ == "__main__":
    ensure_ble_worker()

    print()
    print("====================================")
    print(" ESP32 Robot Controller")
    print("====================================")
    print("Open this on your computer:")
    print("http://127.0.0.1:5000")
    print()
    print("BLE device:", DEVICE_NAME)
    print("Press CTRL+C to stop the server.")
    print()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True
    )

