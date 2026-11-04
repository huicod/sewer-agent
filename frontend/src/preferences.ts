import type { InspectionMode } from "./types";

const DEFAULT_MODE_KEY = "pipe-inspector.default-image-mode";

export function defaultImageMode(): InspectionMode {
  try {
    return localStorage.getItem(DEFAULT_MODE_KEY) === "yolo_only" ? "yolo_only" : "yolo_vlm";
  } catch {
    return "yolo_vlm";
  }
}

export function saveDefaultImageMode(mode: InspectionMode) {
  localStorage.setItem(DEFAULT_MODE_KEY, mode);
}
