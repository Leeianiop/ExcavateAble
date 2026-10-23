import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  connectRobot,
  connectRobotRole,
  disconnectRobot,
  disconnectRobotRole,
  getRobotState,
  sendRobotCommand,
} from "../api/robot";
import { SERVO_CONTROLS } from "../types";
import type { RobotDirection, RobotState } from "../types";

const POLL_MS = 1000;
const DRIVE_REFRESH_MS = 250; // refresh the firmware's 1-second motor watchdog

/** D-pad layout: row-major 3x3 grid. */
const DPAD: (RobotDirection | "STOP" | null)[] = [
  null, "F", null,
  "L", "STOP", "R",
  null, "B", null,
];

const DPAD_GLYPH: Record<string, string> = {
  F: "▲",
  B: "▼",
  L: "◀",
  R: "▶",
};

export const RobotControl: React.FC = () => {
  const [state, setState] = useState<RobotState | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [toast, setToast] = useState("");
  const [toastError, setToastError] = useState(false);
  const [angles, setAngles] = useState<Record<string, number>>(() =>
    Object.fromEntries(SERVO_CONTROLS.map((c) => [c.key, 90]))
  );

  const connectedRef = useRef(false);
  const driveTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastDrive = useRef<RobotDirection | null>(null);

  const connected = state?.connected ?? false;

  const showToast = useCallback((message: string, isError = false) => {
    setToast(message);
    setToastError(isError);
  }, []);

  const stopDriveTimer = useCallback(() => {
    if (driveTimer.current !== null) {
      clearInterval(driveTimer.current);
      driveTimer.current = null;
    }
  }, []);

  const doSend = useCallback(
    async (command: string): Promise<boolean> => {
      if (!connectedRef.current) {
        showToast("Connect to the ESP32 first.", true);
        return false;
      }
      try {
        const res = await sendRobotCommand(command);
        if (!res.ok) {
          showToast(res.error ?? "Command failed", true);
          return false;
        }
        showToast(`Sent: ${command}`);
        return true;
      } catch (err: any) {
        showToast(
          err?.response?.data?.error ?? err?.message ?? "Command failed",
          true
        );
        return false;
      }
    },
    [showToast]
  );

  // Poll the backend for connection + sensor state.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const s = await getRobotState();
        if (cancelled) return;
        setState(s);
        setBackendDown(false);
      } catch {
        if (cancelled) return;
        setBackendDown(true);
        setState((prev) =>
          prev
            ? { ...prev, connected: false, connecting: false, status: "Cannot reach backend" }
            : null
        );
      } finally {
        if (!cancelled) timer = setTimeout(poll, POLL_MS);
      }
    };

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  // Keep the connection flag in a ref (used by the drive timer) and stop the
  // robot if the link drops.
  useEffect(() => {
    connectedRef.current = connected;
    if (!connected) {
      lastDrive.current = null;
      stopDriveTimer();
    }
  }, [connected, stopDriveTimer]);

  // Clear the drive timer on unmount.
  useEffect(() => stopDriveTimer, [stopDriveTimer]);

  const handleConnect = async () => {
    showToast("Searching for boards...");
    try {
      const res = await connectRobot();
      showToast(res.message || "Connecting...");
    } catch (err: any) {
      showToast(err?.message ?? "Connection request failed", true);
    }
  };

  const handleRoleToggle = async (role: string, isConnected: boolean) => {
    showToast(isConnected ? `Disconnecting ${role}...` : `Searching for ${role}...`);
    try {
      const res = isConnected
        ? await disconnectRobotRole(role)
        : await connectRobotRole(role);
      showToast(res.message || (isConnected ? "Disconnected" : "Connecting..."));
    } catch (err: any) {
      showToast(
        err?.response?.data?.error ?? err?.message ?? "Request failed",
        true
      );
    }
  };

  const handleDisconnect = async () => {
    lastDrive.current = null;
    stopDriveTimer();
    try {
      const res = await disconnectRobot();
      showToast(res.message || "Disconnected");
    } catch (err: any) {
      showToast(err?.message ?? "Disconnect request failed", true);
    }
  };

  const driveStart = (command: RobotDirection) => {
    if (!connectedRef.current) {
      showToast("Connect to the ESP32 first.", true);
      return;
    }
    lastDrive.current = command;
    doSend(command);
    stopDriveTimer();
    driveTimer.current = setInterval(() => {
      if (lastDrive.current === command && connectedRef.current) {
        doSend(command);
      }
    }, DRIVE_REFRESH_MS);
  };

  const driveStop = () => {
    if (lastDrive.current === null) return;
    lastDrive.current = null;
    stopDriveTimer();
    doSend("S");
  };

  const handleServoCommit = (key: string, make: (a: number) => string) => {
    doSend(make(angles[key]));
  };

  const devices = state?.devices ?? [];

  const sensors = [
    { label: "Temperature", value: state?.temperature ?? "--", unit: "°C" },
    { label: "Humidity", value: state?.humidity ?? "--", unit: "%" },
    { label: "MQ-8", value: state?.mq8 ?? "--", unit: "" },
    { label: "MQ-5", value: state?.mq5 ?? "--", unit: "" },
  ];

  return (
    <div className="dashboard">
      <div className="page-header">
        <div>
          <h1 className="page-title">Robot Control</h1>
          <p className="page-subtitle">
            Drive the ESP32-C3 rover over BLE — movement, servos, LEDs and live
            sensor telemetry.
          </p>
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
            padding: "0.5rem 1rem",
            borderRadius: "0.5rem",
            backgroundColor: connected ? "#ecfdf5" : "#fef2f2",
            border: `1px solid ${connected ? "#10b98144" : "#ef444444"}`,
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              backgroundColor: connected ? "#10b981" : "#ef4444",
              animation: connected ? "pulse-dot 1.5s infinite" : "none",
            }}
          />
          <span
            style={{
              fontSize: "0.875rem",
              fontWeight: 600,
              color: connected ? "#065f46" : "#991b1b",
            }}
          >
            {connected ? "CONNECTED" : "OFFLINE"}
          </span>
        </div>
      </div>

      {backendDown && (
        <div className="card error-card" style={{ marginBottom: "1rem" }}>
          <div className="card-body">
            <strong>Backend unreachable.</strong>{" "}
            <span style={{ color: "#64748b" }}>
              Start it with <code>uvicorn app.main:app --port 8000</code> in the{" "}
              <code>backend</code> folder.
            </span>
          </div>
        </div>
      )}

      <div className="robot-grid">
        {/* ---------------- Connection ---------------- */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Connection</h2>
            {state?.mock && <span className="chip-btn">MOCK</span>}
          </div>
          <div className="card-body">
            <div className={`robot-status ${connected ? "connected" : "disconnected"}`}>
              <span className={`robot-dot ${connected ? "on" : ""}`} />
              <span>{state?.status ?? "Loading..."}</span>
            </div>

            {devices.length > 0 ? (
              <div className="robot-devices">
                {devices.map((dev) => (
                  <div className="robot-device" key={dev.role}>
                    <span className={`robot-dot ${dev.connected ? "on" : ""}`} />
                    <div className="robot-device-text">
                      <div className="robot-device-role">{dev.role}</div>
                      <div className="robot-device-name">
                        {dev.device_name ? (
                          <code>{dev.device_name}</code>
                        ) : (
                          dev.status
                        )}
                      </div>
                      {dev.device_name && (
                        <div className="robot-device-status">{dev.status}</div>
                      )}
                    </div>
                    <button
                      className="chip-btn"
                      onClick={() => handleRoleToggle(dev.role, dev.connected)}
                      disabled={dev.connecting}
                    >
                      {dev.connecting
                        ? "..."
                        : dev.connected
                          ? "Disconnect"
                          : "Connect"}
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              state?.device_name && (
                <p className="robot-note" style={{ marginTop: "0.75rem" }}>
                  Peripheral: <code>{state.device_name}</code>
                </p>
              )
            )}

            <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
              <button
                className="btn btn-primary"
                onClick={handleConnect}
                disabled={
                  (state?.connecting ?? false) ||
                  (devices.length > 0
                    ? devices.every((d) => d.connected)
                    : connected)
                }
              >
                Connect
              </button>
              <button className="btn btn-outline" onClick={handleDisconnect}>
                Disconnect
              </button>
            </div>

            <div className={`robot-toast ${toastError ? "error" : ""}`}>{toast}</div>

            {state?.mock && (
              <p className="robot-note" style={{ marginTop: "0.5rem" }}>
                Bluetooth library not installed — running a simulated ESP32 so
                the controls stay usable. Install <code>bleak</code> and restart
                the backend for real hardware.
              </p>
            )}
          </div>
        </div>

        {/* ---------------- Sensors ---------------- */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Sensor Readings</h2>
            {state?.last_command && (
              <span style={{ fontSize: "0.8rem", color: "#64748b" }}>
                Last: <code>{state.last_command}</code>
              </span>
            )}
          </div>
          <div className="card-body">
            <div className="robot-sensors">
              {sensors.map((s) => (
                <div className="robot-sensor" key={s.label}>
                  <div className="robot-sensor-label">{s.label}</div>
                  <div className="robot-sensor-value">
                    {s.value}
                    {s.unit && <span className="robot-sensor-unit">{s.unit}</span>}
                  </div>
                </div>
              ))}
            </div>

            {state?.last_message && (
              <p className="robot-note" style={{ marginTop: "1rem" }}>
                Raw: <code>{state.last_message}</code>
              </p>
            )}
            <p className="robot-note" style={{ marginTop: "0.5rem" }}>
              Gas readings use LOW on the digital output as triggered — verify
              this behaviour for your modules.
            </p>
          </div>
        </div>

        {/* ---------------- Movement ---------------- */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Movement</h2>
          </div>
          <div className="card-body">
            <div className="robot-dpad">
              {DPAD.map((cmd, i) => {
                if (cmd === null) {
                  return <span className="spacer" key={`spacer-${i}`} />;
                }
                if (cmd === "STOP") {
                  return (
                    <button
                      key="stop"
                      className="stop"
                      onClick={driveStop}
                      disabled={!connected}
                    >
                      STOP
                    </button>
                  );
                }
                return (
                  <button
                    key={cmd}
                    title={`Drive ${cmd}`}
                    disabled={!connected}
                    onPointerDown={() => driveStart(cmd)}
                    onPointerUp={driveStop}
                    onPointerLeave={driveStop}
                    onPointerCancel={driveStop}
                  >
                    {DPAD_GLYPH[cmd]}
                  </button>
                );
              })}
            </div>
            <p className="robot-note" style={{ textAlign: "center" }}>
              Hold a direction to move. Release to stop.
            </p>
          </div>
        </div>

        {/* ---------------- Servos ---------------- */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Servo Control</h2>
          </div>
          <div className="card-body">
            <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
              <button
                className="btn btn-primary"
                onClick={() => doSend("U")}
                disabled={!connected}
              >
                ⬆ Lift
              </button>
              <button
                className="btn btn-outline"
                onClick={() => doSend("D")}
                disabled={!connected}
              >
                ⬇ Lower
              </button>
            </div>

            {SERVO_CONTROLS.map((c) => (
              <div className="robot-row" key={c.key}>
                <span className="robot-row-label">{c.label}</span>
                <input
                  type="range"
                  min={0}
                  max={180}
                  value={angles[c.key]}
                  disabled={!connected}
                  onChange={(e) =>
                    setAngles((prev) => ({ ...prev, [c.key]: Number(e.target.value) }))
                  }
                  onPointerUp={() => handleServoCommit(c.key, c.make)}
                  onKeyUp={() => handleServoCommit(c.key, c.make)}
                />
                <span className="robot-row-value">{angles[c.key]}°</span>
              </div>
            ))}

            <p className="robot-note" style={{ marginTop: "0.5rem" }}>
              Release a slider to send its angle. Lift/Lower move the servo
              between its lifting and home positions.
            </p>
          </div>
        </div>

        {/* ---------------- LEDs ---------------- */}
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Rainbow LEDs</h2>
          </div>
          <div className="card-body">
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                className="btn btn-primary"
                onClick={() => doSend("RAINBOW_ON")}
                disabled={!connected}
              >
                Rainbow ON
              </button>
              <button
                className="btn btn-outline"
                onClick={() => doSend("RAINBOW_OFF")}
                disabled={!connected}
              >
                Rainbow OFF
              </button>
            </div>
            <p className="robot-note" style={{ marginTop: "1rem" }}>
              Toggles the addressable LED ring on the rover.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
