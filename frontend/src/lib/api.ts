export const basePath = process.env.NEXT_PUBLIC_APP_BASE_PATH || "";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const name = `fillens_csrf_${process.env.NEXT_PUBLIC_COOKIE_SUFFIX}=`;
  const csrf =
    typeof document === "undefined"
      ? ""
      : document.cookie
          .split("; ")
          .find((c) => c.startsWith(name))
          ?.slice(name.length) || "";
  const response = await fetch(`${basePath}/api${path}`, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
    headers: {
      ...(init.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      "X-CSRF-Token": csrf,
      ...init.headers,
    },
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Anfrage fehlgeschlagen." }));
    if (response.status === 401 && path !== "/auth/login")
      window.dispatchEvent(new Event("fillens:expired"));
    throw new ApiError(
      response.status,
      typeof error.detail === "string" ? error.detail : "Eingaben prüfen.",
    );
  }
  return response.json();
}
export const write = (data: unknown, method = "POST"): RequestInit => ({
  method,
  body: JSON.stringify(data),
});
export type User = {
  id: string;
  email: string;
  role: "admin" | "user";
  active: boolean;
};
export type Site = {
  id: string;
  name: string;
  timezone: string;
  description: string;
};
export type Measurement = {
  id: string;
  measured_at: string;
  effective_fill_level: number;
  absolute_level: number;
  capacity_unit: string;
  capacity_snapshot: number;
  change_rate_percent: number | null;
  change_rate_absolute: number | null;
  operating_rate_percent: number | null;
  operating_rate_absolute: number | null;
  rate_revision: number | null;
  rate_cycle: number | null;
  cycle_break: boolean;
  image_mode: string;
  analysis_method: string;
  confidence: number | null;
  model_version: string | null;
  timestamp_source: string;
};
export type Camera = {
  id: string;
  name: string;
  timezone: string;
  ftp_connection_id: string | null;
  ftp_root: string;
  image_rotation: number;
  image_flip: boolean;
  active: boolean;
  scan_mode: "auto" | "date" | "recursive";
  date_pattern: string;
  recent_days: number;
  full_scan_hours: number;
  detected_date_pattern: string | null;
  analysis_enabled: boolean;
  learning_enabled: boolean;
  min_confidence: number;
  roi:
    | { x: number; y: number; width: number; height: number }
    | { points: { x: number; y: number }[] }
    | null;
};
export type Container = {
  id: string;
  site_id: string;
  name: string;
  description: string;
  capacity: number;
  capacity_unit: "liter" | "m3";
  display_mode: "fill" | "consumption";
  rate_time_unit: "minute" | "hour" | "day";
  smoothing_minutes: number;
  event_threshold: number;
  rate_revision: number;
  calculated_revision: number;
  rate_status: "ready" | "pending";
  operating_rate_percent: number | null;
  operating_rate_absolute: number | null;
  alert_severity: number;
  active: boolean;
  camera: Camera;
  current_image: {
    id: string;
    captured_at: string;
    timestamp_source: string;
    image_mode: string;
    has_measurement: boolean;
    analysis: {
      raw_fill_level: number | null;
      confidence: number | null;
      status: string;
      reason: string | null;
    } | null;
  } | null;
  current_measurement: Measurement | null;
  latest_analysis: {
    id: string;
    raw_fill_level: number | null;
    confidence: number | null;
    status: string;
    reason: string | null;
    measured_at: string;
  } | null;
  absolute_level: number | null;
  remaining_capacity: number | null;
  change_rate_percent: number | null;
  change_rate_absolute: number | null;
};
export type FTPConnection = {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  protocol: "ftp" | "ftps";
  root_path: string;
  has_password: boolean;
};
export type FTPTestResult = {
  path: string;
  connected: boolean;
  listed: boolean;
  folders: number | null;
  images: number | null;
  error_code: string | null;
  message: string;
};
export type Status = {
  database: string;
  worker: string;
  worker_busy: boolean;
  manual_run_pending: boolean;
  last_run_started_at: string | null;
  last_run_finished_at: string | null;
  worker_phase: string;
  active_camera_id: string | null;
  scanned_entries: number;
  imported_count: number;
  operation_started_at: string | null;
  worker_error: string | null;
  heartbeat_at: string | null;
  poll_seconds: number;
  analysis: string;
  cameras: {
    id: string;
    name: string;
    last_scan_at: string | null;
    last_import_at: string | null;
    error: string | null;
    error_code: string | null;
    scan_mode: string;
    detected_date_pattern: string | null;
    date_pattern: string;
    scan_kind: string | null;
    scanned_entries: number;
    last_full_scan_at: string | null;
    buffer_count: number;
    buffer_bytes: number;
    configured: boolean;
    active: boolean;
  }[];
};
export const unit = (value: string) => (value === "m3" ? "m³" : "l");
export const number = (value: number | null | undefined, digits = 1) =>
  value == null
    ? "–"
    : new Intl.NumberFormat("de-DE", { maximumFractionDigits: digits }).format(
        value,
      );
export const date = (
  value: string | null | undefined,
  timezone = "Europe/Berlin",
) =>
  value
    ? new Intl.DateTimeFormat("de-DE", {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: timezone,
      }).format(new Date(value))
    : "Noch nicht verfügbar";

export type ContainerEvent = {
  id: string;
  occurred_at: string;
  kind: "emptying" | "refill";
  source: string;
  note: string;
};
export const rateFactor = (value: Container["rate_time_unit"]) =>
  value === "minute" ? 1 / 60 : value === "day" ? 24 : 1;
export const rateUnit = (value: Container["rate_time_unit"]) =>
  value === "minute" ? "min" : value === "day" ? "Tag" : "h";

// Feste visuelle Dashboard-Stufen; administrative Benachrichtigungsregeln sind unabhängig.
export function dashboardLevelTone(
  mode: Container["display_mode"],
  level: number | null | undefined,
): "unknown" | "normal" | "orange" | "red" {
  if (level == null) return "unknown";
  if (mode === "consumption")
    return level <= 5 ? "red" : level <= 20 ? "orange" : "normal";
  return level >= 95 ? "red" : level >= 80 ? "orange" : "normal";
}
