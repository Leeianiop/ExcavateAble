from .v1 import v1_router
from ..robot.router import robot_router

# Robot control (ESP32-C3 over BLE) lives under /api/v1/robot
v1_router.include_router(robot_router, prefix="/robot", tags=["robot"])

__all__ = ["v1_router"]
