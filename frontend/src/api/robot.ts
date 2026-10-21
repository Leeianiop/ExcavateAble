import { apiClient } from "./client";
import type { RobotActionResponse, RobotState } from "../types";

export async function getRobotState(): Promise<RobotState> {
  const res = await apiClient.get<RobotState>("/robot/state");
  return res.data;
}

export async function connectRobot(): Promise<RobotActionResponse> {
  const res = await apiClient.post<RobotActionResponse>("/robot/connect");
  return res.data;
}

export async function disconnectRobot(): Promise<RobotActionResponse> {
  const res = await apiClient.post<RobotActionResponse>("/robot/disconnect");
  return res.data;
}

export async function sendRobotCommand(command: string): Promise<RobotActionResponse> {
  const res = await apiClient.post<RobotActionResponse>("/robot/command", { command });
  return res.data;
}
