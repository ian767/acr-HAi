export type AlarmSeverity = "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export interface Alarm {
  id: string;
  severity: AlarmSeverity;
  source: string;
  message: string;
  acknowledged: boolean;
  created_at: string;
  acknowledged_at: string | null;
}
