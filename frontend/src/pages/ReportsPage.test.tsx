import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReportsPage, REPORT_POLL_MS } from "./ReportsPage";
import type { Repository } from "../api/repositories";
import type { Report, Schedule } from "../api/reports";

vi.mock("../api/repositories", () => ({
  listRepositories: vi.fn(),
}));
vi.mock("../api/reports", async () => {
  const actual = await vi.importActual<typeof import("../api/reports")>("../api/reports");
  return {
    ...actual,
    createReport: vi.fn(),
    listReports: vi.fn(),
    downloadReport: vi.fn(),
    deleteReport: vi.fn(),
    createSchedule: vi.fn(),
    listSchedules: vi.fn(),
    patchSchedule: vi.fn(),
    deleteSchedule: vi.fn(),
  };
});

import { listRepositories } from "../api/repositories";
import {
  createReport,
  createSchedule,
  deleteSchedule,
  downloadReport,
  listReports,
  listSchedules,
  patchSchedule,
} from "../api/reports";

const REPOS: Repository[] = [{ id: "r-1", name: "Handbook", description: null }];

function report(overrides: Partial<Report> = {}): Report {
  return {
    id: "rep-1",
    repository_id: "r-1",
    prompt: "Monthly revenue by region",
    status: "pending",
    error: null,
    created_at: "2026-07-20T10:00:00Z",
    has_pdf: false,
    schedule_id: null,
    ...overrides,
  };
}

function schedule(overrides: Partial<Schedule> = {}): Schedule {
  return {
    id: "sch-1",
    repository_id: "r-1",
    prompt: "Monthly revenue by region",
    run_at_time: "02:00",
    enabled: true,
    last_run_at: null,
    last_error: null,
    created_at: "2026-07-20T10:00:00Z",
    ...overrides,
  };
}

describe("ReportsPage", () => {
  beforeEach(() => {
    vi.mocked(listRepositories).mockResolvedValue(REPOS);
    vi.mocked(listSchedules).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the repository picker once repositories load", async () => {
    vi.mocked(listReports).mockResolvedValue([]);
    render(<ReportsPage />);
    expect(await screen.findByLabelText("Repository")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Handbook" })).toBeInTheDocument();
  });

  it("submits a prompt, calls createReport, and shows the new row as generating", async () => {
    vi.mocked(listReports).mockResolvedValue([]);
    vi.mocked(createReport).mockResolvedValue(report({ status: "pending" }));
    render(<ReportsPage />);
    await screen.findByLabelText("Repository");

    await userEvent.type(screen.getByLabelText("Prompt"), "Monthly revenue by region");
    await userEvent.click(screen.getByRole("button", { name: "Generate report" }));

    expect(createReport).toHaveBeenCalledWith("r-1", "Monthly revenue by region");
    expect(await screen.findByText("Monthly revenue by region")).toBeInTheDocument();
    expect(screen.getByText("Generating…")).toBeInTheDocument();
  });

  it("polls while a report is generating and stops once it reaches done", async () => {
    vi.useFakeTimers();
    try {
      let call = 0;
      vi.mocked(listReports).mockImplementation(() => {
        call += 1;
        return Promise.resolve([
          report({ status: call === 1 ? "processing" : "done", has_pdf: true }),
        ]);
      });
      render(<ReportsPage />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByText("Generating…")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(REPORT_POLL_MS);
      });
      expect(screen.getByRole("button", { name: "Download PDF" })).toBeInTheDocument();

      const callsAfterDone = vi.mocked(listReports).mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(REPORT_POLL_MS * 3);
      });
      // No further polling once every report is terminal.
      expect(vi.mocked(listReports).mock.calls.length).toBe(callsAfterDone);
    } finally {
      vi.useRealTimers();
    }
  });

  it("downloads a done report's PDF and revokes the object URL", async () => {
    vi.mocked(listReports).mockResolvedValue([report({ status: "done", has_pdf: true })]);
    const blob = new Blob(["%PDF"], { type: "application/pdf" });
    vi.mocked(downloadReport).mockResolvedValue(blob);

    const createObjectURL = vi.fn().mockReturnValue("blob:mock-url");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    render(<ReportsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Download PDF" }));

    expect(downloadReport).toHaveBeenCalledWith("r-1", "rep-1");
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");

    vi.unstubAllGlobals();
  });

  it("shows the error text for a failed report", async () => {
    vi.mocked(listReports).mockResolvedValue([
      report({ status: "failed", error: "SQL guardrail rejected the query" }),
    ]);
    render(<ReportsPage />);
    expect(await screen.findByText("SQL guardrail rejected the query")).toBeInTheDocument();
  });

  it("schedules a nightly repeat from a done report via a prompted time", async () => {
    vi.mocked(listReports).mockResolvedValue([report({ status: "done", has_pdf: true })]);
    vi.mocked(createSchedule).mockResolvedValue(schedule());
    vi.spyOn(window, "prompt").mockReturnValue("02:00");

    render(<ReportsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Repeat nightly…" }));

    expect(window.prompt).toHaveBeenCalled();
    expect(createSchedule).toHaveBeenCalledWith("r-1", "rep-1", "02:00");
  });

  it("does not schedule when the time prompt is cancelled", async () => {
    vi.mocked(listReports).mockResolvedValue([report({ status: "done", has_pdf: true })]);
    vi.spyOn(window, "prompt").mockReturnValue(null);

    render(<ReportsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Repeat nightly…" }));

    expect(createSchedule).not.toHaveBeenCalled();
  });

  it("lists schedules and toggles enabled via patchSchedule", async () => {
    vi.mocked(listReports).mockResolvedValue([]);
    vi.mocked(listSchedules).mockResolvedValue([schedule({ enabled: true })]);
    vi.mocked(patchSchedule).mockResolvedValue(schedule({ enabled: false }));

    render(<ReportsPage />);
    const row = (await screen.findByText("Monthly revenue by region")).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("checkbox"));

    expect(patchSchedule).toHaveBeenCalledWith("sch-1", { enabled: false });
  });

  it("deletes a schedule", async () => {
    vi.mocked(listReports).mockResolvedValue([]);
    vi.mocked(listSchedules).mockResolvedValue([schedule()]);
    vi.mocked(deleteSchedule).mockResolvedValue(undefined);

    render(<ReportsPage />);
    const row = (await screen.findByText("Monthly revenue by region")).closest("li") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: "Delete" }));

    expect(deleteSchedule).toHaveBeenCalledWith("sch-1");
  });
});
