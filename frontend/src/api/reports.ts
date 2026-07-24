import { api } from "./client";

// Mirrors ReportResponse / ReportAdminResponse in src/contextvault/api/reports.py.
// Database-backed reports (spec §7): request a report in natural language against a
// repository's connected reporting database; generation (NL→SQL→PDF) runs in the
// background, so the caller polls for the terminal status.
export type ReportStatus = "pending" | "processing" | "done" | "failed";

/** A non-terminal status is still generating, so the UI keeps polling it. */
export function isGenerating(status: ReportStatus): boolean {
  return status === "pending" || status === "processing";
}

export interface Report {
  id: string;
  repository_id: string;
  prompt: string;
  status: ReportStatus;
  error: string | null;
  created_at: string;
  has_pdf: boolean;
  schedule_id: string | null;
  // Only present on the admin `?all=true` view — the audit trail of the exact
  // query that ran.
  generated_sql?: string | null;
}

/** Request a report: creates a PENDING row; NL→SQL→PDF generation runs as a
 *  background task the caller polls for. 400 if the repository has no connected
 *  reporting database yet. */
export function createReport(repositoryId: string, prompt: string): Promise<Report> {
  return api.post<Report>(`/repositories/${repositoryId}/reports`, { prompt });
}

/** The caller's own reports for a repository, newest first. `all: true` is an
 *  admin-only escape hatch onto every user's reports (each row then also carries
 *  `generated_sql`). */
export function listReports(repositoryId: string, all = false): Promise<Report[]> {
  const qs = all ? "?all=true" : "";
  return api.get<Report[]>(`/repositories/${repositoryId}/reports${qs}`);
}

/** Download a finished report's PDF bytes; 404 unless the report is DONE with a
 *  stored artifact. Owner or admin only. */
export function downloadReport(repositoryId: string, reportId: string): Promise<Blob> {
  return api.getBlob(`/repositories/${repositoryId}/reports/${reportId}/download`);
}

/** Delete a report; owner or admin only. */
export function deleteReport(repositoryId: string, reportId: string): Promise<void> {
  return api.del<void>(`/repositories/${repositoryId}/reports/${reportId}`);
}

// --- report schedules (Task 12) ----------------------------------------------
// Mirrors ScheduleResponse in src/contextvault/api/report_schedules.py. A schedule
// freezes an already-generated (DONE) report's validated SQL + chart spec so the
// nightly scheduler can re-run it verbatim with no further LLM call.

export interface Schedule {
  id: string;
  repository_id: string;
  prompt: string;
  run_at_time: string;
  enabled: boolean;
  last_run_at: string | null;
  last_error: string | null;
  created_at: string;
}

/** Freeze a DONE report (must be the caller's own, or the caller is admin, and
 *  must carry both generated SQL and a chart spec) into a nightly run at
 *  `runAtTime` ("HH:MM"). */
export function createSchedule(
  repositoryId: string,
  reportId: string,
  runAtTime: string,
): Promise<Schedule> {
  return api.post<Schedule>(`/repositories/${repositoryId}/report-schedules`, {
    report_id: reportId,
    run_at_time: runAtTime,
  });
}

/** The caller's own schedules for a repository. `all: true` is an admin-only
 *  escape hatch onto every user's schedules. */
export function listSchedules(repositoryId: string, all = false): Promise<Schedule[]> {
  const qs = all ? "?all=true" : "";
  return api.get<Schedule[]>(`/repositories/${repositoryId}/report-schedules${qs}`);
}

/** Partial update — only the fields present are applied. Owner or admin only. */
export function patchSchedule(
  scheduleId: string,
  body: { enabled?: boolean; run_at_time?: string },
): Promise<Schedule> {
  return api.patch<Schedule>(`/report-schedules/${scheduleId}`, body);
}

/** Delete a schedule; owner or admin only. */
export function deleteSchedule(scheduleId: string): Promise<void> {
  return api.del<void>(`/report-schedules/${scheduleId}`);
}
