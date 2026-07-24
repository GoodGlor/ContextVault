import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";
import { useCurrentRepository } from "../repository/RepositoryContext";
import {
  createReport,
  createSchedule,
  deleteSchedule,
  downloadReport,
  isGenerating,
  listReports,
  listSchedules,
  patchSchedule,
  type Report,
  type Schedule,
} from "../api/reports";

/** How often to re-poll while any report is still generating. */
export const REPORT_POLL_MS = 2000;

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

/** Any authenticated user: ask for a natural-language report against a
 *  repository's connected reporting database, watch it generate, download the
 *  PDF, and optionally freeze a done report into a nightly schedule. */
export function ReportsPage(): ReactNode {
  const { t } = useTranslation();
  const { repos, currentRepoId, loading, error } = useCurrentRepository();

  const [reports, setReports] = useState<Report[] | null>(null);
  const [reportsError, setReportsError] = useState<string | null>(null);

  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [schedulesError, setSchedulesError] = useState<string | null>(null);

  // (Re)load this repository's reports and schedules whenever the current
  // repository changes.
  useEffect(() => {
    if (currentRepoId === "") return;
    let cancelled = false;
    setReports(null);
    setReportsError(null);
    listReports(currentRepoId)
      .then((rs) => !cancelled && setReports(rs))
      .catch(
        (err: unknown) =>
          !cancelled && setReportsError(errorMessage(err, t("reports.errorLoadReports"))),
      );
    setSchedules(null);
    setSchedulesError(null);
    listSchedules(currentRepoId)
      .then((ss) => !cancelled && setSchedules(ss))
      .catch(
        (err: unknown) =>
          !cancelled && setSchedulesError(errorMessage(err, t("reports.errorLoadSchedules"))),
      );
    return () => {
      cancelled = true;
    };
  }, [currentRepoId, t]);

  // Poll while anything is still generating; re-schedules each time the list
  // changes, and stops once every report has reached a terminal state — the same
  // idiom as SourcesPanel's ingestion poll.
  useEffect(() => {
    if (currentRepoId === "" || reports === null) return;
    if (!reports.some((r) => isGenerating(r.status))) return;
    const timer = setTimeout(() => {
      listReports(currentRepoId)
        .then(setReports)
        .catch(() => {
          /* transient; the next tick retries */
        });
    }, REPORT_POLL_MS);
    return () => clearTimeout(timer);
  }, [reports, currentRepoId]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const p = prompt.trim();
    if (currentRepoId === "" || p === "" || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createReport(currentRepoId, p);
      setReports((prev) => [created, ...(prev ?? [])]);
      setPrompt("");
    } catch (err) {
      setSubmitError(errorMessage(err, t("reports.errorCreate")));
    } finally {
      setSubmitting(false);
    }
  };

  const onDownload = async (report: Report) => {
    try {
      const blob = await downloadReport(currentRepoId, report.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report-${report.id}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setReportsError(errorMessage(err, t("reports.errorDownload")));
    }
  };

  const onRepeatNightly = async (report: Report) => {
    const time = window.prompt(t("reports.scheduleTimePrompt"), "02:00");
    if (time === null || time.trim() === "") return;
    try {
      const created = await createSchedule(currentRepoId, report.id, time.trim());
      setSchedules((prev) => [created, ...(prev ?? [])]);
    } catch (err) {
      setSchedulesError(errorMessage(err, t("reports.errorSchedule")));
    }
  };

  const onToggleSchedule = async (schedule: Schedule) => {
    try {
      const updated = await patchSchedule(schedule.id, { enabled: !schedule.enabled });
      setSchedules((prev) => prev?.map((s) => (s.id === updated.id ? updated : s)) ?? prev);
    } catch (err) {
      setSchedulesError(errorMessage(err, t("reports.errorToggleSchedule")));
    }
  };

  const onDeleteSchedule = async (schedule: Schedule) => {
    try {
      await deleteSchedule(schedule.id);
      setSchedules((prev) => prev?.filter((s) => s.id !== schedule.id) ?? prev);
    } catch (err) {
      setSchedulesError(errorMessage(err, t("reports.errorDeleteSchedule")));
    }
  };

  if (error !== null) {
    return <p className="error">{error}</p>;
  }
  if (loading) {
    return <p>{t("reports.loadingRepos")}</p>;
  }
  if (repos.length === 0) {
    return <p>{t("reports.noRepos")}</p>;
  }

  const statusLabels: Record<Report["status"], string> = {
    pending: t("reports.statusPending"),
    processing: t("reports.statusProcessing"),
    done: t("reports.statusDone"),
    failed: t("reports.statusFailed"),
  };

  return (
    <section className="reports-page">
      <h1>{t("reports.title")}</h1>

      <form className="report-request" onSubmit={onSubmit}>
        <label htmlFor="report-prompt">{t("reports.promptLabel")}</label>
        <textarea
          id="report-prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("reports.promptPlaceholder")}
        />
        <button type="submit" disabled={submitting || prompt.trim() === ""}>
          {submitting ? t("reports.generating") : t("reports.generateButton")}
        </button>
        {submitError !== null && <p className="error">{submitError}</p>}
      </form>

      {reportsError !== null && <p className="error">{reportsError}</p>}
      {reports === null ? (
        <p>{t("reports.loadingReports")}</p>
      ) : reports.length === 0 ? (
        <p>{t("reports.noReports")}</p>
      ) : (
        <ul className="report-list">
          {reports.map((r) => (
            <li key={r.id} className="report-item">
              <span className="report-prompt">{r.prompt}</span>
              <span className={`badge status-${r.status}`}>{statusLabels[r.status]}</span>
              {isGenerating(r.status) && (
                <span className="report-generating" role="status">
                  {t("reports.generatingLabel")}
                </span>
              )}
              {r.status === "failed" && r.error !== null && (
                <span className="report-error">{r.error}</span>
              )}
              {r.status === "done" && r.has_pdf && (
                <>
                  <button type="button" onClick={() => void onDownload(r)}>
                    {t("reports.downloadButton")}
                  </button>
                  <button type="button" onClick={() => void onRepeatNightly(r)}>
                    {t("reports.scheduleButton")}
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <h2>{t("reports.schedulesTitle")}</h2>
      {schedulesError !== null && <p className="error">{schedulesError}</p>}
      {schedules === null ? (
        <p>{t("reports.loadingSchedules")}</p>
      ) : schedules.length === 0 ? (
        <p>{t("reports.noSchedules")}</p>
      ) : (
        <ul className="schedule-list">
          {schedules.map((s) => (
            <li key={s.id} className="schedule-item">
              <span className="schedule-prompt">{s.prompt}</span>
              <span className="schedule-time">{s.run_at_time}</span>
              <label className="schedule-enabled">
                <input
                  type="checkbox"
                  checked={s.enabled}
                  onChange={() => void onToggleSchedule(s)}
                  aria-label={t("reports.enabledLabel")}
                />
                {t("reports.enabledLabel")}
              </label>
              {s.last_error !== null && <span className="schedule-error">{s.last_error}</span>}
              <button type="button" onClick={() => void onDeleteSchedule(s)}>
                {t("reports.deleteButton")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
