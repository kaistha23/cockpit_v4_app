"use client";

/**
 * The download control that sits on a figure.
 *
 * One small menu per chart and per table, beside the caption, so a reader
 * takes away the thing they are looking at rather than finding it again
 * in a list at the foot of the answer.
 *
 * Every file comes from the API. Nothing here re-renders a chart or
 * re-formats a number: the SVG is the server's, the CSV is the server's,
 * and the PNG is a raster of the server's SVG. A figure in a credit pack
 * and the figure on screen are the same drawing of the same payload.
 */

import * as React from "react";

import { pngName, rasterise, save } from "./chart-download";
import { exportLinks } from "./client";

type Phase = "idle" | "working" | "failed";

/** The filename the API asked for, or a reasonable one if it did not. */
function nameFrom(disposition: string | null, fallback: string): string {
  const quoted = /filename="([^"]+)"/.exec(disposition ?? "");
  return quoted?.[1] || fallback;
}

/**
 * Fetch one export, naming where its URL came from AT the call.
 *
 * `pick` rather than a prepared string, so the `fetch` reads
 * `pick(exportLinks(runId))` and a reader -- and
 * `test_every_v4_fetch_goes_through_the_v4_prefix`, which is the point --
 * can see the V4 prefix without following a variable. This used to take a
 * `url` and pass the guard because `url` was one of three parameter names
 * it skipped, which is not the same as being checked.
 */
async function fetched(
  runId: string,
  pick: (links: ReturnType<typeof exportLinks>) => string,
): Promise<{ body: Blob; name: string }> {
  const response = await fetch(pick(exportLinks(runId)),
                               { credentials: "include" });
  if (!response.ok) {
    // The API says why in a reader's words; passing its status code
    // through instead would put "422" in front of a credit officer.
    let reason = "This could not be downloaded.";
    try {
      const problem = await response.json();
      reason = problem?.detail?.message || problem?.detail || reason;
    } catch {
      /* A non-JSON failure keeps the general sentence. */
    }
    throw new Error(String(reason));
  }
  return {
    body: await response.blob(),
    name: nameFrom(response.headers.get("content-disposition"), "figure"),
  };
}

function Menu({ label, testId, options }: {
  label: string;
  testId: string;
  options: { label: string; run: () => Promise<void>; testId: string }[];
}) {
  const [open, setOpen] = React.useState(false);
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [problem, setProblem] = React.useState("");

  async function attempt(run: () => Promise<void>) {
    setPhase("working");
    setProblem("");
    try {
      await run();
      setPhase("idle");
      setOpen(false);
    } catch (error) {
      // Said where the button is, not as a toast that scrolls away. The
      // reader needs to know why THIS download did not happen, next to
      // the thing that did not download.
      setPhase("failed");
      setProblem(error instanceof Error ? error.message
                                        : "This could not be downloaded.");
    }
  }

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        data-testid={testId}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={phase === "working"}
        className="rounded border border-border px-1.5 py-0.5 text-xs font-normal text-text-secondary transition hover:bg-surface-hover disabled:opacity-50"
        onClick={() => setOpen((was) => !was)}
      >
        {phase === "working" ? "Saving…" : label}
      </button>
      {open ? (
        <span
          role="menu"
          className="absolute right-0 top-full z-20 mt-1 min-w-32 rounded border border-border bg-surface-raised py-1 shadow-sm"
        >
          {options.map((option) => (
            <button
              key={option.label}
              type="button"
              role="menuitem"
              data-testid={option.testId}
              className="block w-full px-3 py-1 text-left text-xs text-text-secondary transition hover:bg-surface-hover"
              onClick={() => void attempt(option.run)}
            >
              {option.label}
            </button>
          ))}
          {phase === "failed" && problem ? (
            <span data-testid="v4-download-problem"
                  className="block max-w-56 px-3 pt-1 text-xs text-negative">
              {problem}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Download this chart, as the drawing or as a picture of it.
 *
 * `index` is the chart's position in the ANSWER, not on screen: the
 * rendered list is filtered, so the second chart a reader sees can be the
 * fourth in the payload, and addressing it by its on-screen position
 * would download a different chart from the one the button sits under.
 */
export function ChartDownload({ runId, index }: { runId: string; index: number }) {
  const chart = React.useCallback(
    (links: ReturnType<typeof exportLinks>) => links.chart(index),
    [index],
  );
  return (
    <Menu
      label="Download"
      testId="v4-chart-download"
      options={[
        {
          label: "SVG",
          testId: "v4-chart-download-svg",
          run: async () => {
            const { body, name } = await fetched(runId, chart);
            save(body, name);
          },
        },
        {
          label: "PNG",
          testId: "v4-chart-download-png",
          run: async () => {
            // Rasterised from the SERVER's drawing, not from the DOM: a
            // picture of the page would carry the reader's theme, their
            // zoom and whatever they were hovering.
            const { body, name } = await fetched(runId, chart);
            save(await rasterise(await body.text()), pngName(name));
          },
        },
      ]}
    />
  );
}

/** Download this table. Every row by default; the API says which. */
export function TableDownload({ runId, artifactId }: {
  runId: string;
  artifactId: string;
}) {
  return (
    <Menu
      label="Download"
      testId="v4-table-download"
      options={[
        {
          label: "CSV, every row",
          testId: "v4-table-download-all",
          run: async () => {
            const { body, name } = await fetched(
              runId, (links) => links.table(artifactId, "all"));
            save(body, name);
          },
        },
        {
          label: "CSV, rows shown",
          testId: "v4-table-download-shown",
          run: async () => {
            const { body, name } = await fetched(
              runId, (links) => links.table(artifactId, "displayed"));
            save(body, name);
          },
        },
      ]}
    />
  );
}
