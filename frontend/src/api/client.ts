import axios from "axios";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const API_PREFIX = "/api/v1";

export const apiClient = axios.create({
  baseURL: `${API_BASE_URL}${API_PREFIX}`,
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
  },
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error("[API Error]", error?.message, error?.response?.data);
    return Promise.reject(error);
  }
);
