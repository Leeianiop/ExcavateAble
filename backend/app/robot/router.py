"""
FastAPI routes for ESP32-C3 robot control (BLE).

Several boards can be connected at the same time, one per *role*: a servo board
for actuators and a sensor board for telemetry.

Mounted under ``/api/v1/robot``:

    GET  /robot/state              aggregated state + per-device detail
    GET  /robot/config             roles, advertising names, mock flag, BLE UUIDs
    POST /robot/connect            scan for and connect every role
    POST /robot/connect/{role}     connect a single role
    POST /robot/disconnect         stop actuators and drop every link
    POST /robot/disconnect/{role}  drop a single link
    POST /robot/command            send a movement / servo / LED command
    POST /robot/command/{role}     send a command to one specific board
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..schemas import RobotActionResponse, RobotCommandRequest, RobotState
from .controller import (
    DEFAULT_ROLES,
    RX_UUID,
    SERVICE_UUID,
    TX_UUID,
    RobotManager,
    is_valid_command,
)

logger = logging.getLogger(__name__)

robot_router = APIRouter()

_manager = RobotManager()


def get_manager() -> RobotManager:
    return _manager


def _not_found(role: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "ok": False,
            "error": f"Unknown device role '{role}'",
            "roles": list(get_manager().roles),
        },
    )


@robot_router.get("/state", response_model=RobotState)
def read_state() -> RobotState:
    return RobotState(**get_manager().state())


@robot_router.get("/config")
def read_config() -> dict:
    state = get_manager().state()
    return {
        "roles": [
            {"role": role, "names": list(names)} for role, names in DEFAULT_ROLES.items()
        ],
        "device_name": state["device_name"],
        "mock": state["mock"],
        "service_uuid": SERVICE_UUID,
        "rx_uuid": RX_UUID,
        "tx_uuid": TX_UUID,
    }


@robot_router.post("/connect", response_model=RobotActionResponse)
async def connect_robot() -> RobotActionResponse | JSONResponse:
    result = get_manager().connect_all()
    if not result.get("ok"):
        return JSONResponse(status_code=500, content=result)
    return RobotActionResponse(**result)


@robot_router.post("/connect/{role}", response_model=RobotActionResponse)
async def connect_role(role: str) -> RobotActionResponse | JSONResponse:
    controller = get_manager().get(role)
    if controller is None:
        return _not_found(role)

    result = await controller.connect()
    if not result.get("ok"):
        return JSONResponse(status_code=500, content=result)
    return RobotActionResponse(**result)


@robot_router.post("/disconnect", response_model=RobotActionResponse)
async def disconnect_robot() -> RobotActionResponse | JSONResponse:
    result = get_manager().disconnect_all()
    if not result.get("ok"):
        return JSONResponse(status_code=500, content=result)
    return RobotActionResponse(**result)


@robot_router.post("/disconnect/{role}", response_model=RobotActionResponse)
async def disconnect_role(role: str) -> RobotActionResponse | JSONResponse:
    controller = get_manager().get(role)
    if controller is None:
        return _not_found(role)

    result = await controller.disconnect()
    if not result.get("ok"):
        return JSONResponse(status_code=500, content=result)
    return RobotActionResponse(**result)


async def _run_command(command: str, role: str | None) -> RobotActionResponse | JSONResponse:
    if role is not None and get_manager().get(role) is None:
        return _not_found(role)

    if not is_valid_command(command):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Invalid command", "command": command},
        )

    result = await get_manager().send(command, role=role)

    if not result.get("ok"):
        return JSONResponse(status_code=500, content=result)

    return RobotActionResponse(**result)


@robot_router.post("/command", response_model=RobotActionResponse)
async def send_command(req: RobotCommandRequest) -> RobotActionResponse | JSONResponse:
    return await _run_command((req.command or "").strip(), None)


@robot_router.post("/command/{role}", response_model=RobotActionResponse)
async def send_command_to_role(
    role: str, req: RobotCommandRequest
) -> RobotActionResponse | JSONResponse:
    return await _run_command((req.command or "").strip(), role)
