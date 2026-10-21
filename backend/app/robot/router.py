"""
FastAPI routes for ESP32-C3 robot control (BLE).

Mounted under ``/api/v1/robot``:

    GET  /robot/state       current connection + sensor state
    GET  /robot/config      device name / mock flag / BLE UUIDs
    POST /robot/connect     scan for and connect to the ESP32
    POST /robot/disconnect  stop motors and drop the BLE link
    POST /robot/command     send a movement / servo / LED command
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..schemas import RobotActionResponse, RobotCommandRequest, RobotState
from .controller import (
    RX_UUID,
    SERVICE_UUID,
    TX_UUID,
    RobotController,
    is_valid_command,
)

logger = logging.getLogger(__name__)

robot_router = APIRouter()

_controller = RobotController()


def get_controller() -> RobotController:
    return _controller


@robot_router.get("/state", response_model=RobotState)
def read_state() -> RobotState:
    return RobotState(**get_controller().state())


@robot_router.get("/config")
def read_config() -> dict:
    state = get_controller().state()
    return {
        "device_name": state["device_name"],
        "mock": state["mock"],
        "service_uuid": SERVICE_UUID,
        "rx_uuid": RX_UUID,
        "tx_uuid": TX_UUID,
    }


@robot_router.post("/connect", response_model=RobotActionResponse)
async def connect_robot() -> RobotActionResponse | JSONResponse:
    result = await get_controller().connect()
    if not result.get("ok"):
        return JSONResponse(status_code=500, content=result)
    return RobotActionResponse(**result)


@robot_router.post("/disconnect", response_model=RobotActionResponse)
async def disconnect_robot() -> RobotActionResponse | JSONResponse:
    result = await get_controller().disconnect()
    if not result.get("ok"):
        return JSONResponse(status_code=500, content=result)
    return RobotActionResponse(**result)


@robot_router.post("/command", response_model=RobotActionResponse)
async def send_command(req: RobotCommandRequest) -> RobotActionResponse | JSONResponse:
    command = (req.command or "").strip()

    if not is_valid_command(command):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Invalid command", "command": command},
        )

    result = await get_controller().send(command)

    if not result.get("ok"):
        return JSONResponse(status_code=500, content=result)

    return RobotActionResponse(**result)
