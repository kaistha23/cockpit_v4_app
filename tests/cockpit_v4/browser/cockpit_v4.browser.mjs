/**
 * REAL BROWSER · REAL UI · REAL BACKEND · MOCK ANALYST.
 *
 * A real Chromium against the real Next.js UI and the real V4 API. Every
 * network request the page makes is recorded, which is what makes the central
 * claim checkable rather than asserted: the Cockpit page uses the V4 run API
 * and never touches the legacy investigation or agentic-officer endpoints.
 *
 * The ANALYST is a stub. This proves the wiring, the lifecycle and the
 * rendering. It proves nothing about answer quality, and nothing here claims
 * a live Opus validation.
 */

import assert from "node:assert/strict";
import { chromium } from "/opt/node22/lib/node_modules/playwright/index.mjs";

const UI = process.env.V4_UI_URL ?? "http://127.0.0.1:5414";
const API = process.env.V4_API_URL ?? "http://127.0.0.1:8414";
const CHROME =
  process.env.V4_CHROME ?? "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

/** Endpoints a V4 runtime must never call. The defect, as a list. */
const FORBIDDEN = [
  "/api/v1/investigations",
  "/api/v1/agentic/officer",
  "/api/v1/ask/mode",
  "/api/v1/ask/briefing",
  "/api/v1/ask/suggestions",
  "/api/v1/ask/cockpit-v2/diagnostics",
  "/api/v1/cockpit/diagnostics",
];

const results = [];
const measurements = [];
let failures = 0;

/** Record a timing so the evidence file carries it, not just the console. */
function measure(what, ms, budgetMs) {
  measurements.push({ what, ms: Math.round(ms), budget_ms: budgetMs });
  console.log(`  ${String(Math.round(ms)).padStart(6)}ms  ${what} ` +
              `(budget ${budgetMs}ms)`);
  assert.ok(ms <= budgetMs,
    `${what} took ${Math.round(ms)}ms, over its ${budgetMs}ms budget`);
}

/** Run a subset while diagnosing. Unset in CI, so the default is everything. */
const ONLY = process.env.V4_BROWSER_ONLY
  ? new RegExp(process.env.V4_BROWSER_ONLY, "i")
  : null;

async function test(name, fn) {
  if (ONLY && !ONLY.test(name)) return;
  const started = Date.now();
  try {
    await fn();
    results.push({ name, ok: true, ms: Date.now() - started });
    console.log(`  ok   ${name}`);
  } catch (error) {
    failures += 1;
    results.push({
      name,
      ok: false,
      ms: Date.now() - started,
      error: String(error?.message ?? error).split("\n").slice(0, 4).join(" | "),
    });
    console.log(`  FAIL ${name}\n       ${error?.message ?? error}`);
  }
}

/** A page that records every request it makes, with a live event tap. */
//: The theme every page in this run is opened in.
//
// Dark-mode evidence used to mean a person switching the theme by hand and
// taking a picture, which is not evidence anybody can re-take. Seeded into
// localStorage BEFORE the first paint, which is also where `ThemeScript`
// reads it, so the screenshot is of the theme the reader would actually
// have seen rather than a flash of the default.
const THEME = process.env.V4_THEME || "";

/** A context with the run's theme already in storage. */
async function newContext(browser, options = {}) {
  const context = await browser.newContext(options);
  if (THEME) {
    await context.addInitScript(
      ([key, value]) => {
        try {
          window.localStorage.setItem(key, value);
        } catch {
          /* Private browsing: the default theme is still a valid page. */
        }
      },
      ["ipm.theme", THEME],
    );
  }
  return context;
}

async function openCockpit(browser, { path = "/" } = {}) {
  const context = await newContext(browser);
  const page = await context.newPage();
  const requests = [];
  const sse = [];
  const problems = [];
  // METHOD and URL. A reload legitimately re-READS the run it is
  // restoring; what it must never do is POST a second one. Recording only
  // the URL made those two indistinguishable, so the assertion below could
  // only count calls and hope.
  page.on("request", (request) =>
    requests.push(`${request.method()} ${request.url()}`));
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(`console: ${message.text()}`);
  });
  page.on("requestfailed", (request) =>
    problems.push(
      `requestfailed: ${request.url()} — ${request.failure()?.errorText ?? ""}`,
    ),
  );
  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/events")) sse.push({ url, status: response.status() });
  });
  await page.goto(`${UI}${path}`, { waitUntil: "domcontentloaded" });
  await expect(page, '[data-testid="cockpit-v4-home"]', 60_000, problems);
  return { context, page, requests, sse, problems };
}

/** A page that is not the Cockpit home: the Data browser, for instance. */
async function openPage(browser, path) {
  const context = await newContext(browser);
  const page = await context.newPage();
  await page.goto(`${UI}${path}`, { waitUntil: "domcontentloaded" });
  return { context, page };
}

function calls(requests, fragment) {
  return requests.filter((entry) => entry.includes(fragment));
}

/**
 * The period shapes each book writes, and the shape it must never write.
 *
 * The Corporate book reports QUARTERS and the Retail book reports MONTHS.
 * These assertions used to name one calendar, which was right while both
 * books had it and became a false expectation the day they diverged -- the
 * failing message read "a monthly book offered a quarter" about a book that
 * reports quarters.
 */
const QUARTER = /\b20\d\d ?Q[1-4]\b|\bQ[1-4] 20\d\d\b/;
const MONTH = /\b20\d\d-(0[1-9]|1[0-2])\b(?![-\d])|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) 20\d\d\b/;

/** The period pattern this book writes, and the one it must not. */
function calendarOf(domain) {
  return domain === "retail"
    ? { own: MONTH, foreign: QUARTER, noun: "month" }
    : { own: QUARTER, foreign: MONTH, noun: "quarter" };
}

/** Which book the API is serving under this domain. */
async function feedFor(domain) {
  const query = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  return (await fetch(`${API}/api/v1/cockpit-v4/attention${query}`)).json();
}

/** Only the calls that ASK a question: a POST to the runs collection. */
function asks(requests) {
  return requests.filter((entry) =>
    /^POST .*\/api\/v1\/cockpit-v4\/runs(\?|$)/.test(entry),
  );
}

function assertNoLegacyCalls(requests, where) {
  for (const endpoint of FORBIDDEN) {
    const hits = calls(requests, endpoint);
    assert.equal(
      hits.length,
      0,
      `${where}: the V4 runtime called ${endpoint} (${hits[0] ?? ""}). ` +
        `That is the legacy flow, and reaching it means Opus V4 was bypassed.`,
    );
  }
}

/**
 * Ask from the HOME page, which now opens a conversation.
 *
 * Submitting no longer answers in place: it creates a thread and navigates
 * into it. So the helper waits for the thread to be on screen, because every
 * assertion after the click is about the thread and a test that raced the
 * navigation would be asserting against the page it just left.
 */
async function ask(page, question) {
  await page.fill('[data-testid="cockpit-v4-question"]', question);
  await page.click('[data-testid="cockpit-v4-ask"]');
  await page.waitForSelector('[data-testid="cockpit-v4-thread"]',
    { timeout: 30_000 });
}

/** Ask again from inside the thread, where the composer is. */
async function followUp(page, question) {
  await page.fill('[data-testid="v4-composer-input"]', question);
  await page.click('[data-testid="v4-composer-send"]');
}

/** The thread id in the URL, which is what makes a thread a place. */
function threadIdFrom(page) {
  const match = /\/cockpit\/thread\/([^/?#]+)/.exec(page.url());
  return match ? decodeURIComponent(match[1]) : "";
}

/** An answer is on screen once its assistant turn has rendered. */
async function waitForAnswer(page, timeout = 60_000) {
  await page.waitForSelector('[data-testid="v4-turn-assistant"]', { timeout });
}

/** Wait, and say what the browser complained about when it times out. */
async function expect(page, selector, timeout, problems) {
  try {
    return await page.waitForSelector(selector, { timeout });
  } catch (error) {
    const submitError = await page
      .textContent('[data-testid="cockpit-v4-submit-error"]')
      .catch(() => "");
    // `waitForSelector` waits for VISIBLE, so this one message covers two
    // completely different failures: the run never produced the thing, or
    // it produced it and the page rendered it with no box. Reporting only
    // "never appeared" sends a reader looking at the backend for what may
    // be a stylesheet, and this cost most of an afternoon once.
    const state = await page
      .evaluate((css) => {
        const node = document.querySelector(css);
        if (!node) return "not in the DOM";
        const box = node.getBoundingClientRect();
        const style = window.getComputedStyle(node);
        return `IN THE DOM but not visible: ${box.width}x${box.height}, ` +
               `display=${style.display}, visibility=${style.visibility}, ` +
               `opacity=${style.opacity}`;
      }, selector)
      .catch(() => "could not be inspected");
    const theme = await page
      .evaluate(() => document.documentElement.getAttribute("data-theme"))
      .catch(() => "?");
    throw new Error(
      `${selector} never appeared (${state}; data-theme=${theme}).` +
        (submitError ? ` Page said: ${submitError}.` : "") +
        (problems?.length ? ` Browser reported: ${problems.slice(0, 3).join(" ; ")}` : ""),
    );
  }
}

/** Read the trace until it says something, expanding stages as they arrive.
 *
 * The panel is a LIVE view of a run that is still going, and its substeps are
 * collapsed under their stage until a reader opens one. Expanding once and
 * reading once samples whatever had arrived at that instant -- which is how
 * this assertion failed about one run in three while the product was working
 * correctly: the panel was read while the run was still on its first stage.
 */
async function traceContains(page, pattern, timeout = 30_000) {
  const deadline = Date.now() + timeout;
  let seen = "";
  for (;;) {
    const stages = await page.$$(
      '[data-testid="v4-process-steps"] li button[aria-expanded]');
    for (const stage of stages) {
      if ((await stage.getAttribute("aria-expanded")) === "false"
          && !(await stage.isDisabled())) {
        await stage.click();
      }
    }
    seen = (await page.textContent('[data-testid="v4-process-steps"]')) ?? "";
    if (pattern.test(seen)) return seen;
    if (Date.now() > deadline) return seen;
    await page.waitForTimeout(250);
  }
}

const browser = await chromium.launch({
  executablePath: CHROME,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

console.log(`\nCockpit V4 browser tests — UI ${UI}, API ${API}\n`);

// ---- the central claim -------------------------------------------------

await test(
  "asking 'Who are you?' posts to the V4 run API and never to the legacy flow",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      // Nothing legacy on load, before a single interaction.
      assertNoLegacyCalls(requests, "on page load");

      await ask(page, "Who are you?");

      await waitForAnswer(page);

      assert.ok(
        asks(requests).length >= 1,
        "submitting must POST /api/v1/cockpit-v4/runs",
      );
      assertNoLegacyCalls(requests, "after submitting");
    } finally {
      await context.close();
    }
  },
);

await test("the process panel appears and consumes the V4 event stream",
  async () => {
    const { context, page, requests, sse, problems } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");

      // The panel is on screen while the run is still working — not after.
      await expect(page, '[data-testid="v4-process-panel"]', 30_000, problems);

      await waitForAnswer(page);

      const streams = calls(requests, "/events");
      assert.ok(
        streams.length >= 1,
        "the page must subscribe to /runs/{run_id}/events",
      );
      assert.match(
        streams[0],
        /\/api\/v1\/cockpit-v4\/runs\/run-[0-9a-f]+\/events/,
        "the stream must be the V4 run's own event endpoint",
      );
      assert.ok(
        sse.some((s) => s.status === 200),
        "the event stream must actually connect",
      );
    } finally {
      await context.close();
    }
  },
);

await test("progress events are rendered before the answer arrives",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");

      // Open the panel and capture the summary while the run is still going.
      await expect(page, '[data-testid="v4-process-panel"]', 30_000, problems);
      await page.click('[data-testid="v4-toggle-process"]');

      const working = await page.waitForFunction(
        () => {
          const summary = document.querySelector(
            '[data-testid="v4-process-summary"]',
          );
          const answered = document.querySelector('[data-testid="v4-response"]');
          if (answered) return false;
          const text = summary?.textContent ?? "";
          return /elapsed/.test(text) ? text : false;
        },
        { timeout: 30_000 },
      );
      const summaryWhileWorking = await working.jsonValue();
      assert.ok(
        summaryWhileWorking,
        "a working summary must be visible before the answer",
      );

      // The steps are backend stages, not invented labels.
      const steps = await page.$$eval(
        '[data-testid="v4-process-steps"] li',
        (nodes) => nodes.map((n) => n.textContent ?? ""),
      );
      assert.ok(steps.length >= 1, "the panel lists real stages");
      assert.ok(
        steps.some((s) => /Request accepted|Understanding the request/.test(s)),
        `the stages must be backend stages, got ${JSON.stringify(steps)}`,
      );

      await waitForAnswer(page);
    } finally {
      await context.close();
    }
  },
);

await test("the final answer renders with its disposition", async () => {
  const { context, page, requests, problems } = await openCockpit(browser);
  try {
    await ask(page, "Who are you?");
    const panel = await expect(page, '[data-testid="v4-response"]', 60_000, problems);
    const disposition = await panel.getAttribute("data-disposition");
    assert.equal(disposition, "answer");
    const text = await panel.textContent();
    assert.match(text ?? "", /CreditProbe/);

    // The authoritative status was read, not guessed.
    assert.ok(
      calls(requests, "/api/v1/cockpit-v4/runs/").some(
        (url) => !url.includes("/events"),
      ),
      "the page reads the authoritative run state",
    );
  } finally {
    await context.close();
  }
});

await test("an evidence-bound analysis renders artifact links", async () => {
  const { context, page, problems } = await openCockpit(browser);
  try {
    await ask(page, "What is the EAD by sector for the latest quarter?");
    await expect(page, '[data-testid="v4-response"]', 90_000, problems);
    const artifacts = await page.$$eval(
      '[data-testid="v4-artifacts"] a',
      (nodes) => nodes.map((n) => n.getAttribute("href") ?? ""),
    );
    assert.ok(artifacts.length >= 1, "the answer links to its evidence");
    assert.match(artifacts[0], /\/api\/v1\/cockpit-v4\/runs\/.+\/artifacts\//);
  } finally {
    await context.close();
  }
});

// ---- failure, cancel, replay ------------------------------------------

await test("a terminal failure renders explicitly with a support reference",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      await ask(page, "please fail this run");
      await expect(page, '[data-testid="v4-terminal-failure"]', 60_000, problems);
      const text = await page.textContent('[data-testid="v4-terminal-failure"]');
      assert.match(
        text ?? "",
        /stopped|interrupted|ran out of time/i,
        "a failure is rendered as a failure, not as a short answer",
      );
      assertNoLegacyCalls(requests, "on a failed run");
    } finally {
      await context.close();
    }
  },
);

await test("stop cancels the run through the V4 cancel endpoint", async () => {
  const { context, page, requests, problems } = await openCockpit(browser);
  try {
    await ask(page, "a slow question");
    await page.waitForSelector('[data-testid="v4-stop"]', { timeout: 30_000 });
    await page.click('[data-testid="v4-stop"]');

    await page.waitForFunction(
      () => {
        const summary = document.querySelector(
          '[data-testid="v4-process-summary"]',
        );
        return /Cancelled/i.test(summary?.textContent ?? "");
      },
      { timeout: 60_000 },
    );

    const cancels = calls(requests, "/cancel");
    assert.ok(cancels.length >= 1, "Stop must call the V4 cancel endpoint");
    assert.match(cancels[0], /\/api\/v1\/cockpit-v4\/runs\/run-[0-9a-f]+\/cancel/);
  } finally {
    await context.close();
  }
});

await test("a browser refresh replays the same run instead of re-asking",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await ask(page, "a slow question");
      await expect(page, '[data-testid="v4-process-panel"]', 30_000, problems);

      const runId = await page.evaluate(() => {
        const raw = sessionStorage.getItem("cockpit-v4:active-run");
        return raw ? JSON.parse(raw).runId : "";
      });
      assert.match(runId, /^run-[0-9a-f]+$/, "the active run is remembered");

      // Reload, and watch what the fresh page does.
      const afterReload = [];
      page.on("request", (request) => afterReload.push(request.url()));
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page, '[data-testid="v4-process-panel"]', 60_000, problems);

      const posts = afterReload.filter(
        (url) =>
          url.endsWith("/api/v1/cockpit-v4/runs") ||
          url.endsWith("/api/v1/cockpit-v4/threads"),
      );
      assert.equal(
        posts.length,
        0,
        `a refresh must not start a new run; it requested ${JSON.stringify(posts)}`,
      );
      assert.ok(
        afterReload.some((url) => url.includes(`${runId}/events`)),
        "the refreshed page reconnects to the SAME run's event stream",
      );
      assertNoLegacyCalls(afterReload, "after a refresh");
    } finally {
      await context.close();
    }
  },
);

// ---- the shell around it ----------------------------------------------

await test("optional landing widgets show a neutral state and request nothing",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      const neutral = await page.$$('[data-testid="v4-not-in-runtime"]');
      assert.ok(
        neutral.length >= 1,
        "optional widgets render a neutral not-in-this-runtime state",
      );
      const text = await page.textContent('[data-testid="v4-not-in-runtime"]');
      assert.match(text ?? "", /not available in this isolated Cockpit V4/);
      assertNoLegacyCalls(requests, "for the optional widgets");
    } finally {
      await context.close();
    }
  },
);

await test("no request from the page goes to the legacy backend port",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);
      const stray = requests.filter((url) => /:8000(\/|$)/.test(url));
      assert.deepEqual(stray, [], "nothing may address the legacy backend");
    } finally {
      await context.close();
    }
  },
);

// ---- the trace, the rendering and the one-generation contract ---------

await test("the process panel shows real stages, not 'not started' at 0s",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await expect(page, '[data-testid="v4-process-panel"]', 30_000, problems);
      await page.click('[data-testid="v4-toggle-process"]');
      await waitForAnswer(page);

      const steps = await page.$$eval(
        '[data-testid="v4-process-steps"] li',
        (nodes) => nodes.map((n) => n.textContent ?? ""),
      );
      const notStarted = steps.filter((s) => /not started/.test(s));
      assert.equal(
        notStarted.length,
        0,
        `every stage the run reached must be marked; still "not started": ` +
          JSON.stringify(notStarted),
      );

      const summary = await page.textContent('[data-testid="v4-process-summary"]');
      assert.match(summary ?? "", /Answered in/);
      assert.doesNotMatch(
        summary ?? "",
        /Answered in 0s/,
        "a run that took real time must not report 0s — the recorded live " +
          "run showed 29.4s on the backend and 0s in the browser",
      );

      // Substeps live inside an expanded step, so open one. Each carries a
      // backend event with its own timing.
      const stepButtons = await page.$$(
        '[data-testid="v4-process-steps"] li button',
      );
      assert.ok(stepButtons.length >= 1, "the panel lists stages");
      // Only click a step that is CLOSED. The panel auto-expands the stage
      // that failed, and toggling it would close the very thing under test.
      for (const button of stepButtons) {
        if (await button.isDisabled()) continue;
        if ((await button.getAttribute("aria-expanded")) !== "true") {
          await button.click();
        }
      }
      const substeps = await page.$$eval(
        '[data-testid="v4-substep"]',
        (nodes) => nodes.map((n) => n.textContent ?? ""),
      );
      assert.ok(substeps.length >= 3,
        `expected several real substeps, got ${substeps.length}`);
      assert.ok(
        substeps.some((s) => /\d+\.\ds/.test(s)),
        "each substep carries the time it happened at",
      );
    } finally {
      await context.close();
    }
  },
);

await test("the answer renders as Markdown, with no raw syntax left over",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);
      await expect(page, '[data-testid="v4-markdown"]', 10_000, problems);

      const headings = await page.$$eval(
        '[data-testid="v4-markdown"] h2',
        (nodes) => nodes.map((n) => n.textContent ?? ""),
      );
      assert.ok(headings.length >= 1, "headings render as headings");

      const strong = await page.$$('[data-testid="v4-markdown"] strong');
      assert.ok(strong.length >= 1, "bold renders as bold");

      const bullets = await page.$$('[data-testid="v4-markdown"] li');
      assert.ok(bullets.length >= 1, "bullets render as list items");

      const rendered = await page.textContent('[data-testid="v4-markdown"]');
      assert.doesNotMatch(rendered ?? "", /\*\*/,
        "no raw ** may reach the reader");
      assert.doesNotMatch(rendered ?? "", /^#{1,4}\s/m,
        "no raw heading syntax may reach the reader");

      // And nothing on this path builds HTML from the answer.
      const injected = await page.$$('[data-testid="v4-markdown"] script');
      assert.equal(injected.length, 0);
    } finally {
      await context.close();
    }
  },
);

await test("suggested-question chips stay interactive UI, not Markdown",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);

      const chips = await page.$$('[data-testid="v4-response"] button');
      assert.ok(chips.length >= 1, "the answer offers next questions as chips");
      const label = (await chips[chips.length - 1].textContent()) ?? "";
      await chips[chips.length - 1].click();

      // Clicking a chip starts a NEW V4 run, not a legacy submission.
      await page.waitForFunction(
        () => {
          const el = document.querySelector('[data-testid="v4-process-summary"]');
          return /elapsed|Answered/.test(el?.textContent ?? "");
        },
        { timeout: 30_000 },
      );
      assertNoLegacyCalls(requests, `after clicking the chip "${label}"`);
      assert.ok(
        calls(requests, "/api/v1/cockpit-v4/runs").length >= 2,
        "a chip asks through the V4 run API",
      );
    } finally {
      await context.close();
    }
  },
);

/**
 * §18. A CUT-OFF ACTION IS NOT A CALL LIMIT, and the panel says so.
 *
 * The Mac screen read: "Understanding the request", then a cut-off notice,
 * then CALL_LIMIT, with no query ever run. The panel was telling the truth
 * about a run that had stopped for the wrong reason. Now the same first
 * turn is cut off and the run carries on -- so the panel has to show the
 * attempt failing AND the stages that follow it.
 */
await test("a cut-off action shows the retry and then the real stages",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      await ask(page, "EAD by sector, cut off the first action");
      await expect(page, '[data-testid="v4-response"]', 120_000, problems);

      // The stage that failed says so, without anything being expanded.
      const note = await expect(
        page, '[data-testid^="v4-step-failures-"]', 20_000, problems);
      assert.match((await note.textContent()) ?? "",
        /\d+ attempts? failed here/);

      // And the attempt itself, in the substeps, says what happened to it.
      for (const button of await page.$$(
        '[data-testid="v4-process-steps"] li button')) {
        if (await button.isDisabled()) continue;
        if ((await button.getAttribute("aria-expanded")) !== "true") {
          await button.click();
        }
      }
      const panel = await page.textContent('[data-testid="v4-process-panel"]');
      const text = (panel ?? "").toLowerCase();
      assert.ok(/cut off|asking again/.test(text),
        `the panel never says the attempt was cut off: ${text.slice(0, 600)}`);
      assert.ok(!text.includes("call_limit") && !text.includes("call limit"),
        "a single cut-off action is not a call limit");

      // And the run went on through the stages that actually happened.
      const stages = await page.$$eval(
        '[data-testid="v4-process-steps"] li',
        (rows) => rows.map((row) => (row.textContent ?? "").toLowerCase()),
      );
      const joined = stages.join(" | ");
      for (const stage of ["preparing", "executing", "publishing"]) {
        assert.ok(joined.includes(stage),
          `the panel never showed ${stage}: ${joined.slice(0, 400)}`);
      }
      assertNoLegacyCalls(requests, "cut-off action");
    } finally {
      await context.close();
    }
  },
);

/**
 * FAULT 4. A RESULT THAT WAS COMPUTED IS SHOWN, NARRATIVE OR NOT.
 *
 * Thread th-48fdeffe125f489592f627e307852e31 on the Mac: the analysis ran,
 * its rows were stored, the answer turn was cut off at its output allowance
 * twice, and the reader was shown
 *
 *     This request stopped
 *     Reason: ANSWER_FORMAT_EXHAUSTED
 *
 * over rows they had already paid for. The panel branched on `!response`
 * alone, and the server sent no response at all on a stop.
 *
 * This path had never been exercised end to end, which is how it shipped.
 */
await test("a result survives an answer that could not be written",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      await ask(page, "EAD by sector with no write-up");
      await expect(page, '[data-testid="v4-response"]', 120_000, problems);

      // THE defect: the empty stop box must not be what a reader gets when
      // there are rows behind it.
      assert.equal(await page.$('[data-testid="v4-terminal-failure"]'), null,
        "the run computed rows; an empty stop box throws them away");

      // The rows are on screen, and they are rows, not a placeholder.
      await expect(page, '[data-testid="v4-result-table"]', 20_000, problems);
      const rows = await page.$$('[data-testid="v4-table-row"]');
      assert.ok(rows.length >= 2,
        `the table published ${rows.length} rows`);

      // And the caveat says WHICH layer failed. "The analysis failed" over
      // correct numbers is the same defect with the opposite sign.
      const caveat = await expect(
        page, '[data-testid="v4-result-only-caveat"]', 10_000, problems);
      const said = ((await caveat.textContent()) ?? "").toLowerCase();
      assert.ok(said.includes("the analysis ran"),
        `the caveat does not say the analysis ran: ${said.slice(0, 300)}`);
      assert.ok(said.includes("explanation"),
        `the caveat does not name the explanation: ${said.slice(0, 300)}`);

      // The run is labelled for what it was, not as a bare stop.
      const disposition = await page.getAttribute(
        '[data-testid="v4-response"]', "data-disposition");
      assert.equal(disposition, "partial_answer");

      // And it is still there after a refresh: the exchange is a turn in
      // the transcript, not something that lived only in this tab.
      const threadId = threadIdFrom(page);
      const before = asks(requests).length;
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page, '[data-testid="cockpit-v4-thread"]', 30_000, problems);
      await waitForAnswer(page, 30_000);
      await expect(page, '[data-testid="v4-result-table"]', 20_000, problems);
      assert.equal(threadIdFrom(page), threadId);
      assert.equal(asks(requests).length, before,
        "a reload must not re-ask a run that already produced its rows");
      assert.equal(await page.$('[data-testid="v4-terminal-failure"]'), null,
        "the preserved result must survive the reload it is stored for");

      assertNoLegacyCalls(requests, "result without a write-up");
    } finally {
      await context.close();
    }
  },
);

await test("a failed attempt stays visible after a later attempt succeeds",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await ask(page, "retry once then answer");
      await expect(page, '[data-testid="v4-response"]', 90_000, problems);

      // The stage-level note is visible as soon as the panel is open: a
      // later success must not erase the earlier failure.
      const note = await expect(
        page, '[data-testid^="v4-step-failures-"]', 20_000, problems,
      );
      // One or more: a rejected answer emits both `answer.validated`
      // (rejected) and `tool.failed`, and both are genuine failed events.
      assert.match(
        (await note.textContent()) ?? "",
        /\d+ attempts? failed here/,
        "the stage says how many attempts failed there",
      );

      // And the failed attempt itself is still in the substeps.
      const stepButtons = await page.$$(
        '[data-testid="v4-process-steps"] li button',
      );
      // Only click a step that is CLOSED. The panel auto-expands the stage
      // that failed, and toggling it would close the very thing under test.
      for (const button of stepButtons) {
        if (await button.isDisabled()) continue;
        if ((await button.getAttribute("aria-expanded")) !== "true") {
          await button.click();
        }
      }
      const failedSubsteps = await page.$$eval(
        '[data-testid="v4-substep"][data-status="rejected"], ' +
          '[data-testid="v4-substep"][data-status="failed"]',
        (nodes) => nodes.map((n) => n.textContent ?? ""),
      );
      assert.ok(
        failedSubsteps.length >= 1,
        "the earlier failed attempt must remain on screen after the retry " +
          "succeeded",
      );

      // The run still ended as a real answer.
      const panel = await page.$('[data-testid="v4-response"]');
      assert.equal(await panel?.getAttribute("data-disposition"), "answer");
    } finally {
      await context.close();
    }
  },
);

// Pictures of the conversation, so "it feels like a thread" is inspectable
// rather than asserted. §58: a human looks at these; a DOM assertion that a
// component exists is not the same claim.
if (process.env.V4_THREAD_SHOTS) {
  const dir = process.env.V4_THREAD_SHOTS;
  const shot = async (page, name) => {
    await page.setViewportSize({ width: 1440, height: 1200 });
    await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
    console.log(`  screenshot ${dir}/${name}.png`);
  };

  // B. an analytical thread, with its chart and its table.
  {
    const { context, page } = await openCockpit(browser);
    try {
      await ask(page, "What is the EAD by sector for the latest quarter?");
      await waitForAnswer(page, 90_000);
      // ONE SCREEN, not two. The chart/table toggle is gone: every chart
      // is stacked and the table sits below them, so a reader sees the
      // picture and the figures without choosing between them.
      await page.waitForSelector('[data-testid="v4-result-table"]',
        { timeout: 30_000 }).catch(() => {});
      await shot(page, "thread_analytical_chart");
    } finally {
      await context.close();
    }
  }

  // C. a seeded investigation.
  {
    const { context, page } = await openCockpit(browser);
    try {
      await openDrawer(page);
      await page.click('[data-testid="attention-investigate"]');
      await page.waitForSelector('[data-testid="investigation-context"]',
        { timeout: 30_000 });
      // A seeded thread with nothing asked in it yet shows the case and an
      // empty transcript, which is not what the surface is FOR. Ask the
      // question the case exists to raise, so the picture shows the seed
      // doing its job.
      await followUp(page,
        "Show the borrowers behind this covenant breach.");
      await waitForAnswer(page, 90_000);
      await shot(page, "thread_investigation");
    } finally {
      await context.close();
    }
  }

  // E. the two books: home, dashboard, ECL panel and drawer, for each.
  {
    const { context, page } = await openCockpit(browser);
    try {
      for (const domain of ["corporate", "retail"]) {
        await switchTo(page, domain);
        await shot(page, `home_${domain}`);
        await page.waitForSelector('[data-testid="v4-ecl-panel"][data-state="ready"]',
          { timeout: 60_000 });
        await page.$eval('[data-testid="v4-ecl-panel"]', (node) =>
          node.scrollIntoView());
        await shot(page, `ecl_panel_${domain}`);
        await openDrawer(page);
        await shot(page, `drawer_segment_${domain}`);
        await page.click('[data-testid="attention-drawer-close"]');
        await openDrawer(page, "ecl-highlights");
        await shot(page, `drawer_ecl_${domain}`);
        await page.click('[data-testid="attention-drawer-close"]');
      }
    } finally {
      await context.close();
    }
  }

  // F. the Data page: both books, and one relation opened.
  {
    const { context, page } = await openPage(browser, "/cockpit/data");
    try {
      await page.waitForSelector('[data-testid="v4-book-corporate"][data-state="ready"]',
        { timeout: 60_000 });
      await page.waitForSelector('[data-testid="v4-book-retail"][data-state="ready"]',
        { timeout: 60_000 });
      await shot(page, "data_books");
      await page.click('[data-testid="v4-relation-retail_account_month"]');
      await page.waitForSelector(
        '[data-testid="v4-relation-detail-retail_account_month"]',
        { timeout: 30_000 });

      // THE ENRICHMENT, ON THE PAGE A READER ACTUALLY OPENS. The API test
      // proves the endpoint serves these columns; this proves they are
      // rendered, which is a different claim and the one a reader cares
      // about. `sub_product` also carries its value list rather than a
      // sample, because twelve sub-products are a category and not a
      // catalogue of names.
      for (const column of ["sub_product", "employment_type",
                            "origination_channel",
                            "delinquency_bucket_fine"]) {
        await page.waitForSelector(`[data-testid="v4-field-${column}"]`,
          { timeout: 15_000 });
      }
      const kind = await page.getAttribute(
        '[data-testid="v4-field-values-sub_product"]', "data-kind");
      assert.equal(kind, "governed",
        "sub_product should be served as a governed value list");
      await shot(page, "data_relation_detail");
    } finally {
      await context.close();
    }
  }

  // F2. §12: the SIDEBAR route. The Mac reader opened Data Builder and
  // found the onboarding estate with nothing about the two published
  // books. The screenshot is of the page they actually land on.
  {
    const { context, page } = await openPage(browser, "/data-builder");
    try {
      await page.waitForSelector(
        '[data-testid="data-builder-analytical-books"]', { timeout: 60_000 });
      await page.waitForSelector(
        '[data-testid="v4-book-corporate"][data-state="ready"]',
        { timeout: 60_000 });
      await page.waitForSelector(
        '[data-testid="v4-book-retail"][data-state="ready"]',
        { timeout: 60_000 });
      await shot(page, "data_builder_sidebar");
    } finally {
      await context.close();
    }
  }

  // G. the export panel, on a finished analytical answer.
  {
    const { context, page } = await openCockpit(browser);
    try {
      await ask(page, "What is exposure at default this month?");
      await waitForAnswer(page, 90_000);
      await page.click('[data-testid="v4-action-export"]');
      await page.waitForSelector('[data-testid="v4-panel-export"]',
        { timeout: 30_000 });
      await shot(page, "answer_export");
    } finally {
      await context.close();
    }
  }

  // H. a retail thread, badged.
  {
    const { context, page } = await openCockpit(browser);
    try {
      await switchTo(page, "retail");
      await ask(page, "What is exposure at default this month?");
      await waitForAnswer(page, 90_000);
      await shot(page, "thread_retail");
    } finally {
      await context.close();
    }
  }

  // I. the landing page on a phone.
  {
    const { context, page } = await openCockpit(browser);
    try {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(400);
      await page.screenshot({ path: `${dir}/landing_phone.png`,
        fullPage: true });
      console.log(`  screenshot ${dir}/landing_phone.png`);
    } finally {
      await context.close();
    }
  }

  // D. a multi-turn conversation.
  {
    const { context, page } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);
      await followUp(page, "What is Cockpit for?");
      await page.waitForFunction(
        () =>
          document.querySelectorAll('[data-testid="v4-turn-assistant"]').length
            >= 2,
        { timeout: 90_000 },
      );
      await shot(page, "thread_multi_turn");
    } finally {
      await context.close();
    }
  }
}

// A picture of the rendered answer, so "polished" is inspectable rather than
// asserted. Written only when a path is given.
if (process.env.V4_BROWSER_SCREENSHOT) {
  const { context, page, problems } = await openCockpit(browser);
  try {
    await ask(page, "Who are you?");
    await waitForAnswer(page);
    await page.click('[data-testid="v4-toggle-process"]');
    await page.setViewportSize({ width: 900, height: 1400 });
    await page.screenshot({
      path: process.env.V4_BROWSER_SCREENSHOT,
      fullPage: true,
    });
    console.log(`  screenshot ${process.env.V4_BROWSER_SCREENSHOT}`);
  } finally {
    await context.close();
  }
}


// ---- the conversation ---------------------------------------------------
//
// The product problem this round exists for: V4 could answer, and the
// experience was still a dashboard. One question, one answer, and to ask the
// obvious next thing you scrolled back to the box you started in. These
// assert the thread as a PLACE -- its own URL, its own transcript, its own
// composer -- and that asking again appends rather than replaces.

await test("asking from home opens a conversation at its own URL", async () => {
  const { context, page, requests, problems } = await openCockpit(browser);
  try {
    await ask(page, "Who are you?");

    const threadId = threadIdFrom(page);
    assert.ok(threadId, `the URL is not a thread: ${page.url()}`);
    const mounted = await page.getAttribute('[data-testid="cockpit-v4-thread"]',
      "data-thread-id");
    assert.equal(mounted, threadId, "the view and the URL name one thread");

    // The question the reader typed is at the top of it, immediately.
    const asked = await page.textContent('[data-testid="v4-turn-user"]');
    assert.match(asked ?? "", /Who are you\?/);

    assertNoLegacyCalls(requests, "after opening a thread");
  } finally {
    await context.close();
  }
});

await test("the follow-up composer is waiting under the answer", async () => {
  const { context, page, problems } = await openCockpit(browser);
  try {
    await ask(page, "Who are you?");
    await waitForAnswer(page);
    await expect(page, '[data-testid="v4-composer-input"]', 10_000, problems);
    const placeholder = await page.getAttribute(
      '[data-testid="v4-composer-input"]', "placeholder");
    assert.match(placeholder ?? "", /follow-up/i,
      "the reader must not have to go back to the landing page to continue");
  } finally {
    await context.close();
  }
});

await test("a follow-up appends and does not replace what came before",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);
      const threadId = threadIdFrom(page);

      await followUp(page, "What is Cockpit for?");
      await page.waitForFunction(
        () =>
          document.querySelectorAll('[data-testid="v4-turn-assistant"]').length
            >= 2,
        { timeout: 90_000 },
      );

      const questions = await page.$$eval('[data-testid="v4-turn-user"]',
        (nodes) => nodes.map((n) => n.textContent ?? ""));
      assert.ok(questions.length >= 2, "both questions must remain on screen");
      assert.match(questions[0], /Who are you\?/,
        "the first exchange must not have been replaced");
      assert.equal(threadIdFrom(page), threadId,
        "a follow-up stays in the same conversation");
    } finally {
      await context.close();
    }
  });

await test("a refresh restores the transcript without asking again",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);
      const threadId = threadIdFrom(page);
      const before = asks(requests).length;

      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page, '[data-testid="cockpit-v4-thread"]', 30_000, problems);
      await waitForAnswer(page, 30_000);

      assert.equal(threadIdFrom(page), threadId);
      assert.equal(asks(requests).length, before,
        "a reload must not re-ask: it spends the analysis twice and tells "
        + "the reader nothing about the first one");
      // And it DID restore rather than render from nothing: the transcript
      // was read back from the server.
      assert.ok(
        calls(requests, `/threads/${threadId}`).some((entry) =>
          entry.startsWith("GET "),
        ),
        "the reload did not read the thread back",
      );
    } finally {
      await context.close();
    }
  });

await test("the thread header names the conversation and renames it",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);

      const title = await page.textContent('[data-testid="v4-thread-title"]');
      assert.match(title ?? "", /Who are you\?/,
        "a thread is named by what was asked in it");

      await page.click('[data-testid="v4-thread-rename"]');
      await page.fill('[data-testid="v4-thread-title-input"]', "My review");
      await page.click('[data-testid="v4-thread-rename-save"]');
      await page.waitForFunction(
        () =>
          document.querySelector('[data-testid="v4-thread-title"]')
            ?.textContent === "My review",
        { timeout: 10_000 },
      );

      // And it survives a reload, because the rename reached the server.
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page, '[data-testid="v4-thread-title"]', 30_000, problems);
      assert.equal(
        await page.textContent('[data-testid="v4-thread-title"]'),
        "My review",
      );
    } finally {
      await context.close();
    }
  });

await test("the way back to Cockpit is at the top left",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);
      const thread = threadIdFrom(page);
      assert.ok(thread, "no thread was opened");

      // §22: top-left, above the title -- not level with Rename on the far
      // right, which is where actions ON a conversation live.
      const box = await page.evaluate(() => {
        const back = document.querySelector(
          '[data-testid="v4-back-to-cockpit"]');
        const title = document.querySelector('[data-testid="v4-thread-title"]');
        if (!back || !title) return null;
        const b = back.getBoundingClientRect();
        const t = title.getBoundingClientRect();
        return { backLeft: b.left, backTop: b.top, titleLeft: t.left,
                 titleTop: t.top, width: window.innerWidth };
      });
      assert.ok(box, "there is no back control on the thread");
      assert.ok(box.backTop <= box.titleTop,
        "the way back sits below the title");
      assert.ok(box.backLeft < box.width / 2,
        "the way back is not on the left of the page");

      await page.click('[data-testid="v4-back-to-cockpit"]');
      await page.waitForSelector('[data-testid="cockpit-v4-home"]',
        { timeout: 30_000 });

      // §49: the conversation is still there to come back to.
      await page.waitForSelector('[data-testid="continue-thread"]',
        { timeout: 60_000 });
      await page.click('[data-testid="continue-thread"]');
      await page.waitForSelector('[data-testid="v4-turn-assistant"]',
        { timeout: 60_000 });
      assert.equal(threadIdFrom(page), thread,
        "reopening landed on a different conversation");
    } finally {
      await context.close();
    }
  });

await test("browser back also returns to Cockpit", async () => {
  const { context, page } = await openCockpit(browser);
  try {
    await ask(page, "Who are you?");
    await waitForAnswer(page);
    await page.goBack({ waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="cockpit-v4-home"]',
      { timeout: 30_000 });
  } finally {
    await context.close();
  }
});

await test("the conversation is named by the question, not 'New conversation'",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      // §23: named as soon as the question exists -- while the run is still
      // working, not when the answer lands.
      //
      // Read as a RACE, not as a single read. The title arrives with the
      // transcript fetch that navigation starts, and reading it in the same
      // tick as `ask` returns asks whether that fetch has landed yet --
      // which is a question about machine load, not about §23. The claim
      // under test is an ORDER: the name beats the answer. So wait for
      // both and assert which one arrived first.
      await ask(page, "reported EAD by sector this quarter");
      const named = page.waitForFunction(
        () => /reported EAD by sector/i.test(
          document.querySelector('[data-testid="v4-thread-title"]')
            ?.textContent ?? ""),
        { timeout: 60_000 }).then(() => "named");
      const answered = page.waitForSelector(
        '[data-testid="v4-turn-assistant"]', { timeout: 60_000 })
        .then(() => "answered");
      assert.equal(
        await Promise.race([named, answered]), "named",
        "the answer landed before the conversation had a name; §23 wants "
        + "the question to name it while the run is still working");
      await waitForAnswer(page);
      assert.match(
        await page.textContent('[data-testid="v4-thread-title"]') ?? "",
        /reported EAD by sector/i);
    } finally {
      await context.close();
    }
  });

await test("a seeded thread opens on three to five questions, before anybody types (§31)",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      // Open a real attention card the way a reader does.
      await openDrawer(page);
      await page.click('[data-testid="attention-investigate"]');
      await page.waitForSelector('[data-testid="investigation-context"]',
        { timeout: 30_000 });

      // Nothing has been asked in it yet.
      const asked = await page.$$('[data-testid="v4-turn-user"]');
      assert.equal(asked.length, 0,
        "this thread already has a question in it; §31 is about the empty one");

      await page.waitForSelector('[data-testid="v4-followups"]',
        { timeout: 20_000 });
      const chips = await page.$$eval('[data-testid="v4-followup-chip"]',
        (nodes) => nodes.map((n) => n.textContent?.trim() ?? ""));
      assert.ok(chips.length >= 3 && chips.length <= 5,
        `an empty seeded thread offered ${chips.length} questions; §31 wants 3-5`);
      for (const chip of chips) {
        assert.ok(chip.length > 8, `not a question: ${JSON.stringify(chip)}`);
        assert.ok(/[.?]$/.test(chip), `not a sentence: ${JSON.stringify(chip)}`);
        // A seeded thread is in the book its card came from, so its
        // questions are written in that book's calendar and never the
        // other's.
        const seededFeed = await feedFor("");
        const calendar = calendarOf(seededFeed.domain_id);
        assert.ok(!calendar.foreign.test(chip),
          `a ${seededFeed.reporting_frequency} book offered the other `
          + `calendar: ${JSON.stringify(chip)}`);
      }
      assert.equal(new Set(chips).size, chips.length,
        "the same question was offered twice");
    } finally {
      await context.close();
    }
  });

await test("the Ask next strip never overlaps what is behind it (§34)",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      await ask(page, "reported EAD by sector this quarter");
      await waitForAnswer(page);
      await page.waitForSelector('[data-testid="v4-followups"]',
        { timeout: 30_000 });

      const geometry = await page.evaluate(() => {
        const strip = document.querySelector('[data-testid="v4-followups"]');
        const label = strip.querySelector("span");
        const chips = Array.from(
          strip.querySelectorAll('[data-testid="v4-followup-chip"]'));
        const style = getComputedStyle(strip);
        const box = strip.getBoundingClientRect();
        return {
          opaque: style.backgroundColor,
          box: { top: box.top, bottom: box.bottom,
                 left: box.left, right: box.right },
          label: label ? label.getBoundingClientRect() : null,
          chips: chips.map((c) => {
            const r = c.getBoundingClientRect();
            return { top: r.top, bottom: r.bottom, left: r.left,
                     right: r.right };
          }),
          // What the browser says is painted at the strip's own top-left
          // corner. If the transcript shows through, the strip is not a
          // band, it is a transparency.
          atTopLeft: (document.elementFromPoint(box.left + 2, box.top + 2)
                      ?? {}).getAttribute?.("data-testid") ?? "",
        };
      });

      assert.ok(!/rgba\(0, 0, 0, 0\)|transparent/.test(geometry.opaque),
        `the strip has no background of its own: ${geometry.opaque}`);

      // The label is on its own line, above every chip.
      assert.ok(geometry.label, "the Ask next label is missing");
      for (const chip of geometry.chips) {
        assert.ok(geometry.label.bottom <= chip.top + 1,
          "the Ask next label shares a line with a chip");
      }

      // No two chips overlap, and every chip is inside the band.
      for (let i = 0; i < geometry.chips.length; i += 1) {
        const a = geometry.chips[i];
        assert.ok(a.top >= geometry.box.top - 1
                  && a.bottom <= geometry.box.bottom + 1,
          `a chip hangs outside its own strip`);
        for (let j = i + 1; j < geometry.chips.length; j += 1) {
          const b = geometry.chips[j];
          const overlaps = a.left < b.right && b.left < a.right
            && a.top < b.bottom && b.top < a.bottom;
          assert.ok(!overlaps,
            `chips ${i} and ${j} overlap: ${JSON.stringify([a, b])}`);
        }
      }
    } finally {
      await context.close();
    }
  });

await test("follow-up chips sit directly above the composer and stay in the thread",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      await ask(page, "reported EAD by sector this quarter");
      await waitForAnswer(page);
      const thread = threadIdFrom(page);

      await page.waitForSelector('[data-testid="v4-followups"]',
        { timeout: 30_000 });
      const chips = await page.$$('[data-testid="v4-followup-chip"]');
      assert.ok(chips.length >= 2 && chips.length <= 4,
        `${chips.length} follow-ups were offered; §24 wants 2-4`);

      // §24: attached to the box you type the next question into, and out
      // of the answer panel entirely. The composer is sticky, so "below the
      // answer" is a question about the DOM and "above the input" is a
      // question about the screen; both have to hold.
      const layout = await page.evaluate(() => {
        const strip = document.querySelector('[data-testid="v4-followups"]');
        const input = document.querySelector(
          '[data-testid="v4-composer-input"]');
        if (!strip || !input) return null;
        return {
          strip: strip.getBoundingClientRect().top,
          input: input.getBoundingClientRect().top,
          insideComposer: Boolean(strip.closest('[data-testid="v4-composer"]')),
          insideAnswer: Boolean(
            strip.closest('[data-testid="v4-turn-assistant"]')),
        };
      });
      assert.ok(layout, "the follow-up strip is not on the page");
      assert.ok(layout.strip < layout.input,
        "the follow-ups are below the box they feed");
      assert.ok(layout.insideComposer,
        "the follow-ups do not travel with the composer");
      assert.ok(!layout.insideAnswer,
        "the follow-ups are still buried inside the answer panel");

      // §48: clicking one continues THIS conversation.
      const label = (await chips[0].textContent())?.trim() ?? "";
      await chips[0].click();
      await page.waitForFunction(
        () => document.querySelectorAll(
          '[data-testid="v4-turn-user"]').length >= 2,
        { timeout: 30_000 },
      );
      assert.equal(threadIdFrom(page), thread,
        "a follow-up chip started a new conversation");
      const asked = await page.$$eval('[data-testid="v4-turn-user"]',
        (nodes) => nodes.map((n) => n.textContent?.trim() ?? ""));
      assert.ok(asked.includes(label),
        `the chip said "${label}" and the transcript does not show it`);
    } finally {
      await context.close();
    }
  });

await test("a published figure is written once, the way a credit paper writes it",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      await ask(page, "reported EAD by sector this quarter");
      await waitForAnswer(page);
      // The whole conversation, not just the prose: the transcript, the
      // table, the chart and every tooltip on it.
      await page.waitForSelector('[data-testid="v4-chart-bar"]',
        { timeout: 30_000 });
      const read = async () =>
        await page.evaluate(() => {
          const root = document.querySelector(
            '[data-testid="cockpit-v4-thread"]');
          const titles = Array.from(root?.querySelectorAll("[title]") ?? [])
            .map((el) => el.getAttribute("title") ?? "");
          return `${root?.textContent ?? ""} ${titles.join(" ")}`;
        });

      // The chart and the table are on the SAME screen now, so one read
      // covers both. It is also a stronger assertion than the two it
      // replaces: a figure written twice is caught wherever it is written.
      await page.waitForSelector('[data-testid="v4-result-table"]',
        { timeout: 30_000 });
      const screens = [["chart and table", await read()]];

      for (const [where, screen] of screens) {
        // §37: machine precision must never reach a reader. The live defect
        // printed `7013.1167117986615 SAR million` under a narrative that
        // had already written the same figure properly.
        assert.ok(!/\d\.\d{4,}/.test(screen),
          `machine precision on the ${where} screen: ${
            (/\d+\.\d{4,}/.exec(screen) ?? [""])[0]}`);
        // The unit is said once, not appended beside a string that already
        // carries it.
        assert.ok(!/SAR [\d,.]+ million SAR million/.test(screen),
          `the unit was written twice on the ${where} screen`);
        // And an amount is an amount: no decimals, anywhere, ever.
        const withDecimals = /SAR [\d,]+\.\d+ million/.exec(screen);
        assert.equal(withDecimals, null,
          `an amount carried decimals on the ${where} screen: ${
            withDecimals?.[0]}`);
      }

      // One metric, one string: the figure the prose quotes is a figure the
      // table shows.
      const [, whole] = screens[0];
      const quoted = /SAR [\d,]+ million/.exec(whole)?.[0] ?? "";
      assert.ok(quoted, "no amount was published at all");
      const occurrences = whole.split(quoted).length - 1;
      assert.ok(occurrences >= 2,
        `${quoted} appears once; the prose and the table must agree`);
    } finally {
      await context.close();
    }
  });

await test("the conversation is quick to open, restore and ask again in",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      // Opening a conversation: from the click that asks to the thread being
      // on screen, and from there to the first process event.
      await page.fill('[data-testid="cockpit-v4-question"]',
        "reported EAD by sector this quarter");
      const asked = Date.now();
      await page.click('[data-testid="cockpit-v4-ask"]');
      await page.waitForSelector('[data-testid="cockpit-v4-thread"]',
        { timeout: 30_000 });
      measure("thread opens after asking", Date.now() - asked, 5_000);

      // The panel opens collapsed, so the first event shows up in its
      // summary line -- which is where a reader sees it too.
      await page.waitForFunction(
        () => /elapsed|Answered/.test(
          document.querySelector(
            '[data-testid="v4-process-summary"]')?.textContent ?? ""),
        { timeout: 30_000 },
      );
      measure("first process event on screen", Date.now() - asked, 10_000);

      await waitForAnswer(page);
      // The analytical answer brings a chart and a table with it. Both are
      // server-rendered, so this measures paint, not arithmetic.
      const drawn = Date.now();
      await page.waitForSelector('[data-testid="v4-chart-bar"]',
        { timeout: 30_000 });
      await page.waitForSelector('[data-testid="v4-result-table"]',
        { timeout: 30_000 });
      measure("chart and table rendered", Date.now() - drawn, 5_000);

      // Restoring it: a reload must put the whole transcript back.
      const reloaded = Date.now();
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForSelector('[data-testid="v4-turn-assistant"]',
        { timeout: 30_000 });
      measure("transcript restored after reload", Date.now() - reloaded,
        10_000);

      // Asking again: the follow-up must appear in the transcript at once,
      // long before its answer arrives.
      const followed = Date.now();
      await followUp(page, "and the quarter before that?");
      await page.waitForFunction(
        () => document.querySelectorAll(
          '[data-testid="v4-turn-user"]').length >= 2,
        { timeout: 30_000 },
      );
      measure("follow-up appears in the transcript", Date.now() - followed,
        5_000);
      await waitForAnswer(page);
    } finally {
      await context.close();
    }
  });

await test("the conversation can be shared, and says how it was delivered",
  async () => {
    const { context, page, requests } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);

      await page.click('[data-testid="v4-thread-share"]');
      await page.fill('[data-testid="v4-thread-share-audience"]',
        "credit-committee");
      await page.click('[data-testid="v4-thread-share-submit"]');
      await page.waitForSelector('[data-testid="v4-thread-action-status"]',
        { timeout: 30_000 });
      const status = await page.textContent(
        '[data-testid="v4-thread-action-status"]');
      assert.match(status ?? "", /Shared with credit-committee/);

      // The delivery line never claims an email was sent unless a transport
      // accepted it, and this build configures none.
      const delivery = await page.textContent(
        '[data-testid="v4-thread-share-delivery"]').catch(() => "");
      assert.ok(!/\bSent\b/.test(delivery ?? ""), delivery ?? "");
      assertNoLegacyCalls(requests, "for a shared conversation");
    } finally {
      await context.close();
    }
  });

await test("a conversation opens an investigation, and Projects are not faked",
  async () => {
    const { context, page, requests } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);

      await page.click('[data-testid="v4-thread-investigate"]');
      // No button pretends to reach a surface this runtime does not serve.
      const note = await page.textContent(
        '[data-testid="v4-thread-no-projects"]');
      assert.match(note ?? "", /not available in this isolated Cockpit V4/);
      assert.equal(await page.$('[data-testid="v4-thread-add-to-project"]'),
        null, "there is no Add to Project button to click");

      await page.click('[data-testid="v4-thread-investigate-submit"]');
      await page.waitForSelector('[data-testid="v4-thread-action-status"]',
        { timeout: 30_000 });
      assert.match(
        await page.textContent('[data-testid="v4-thread-action-status"]') ?? "",
        /Investigation opened/);
      assertNoLegacyCalls(requests, "for a conversation investigation");
    } finally {
      await context.close();
    }
  });

await test("the conversation links to the trace of its latest answer",
  async () => {
    // `/cockpit/trace`, not `/trace`.
    //
    // This test pinned the OLD path, which is the legacy Analytical
    // Reasoning Map -- it calls `Number(runId)` on a `run-<32 hex>` id,
    // gets NaN and resolves nothing. So the button had been dead since V4
    // shipped, and the assertion that it pointed there passed the whole
    // time: a link is well-formed whether or not anything is behind it.
    const { context, page } = await openCockpit(browser);
    try {
      await ask(page, "Who are you?");
      await waitForAnswer(page);
      const href = await page.getAttribute('[data-testid="v4-thread-trace"]',
        "href");
      assert.match(href ?? "", /^\/cockpit\/trace\/run-[0-9a-f]+$/,
        href ?? "");

      // And the page behind it opens, which is the part the old
      // assertion could not have caught.
      await page.click('[data-testid="v4-thread-trace"]');
      await expect(page, '[data-testid="v4-governance"]', 30_000, []);
    } finally {
      await context.close();
    }
  });

await test("Investigate Further opens the conversation, not just the context",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      await openDrawer(page);
      await page.click('[data-testid="attention-investigate"]');

      await expect(page, '[data-testid="cockpit-v4-thread"]', 30_000, problems);
      assert.ok(threadIdFrom(page), "an investigation must open somewhere");

      const seed = await expect(
        page, '[data-testid="investigation-context"]', 15_000, problems);
      const shown = (await seed.textContent()) ?? "";
      assert.ok(shown.trim().length > 0, "the card says what is under review");

      await expect(page, '[data-testid="v4-composer-input"]', 10_000, problems);
      assertNoLegacyCalls(requests, "after Investigate Further");
    } finally {
      await context.close();
    }
  });

await test("an analytical answer shows every chart it sent, and its table",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await ask(page, "What is the EAD by sector for the latest quarter?");
      await waitForAnswer(page, 90_000);

      await expect(page, '[data-testid="v4-visuals"]', 20_000, problems);
      const bars = await page.$$('[data-testid="v4-chart-bar"]');
      assert.ok(bars.length >= 2,
        "a ranked comparison of sectors is what a bar chart is for");

      // Bars are scaled from CANONICAL values, so the widths must differ
      // when the values do.
      const values = await page.$$eval('[data-testid="v4-chart-bar"]',
        (nodes) => nodes.map((n) => Number(n.getAttribute("data-value"))));
      assert.ok(values.some((v) => v !== values[0]),
        "every bar carries the same value; the chart is not reading the data");

      // EVERY chart, one after the other. `choose` used `.find()`, so an
      // answer carrying three charts rendered one -- which is how "a line
      // chart per product" came back as a single bar chart.
      const figures = await page.$$eval('[data-testid="v4-result-chart"]',
        (nodes) => nodes.map((n) => n.getAttribute("data-kind")));
      assert.ok(figures.length >= 3,
        `the analyst sent three charts and ${figures.length} were drawn`);
      assert.equal(new Set(figures).size, figures.length,
        `each chart keeps its own form: ${figures.join(", ")}`);
      // And each form is drawn as itself rather than falling through to
      // bars: a line has a line, a donut has slices.
      await expect(page, '[data-testid="v4-chart-line"]', 10_000, problems);
      await expect(page, '[data-testid="v4-chart-donut"]', 10_000, problems);

      // The exact figures are right there, not one click away: the toggle
      // that used to hide one behind the other is gone.
      await expect(page, '[data-testid="v4-result-table"]', 10_000, problems);
      const rows = await page.$$('[data-testid="v4-table-row"]');
      assert.ok(rows.length >= 2, "the table shows the rows behind the chart");
    } finally {
      await context.close();
    }
  });

await test("the active stage counts seconds while it is working", async () => {
  // §64: the acceptance is that a reader can WATCH the number change. The
  // stub is deliberately slow so there is something to watch.
  const { context, page, problems } = await openCockpit(browser);
  try {
    await ask(page, "slowly answer");
    await expect(page, '[data-testid="v4-process-panel"]', 20_000, problems);
    await page.click('[data-testid="v4-toggle-process"]');

    const readSummary = async () =>
      (await page.textContent('[data-testid="v4-process-summary"]')) ?? "";

    const first = await page.waitForFunction(
      () => {
        const text = document.querySelector(
          '[data-testid="v4-process-summary"]')?.textContent ?? "";
        const match = /(\d+)s elapsed/.exec(text);
        return match ? Number(match[1]) : false;
      },
      { timeout: 30_000 },
    );
    const started = await first.jsonValue();

    const moved = await page.waitForFunction(
      (from) => {
        const text = document.querySelector(
          '[data-testid="v4-process-summary"]')?.textContent ?? "";
        const match = /(\d+)s elapsed/.exec(text);
        return match && Number(match[1]) > from ? Number(match[1]) : false;
      },
      started,
      { timeout: 30_000 },
    );
    const later = await moved.jsonValue();
    assert.ok(later > started,
      `the clock did not move: ${started}s then ${await readSummary()}`);
  } finally {
    await context.close();
  }
});

// ---- the home feed, the drawer, and Investigate Further ----------------

async function openDrawer(page, section = "segments-requiring-attention") {
  await page.waitForSelector(
    `[data-testid="${section}"] [data-testid="attention-card"]`,
    { timeout: 60_000 },
  );
  const cards = await page.$$(
    `[data-testid="${section}"] [data-testid="attention-card"]`,
  );
  const headline = (await cards[0].textContent()) ?? "";
  const segment = await cards[0].getAttribute("data-segment");
  await cards[0].click();
  await page.waitForSelector('[data-testid="attention-drawer"]', {
    timeout: 30_000,
  });
  return { count: cards.length, headline, segment };
}

await test("segments requiring attention loads from the pinned release",
  async () => {
    const { context, page, requests } = await openCockpit(browser);
    try {
      await page.waitForSelector('[data-testid="segments-requiring-attention"]',
        { timeout: 60_000 });
      const cards = await page.$$(
        '[data-testid="segments-requiring-attention"] [data-testid="attention-card"]',
      );
      assert.ok(
        cards.length >= 1 && cards.length <= 5,
        `expected up to five segment cards, saw ${cards.length}`,
      );
      const segments = await Promise.all(
        cards.map((card) => card.getAttribute("data-segment")),
      );
      assert.equal(
        new Set(segments).size,
        segments.length,
        "five cards must be five different segments",
      );
      assert.ok(
        calls(requests, "/api/v1/cockpit-v4/attention").length >= 1,
        "the feed comes from the V4 attention endpoint",
      );
      assertNoLegacyCalls(requests, "for the attention feed");
    } finally {
      await context.close();
    }
  },
);

await test("latest-quarter ECL highlights load below it", async () => {
  const { context, page } = await openCockpit(browser);
  try {
    await page.waitForSelector('[data-testid="ecl-highlights"]',
      { timeout: 60_000 });
    const cards = await page.$$(
      '[data-testid="ecl-highlights"] [data-testid="attention-card"]',
    );
    assert.ok(cards.length >= 1, "at least one ECL highlight renders");
    const footnote = await page.textContent('[data-testid="attention-footnote"]');
    assert.match(footnote ?? "", /no model call/);
    // Order on the page: attention above highlights.
    const order = await page.evaluate(() => {
      const a = document.querySelector('[data-testid="segments-requiring-attention"]');
      const b = document.querySelector('[data-testid="ecl-highlights"]');
      if (!a || !b) return "missing";
      return a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING
        ? "attention-first"
        : "highlights-first";
    });
    assert.equal(order, "attention-first");
  } finally {
    await context.close();
  }
});

await test("clicking a segment card opens the right-side drawer", async () => {
  const { context, page } = await openCockpit(browser);
  try {
    const { headline, segment } = await openDrawer(page);
    const title = await page.textContent('[data-testid="attention-drawer-title"]');
    assert.ok(
      headline.includes(title ?? "__none__"),
      `drawer title ${title} must match the card that was clicked`,
    );
    const why = await page.textContent('[data-testid="attention-drawer-why"]');
    assert.match(why ?? "", /Why it appeared/);
    // The card says WHY it is on the page: how far it moved, and how much
    // of the book it is. The wording changed with the per-domain engine;
    // the two facts it has to carry did not.
    {
      const feed = await feedFor("");
      const calendar = calendarOf(feed.domain_id);
      assert.match(why ?? "", /higher than /);
      assert.ok(calendar.own.test(why ?? ""),
        `the drawer must compare against a ${calendar.noun}: ${why}`);
      assert.ok(!calendar.foreign.test(why ?? ""),
        `the drawer wrote the other book's calendar: ${why}`);
    }
    assert.match(why ?? "", /of the book's exposure at default/);
    const numbers = await page.$$(
      '[data-testid="attention-drawer-numbers"] dd',
    );
    assert.ok(numbers.length >= 3, "the drawer shows its key numbers");
    assert.ok(segment, "the card names its segment");
    // It is a right-side panel, not a navigation.
    const box = await (await page.$('[data-testid="attention-drawer"]')).boundingBox();
    const width = page.viewportSize()?.width ?? 1280;
    assert.ok(box.x > width / 2, "the drawer sits on the right");
    assert.match(page.url(), /\/(\?.*)?$/, "the Cockpit was not navigated away");
  } finally {
    await context.close();
  }
});

await test("the drawer offers borrower drill-down and no invented subsegment",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      await openDrawer(page);
      const drill = await page.textContent(
        '[data-testid="attention-drawer-drilldown"]',
      );
      // Where the book stops, and how many counterparties are behind this
      // segment. The noun belongs to the BOOK: a corporate sector holds
      // borrowers and a retail product holds customers, so asserting
      // "borrowers" here would be asserting that the page names one of them
      // whatever it is showing.
      assert.match(drill ?? "",
        /no subsegment level|has a level below it in this book/);
      assert.match(drill ?? "", /holds \d+ (borrowers|customers|facilities)/);
    } finally {
      await context.close();
    }
  },
);

await test("possible drivers are stated as association, never as cause",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      await openDrawer(page);
      const drivers = await page
        .textContent('[data-testid="attention-drawer-drivers"]')
        .catch(() => "");
      if (drivers) {
        for (const causal of ["because", "caused by", "driven by"]) {
          assert.ok(
            !drivers.toLowerCase().includes(causal),
            `a driver claimed cause: "${causal}"`,
          );
        }
      }
    } finally {
      await context.close();
    }
  },
);

await test("Investigate Further opens a seeded V4 thread", async () => {
  const { context, page, requests } = await openCockpit(browser);
  try {
    const { segment } = await openDrawer(page);
    await page.click('[data-testid="attention-investigate"]');
    await page.waitForSelector('[data-testid="investigation-context"]', {
      timeout: 30_000,
    });
    const banner = await page.$('[data-testid="investigation-context"]');
    assert.equal(await banner.getAttribute("data-segment"), segment);
    const threadId = await banner.getAttribute("data-thread-id");
    assert.ok(threadId?.startsWith("th-"), `a real thread id, got ${threadId}`);
    assert.ok(
      calls(requests, "/investigate").length === 1,
      "exactly one V4 investigate call",
    );
    assertNoLegacyCalls(requests, "for Investigate Further");
  } finally {
    await context.close();
  }
});

await test("a follow-up uses the seeded context without restating it",
  async () => {
    const { context, page, requests, problems } = await openCockpit(browser);
    try {
      const { segment } = await openDrawer(page);
      await page.click('[data-testid="attention-investigate"]');
      await page.waitForSelector('[data-testid="investigation-context"]',
        { timeout: 30_000 });
      const threadId = await page
        .$('[data-testid="investigation-context"]')
        .then((el) => el.getAttribute("data-thread-id"));

      const posted = [];
      page.on("request", (request) => {
        // Only the SUBMISSION. `/runs/<id>/delivered` and `/runs/<id>/cancel`
        // are POSTs to a URL containing "/runs" as well.
        if (request.method() === "POST" && /\/runs$/.test(new URL(request.url()).pathname)) {
          posted.push(request.postData() ?? "");
        }
      });

      // From inside the thread: after Investigate Further the reader is
      // in the conversation, and the composer is where they continue.
      await followUp(page, "show me the customers behind this");
      await waitForAnswer(page);

      assert.equal(posted.length, 1, "one run submitted");
      const body = JSON.parse(posted[0]);
      assert.equal(
        body.thread_id,
        threadId,
        "the follow-up runs inside the seeded thread",
      );
      assert.equal(body.question, "show me the customers behind this");
      assert.ok(
        !body.question.includes(segment ?? "__none__"),
        "the user did not have to restate the segment",
      );
      assertNoLegacyCalls(requests, "for the seeded follow-up");
    } finally {
      await context.close();
    }
  },
);

await test("the seeded context load appears in the process panel", async () => {
  const { context, page, problems } = await openCockpit(browser);
  try {
    await openDrawer(page);
    await page.click('[data-testid="attention-investigate"]');
    await page.waitForSelector('[data-testid="investigation-context"]',
      { timeout: 30_000 });
    await followUp(page, "show me the customers behind this");
    await expect(page, '[data-testid="v4-process-panel"]', 30_000, problems);
    await page.click('[data-testid="v4-toggle-process"]');
    await expect(page, '[data-testid="v4-process-steps"]', 30_000, problems);
    const panel = await traceContains(page, /Investigation context loaded/);
    assert.match(
      panel,
      /Investigation context loaded/,
      "context seeding is a real step and the trace shows it. Panel was: "
        + JSON.stringify(panel),
    );
    assert.ok(
      !/chain of thought|reasoning:/i.test(panel ?? ""),
      "the trace shows steps, never the model's reasoning",
    );
  } finally {
    await context.close();
  }
});

await test("an ECL highlight opens the same drawer and investigates",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      const { segment } = await openDrawer(page, "ecl-highlights");
      const numbers = await page.$$(
        '[data-testid="attention-drawer-numbers"] dd',
      );
      assert.ok(numbers.length >= 2, "an ECL highlight shows its numbers");
      await page.click('[data-testid="attention-investigate"]');
      await page.waitForSelector('[data-testid="investigation-context"]',
        { timeout: 30_000 });
      const banner = await page.$('[data-testid="investigation-context"]');
      assert.equal(await banner.getAttribute("data-segment"), segment);
    } finally {
      await context.close();
    }
  },
);

await test("a refresh keeps the open investigation", async () => {
  const { context, page } = await openCockpit(browser);
  try {
    const { segment } = await openDrawer(page);
    await page.click('[data-testid="attention-investigate"]');
    await page.waitForSelector('[data-testid="investigation-context"]',
      { timeout: 30_000 });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="investigation-context"]',
      { timeout: 30_000 });
    const banner = await page.$('[data-testid="investigation-context"]');
    assert.equal(
      await banner.getAttribute("data-segment"),
      segment,
      "the same investigation comes back after a reload",
    );
  } finally {
    await context.close();
  }
});

await test("an attention-feed failure does not break Ask", async () => {
  const context = await newContext(browser);
  const page = await context.newPage();
  const problems = [];
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text()}`);
  });
  try {
    await page.route("**/api/v1/cockpit-v4/attention**", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            error_code: "ATTENTION_UNAVAILABLE",
            message: "the aggregate query did not return.",
            component: "segment_attention_feed",
            error_reference: "att-deadbeef1234",
          },
        }),
      }),
    );
    await page.goto(`${UI}/`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="attention-unavailable"]',
      { timeout: 60_000 });
    const text = await page.textContent('[data-testid="attention-unavailable"]');
    assert.match(text ?? "", /Segment attention feed unavailable/);
    assert.match(text ?? "", /att-deadbeef1234/);
    assert.ok(
      !/Not Found|something failed/i.test(text ?? ""),
      "a component failure is named, not disguised as a 404",
    );

    await ask(page, "Who are you?");
    await waitForAnswer(page);
  } finally {
    await context.close();
  }
});

// ---- the restored landing page ----------------------------------------

await test("the landing page greets, then asks what is on your mind",
  async () => {
    const { context, page, requests } = await openCockpit(browser);
    try {
      const heading = await page.textContent('[data-testid="cockpit-v4-greeting"]');
      assert.match(
        heading ?? "",
        /^Good (morning|afternoon|evening)/,
        `the greeting must be time-aware, got ${heading}`,
      );
      // A name appears only when the session has one. It is never invented.
      const named = await page.$('[data-testid="cockpit-v4-greeting-name"]');
      if (named) {
        const name = (await named.textContent())?.trim() ?? "";
        assert.ok(name.length > 0, "an empty name must not be rendered");
        assert.ok(
          !/^(user|there|admin)$/i.test(name),
          `a placeholder is not a name: ${name}`,
        );
      }
      const line = await page.textContent('[data-testid="cockpit-v4-prompt-line"]');
      assert.match(line ?? "", /What.s on your mind\?/);
      assertNoLegacyCalls(requests, "for the landing page");
    } finally {
      await context.close();
    }
  },
);

await test("the Ask box is the primary element and spans the workspace",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      const box = await page.$('[data-testid="cockpit-v4-ask-box"]');
      assert.ok(box, "the Ask box renders");
      const boxRect = await box.boundingBox();
      const home = await (await page.$('[data-testid="cockpit-v4-home"]'))
        .boundingBox();
      assert.ok(
        boxRect.width > home.width * 0.85,
        `the Ask box should span the workspace: ${boxRect.width} of ${home.width}`,
      );
      // And it sits above what requires attention, not beside or below it.
      const attention = await (
        await page.$('[data-testid="segments-requiring-attention"]')
      ).boundingBox();
      assert.ok(
        boxRect.y + boxRect.height <= attention.y,
        "the question box comes before the attention feed",
      );
      // Nothing reserves space for the process panel before a run exists.
      assert.equal(
        await page.$('[data-testid="v4-process-panel"]'),
        null,
        "an idle landing page shows no process panel",
      );
    } finally {
      await context.close();
    }
  },
);

await test("prompt chips render, ask when clicked, and can be dismissed",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      const chips = await page.$$('[data-testid="cockpit-v4-prompt"]');
      assert.ok(chips.length >= 3, `expected prompt chips, saw ${chips.length}`);
      const texts = await Promise.all(chips.map((c) => c.textContent()));
      assert.ok(
        texts.some((text) => /risk|exposure|Stage 2|EAD|sector/i.test(text ?? "")),
        `chips must be business prompts, got ${JSON.stringify(texts)}`,
      );

      await page.click('[data-testid="cockpit-v4-prompts-dismiss"]');
      assert.equal(
        await page.$('[data-testid="cockpit-v4-prompts"]'),
        null,
        "dismissing hides the chips",
      );

      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page, '[data-testid="cockpit-v4-prompt"]', 30_000, problems);
      const first = (await page.$$('[data-testid="cockpit-v4-prompt"]'))[0];
      await first.click();
      await expect(page, '[data-testid="v4-process-panel"]', 30_000, problems);
    } finally {
      await context.close();
    }
  },
);

await test("the Trace line is there and explains itself", async () => {
  const { context, page } = await openCockpit(browser);
  try {
    const note = await page.textContent('[data-testid="cockpit-v4-trace-note"]');
    assert.match(note ?? "", /Every answer carries a Trace/);
    await page.click('[data-testid="cockpit-v4-trace-explain"]');
    const help = await page.textContent('[data-testid="cockpit-v4-trace-help"]');
    assert.match(help ?? "", /bound to a query that actually ran/);
  } finally {
    await context.close();
  }
});

await test("Segments requiring attention shows its period and only segments",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      await page.waitForSelector('[data-testid="attention-reporting-period"]',
        { timeout: 60_000 });
      const period = await page.textContent(
        '[data-testid="attention-reporting-period"]',
      );
      // The period comes from the RELEASE, in the calendar that release
      // keeps. Naming one calendar here was right while both books had it
      // and is a false expectation now: a quarterly book described as
      // 2026-08, or a monthly one as Q2 2026, would each be the page
      // inventing a calendar.
      const feed = await feedFor("");
      const calendar = calendarOf(feed.domain_id);
      assert.match(
        period ?? "",
        new RegExp(`Reporting ${calendar.noun} `),
        `the heading must name this book's period, got ${period}`,
      );
      assert.ok(calendar.own.test(period ?? ""),
        `the period must be a ${calendar.noun}, got ${period}`);
      assert.ok(!calendar.foreign.test(period ?? ""),
        `the page wrote the other book's calendar: ${period}`);
      // And it is THE period the release publishes, not merely one shaped
      // like it.
      const shown = (period ?? "").replace(/^Reporting \w+ /, "").trim();
      const expected = feed.reporting_period;
      assert.ok(
        shown === expected
          || shown === expected.replace(/^(\d{4})Q([1-4])$/, "Q$2 $1"),
        `the page shows ${shown} and the release says ${expected}`,
      );
      const heading = await page.textContent(
        '[data-testid="segments-requiring-attention"] h2',
      );
      assert.match(heading ?? "", /Segments requiring attention/);

      // No tab merges the two dashboards back together.
      assert.equal(
        await page.$('[data-testid="attention-tabs"]'),
        null,
        "the All tab existed only to merge two feeds that are both visible",
      );

      const cards = await page.$$(
        '[data-testid="segments-requiring-attention"] [data-testid="attention-card"]',
      );
      assert.ok(
        cards.length >= 1 && cards.length <= 5,
        `expected up to five segment cards, saw ${cards.length}`,
      );
      for (const card of cards) {
        assert.equal(
          await card.getAttribute("data-scope"),
          "segment",
          `a segment card must be about a segment: ${await card.textContent()}`,
        );
        const segment = await card.getAttribute("data-segment");
        assert.ok(segment && segment !== "Whole book", segment ?? "");
      }
    } finally {
      await context.close();
    }
  },
);

await test("the two dashboards are distinct and share no card", async () => {
  const { context, page } = await openCockpit(browser);
  try {
    await page.waitForSelector('[data-testid="ecl-highlights"]',
      { timeout: 60_000 });
    const read = async (section) =>
      Promise.all(
        (
          await page.$$(`[data-testid="${section}"] [data-testid="attention-card"]`)
        ).map(async (card) => ({
          id: await card.getAttribute("data-item-id"),
          scope: await card.getAttribute("data-scope"),
          text: ((await card.textContent()) ?? "").trim(),
        })),
      );

    const segments = await read("segments-requiring-attention");
    const highlights = await read("ecl-highlights");
    assert.ok(segments.length >= 1 && highlights.length >= 1);

    const ids = new Set(segments.map((c) => c.id));
    for (const card of highlights) {
      assert.ok(!ids.has(card.id), `duplicated across dashboards: ${card.text}`);
    }
    const headlines = new Set(segments.map((c) => c.text));
    for (const card of highlights) {
      assert.ok(
        !headlines.has(card.text),
        `the same card appears twice on the page: ${card.text}`,
      );
    }

    // The specific items the live screenshot showed in the wrong list.
    const upper = segments.map((c) => c.text).join(" | ");
    for (const wrong of [
      /carries the most ECL in the book/,
      /is the largest single ECL contributor/,
      /Stage 2 share of the book/,
      /had the largest ECL reduction/,
      /had the largest ECL increase/,
    ]) {
      assert.ok(
        !wrong.test(upper),
        `an ECL highlight is in the segment list: ${wrong}`,
      );
    }

    // And the ECL feed keeps the borrower and book-level highlights it is
    // designed to carry.
    const scopes = new Set(highlights.map((c) => c.scope));
    assert.ok(scopes.has("segment"), "sector-level ECL highlights remain");
  } finally {
    await context.close();
  }
});

// ---- the two books, in the browser -------------------------------------

async function switchTo(page, domain) {
  await page.waitForSelector('[data-testid="domain-switch"]', {
    timeout: 60_000,
  });
  await page.click(`[data-testid="domain-${domain}"]`);
  await page.waitForFunction(
    (want) =>
      document
        .querySelector('[data-testid="domain-switch"]')
        ?.getAttribute("data-domain") === want,
    domain,
    { timeout: 30_000 },
  );
  await page.waitForFunction(
    (want) => {
      const cards = document.querySelectorAll(
        '[data-testid="segments-requiring-attention"] ' +
          '[data-testid="attention-card"]',
      );
      const panel = document.querySelector('[data-testid="v4-ecl-panel"]');
      return (
        cards.length > 0 && panel?.getAttribute("data-domain") === want
      );
    },
    domain,
    { timeout: 60_000 },
  );
}

async function cardText(page, section) {
  return (
    await Promise.all(
      (
        await page.$$(
          `[data-testid="${section}"] [data-testid="attention-card"]`,
        )
      ).map(async (card) => ((await card.textContent()) ?? "").trim()),
    )
  ).sort();
}

await test("switching the book asks the server again, it is not a filter",
  async () => {
    const { context, page, requests } = await openCockpit(browser);
    try {
      await switchTo(page, "corporate");
      const before = requests.length;
      const corporate = await cardText(page, "segments-requiring-attention");

      await switchTo(page, "retail");
      const retail = await cardText(page, "segments-requiring-attention");

      // A NEW request, naming the book. A client-side filter over one
      // payload would show different cards and issue nothing.
      const asked = calls(requests.slice(before), "/attention").filter((u) =>
        u.includes("domain=retail"),
      );
      assert.ok(
        asked.length >= 1,
        "switching to Retail must ask the server for the Retail book",
      );
      assert.ok(
        calls(requests.slice(before), "/ecl").some((u) =>
          u.includes("domain=retail"),
        ),
        "the ECL panel must follow the switch",
      );
      assert.notDeepEqual(
        corporate,
        retail,
        "the two books must not show the same cards",
      );
      for (const card of retail) {
        assert.ok(
          !corporate.includes(card),
          `a card appears in both books: ${card}`,
        );
      }
    } finally {
      await context.close();
    }
  },
);

await test("each book shows its own release, currency and ECL position",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      const seen = {};
      for (const domain of ["corporate", "retail"]) {
        await switchTo(page, domain);
        seen[domain] = {
          release: await page.getAttribute(
            '[data-testid="v4-ecl-panel"]',
            "data-release",
          ),
          ecl: await page.textContent('[data-testid="v4-ecl-ecl"]'),
          ead: await page.textContent('[data-testid="v4-ecl-ead"]'),
          coverage: await page.textContent(
            '[data-testid="v4-ecl-coverage"]',
          ),
          reconciles: await page.textContent(
            '[data-testid="v4-ecl-reconciliation"]',
          ),
        };
      }
      assert.notEqual(seen.corporate.release, seen.retail.release);
      assert.notEqual(seen.corporate.ecl, seen.retail.ecl);
      assert.notEqual(seen.corporate.ead, seen.retail.ead);
      for (const domain of ["corporate", "retail"]) {
        assert.match(seen[domain].ead, /SAR [\d,]+ million/);
        assert.match(seen[domain].coverage, /\d+\.\d{2}%/);
        assert.match(
          seen[domain].reconciles,
          /sum to the movement exactly/,
          `${domain}: the decomposition must state that it reconciles`,
        );
      }
    } finally {
      await context.close();
    }
  },
);

await test("switching back shows the first book's numbers, not a cached other",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      await switchTo(page, "corporate");
      const first = await page.textContent('[data-testid="v4-ecl-ecl"]');
      const firstCards = await cardText(page, "segments-requiring-attention");
      await switchTo(page, "retail");
      const retail = await page.textContent('[data-testid="v4-ecl-ecl"]');
      await switchTo(page, "corporate");
      const again = await page.textContent('[data-testid="v4-ecl-ecl"]');
      const againCards = await cardText(page, "segments-requiring-attention");

      assert.notEqual(first, retail);
      assert.equal(
        first,
        again,
        "returning to Corporate must show Corporate's ECL, not Retail's",
      );
      assert.deepEqual(firstCards, againCards);
    } finally {
      await context.close();
    }
  },
);

/**
 * What the SERVER says each book's calendar is.
 *
 * Read from `/domains`, not decided here: the point of these tests is that
 * the page takes its calendar from the release, so a test that carried its
 * own table of frequencies would pass a page that carried one too.
 */
async function publishedCalendars() {
  const body = await (
    await fetch(`${API}/api/v1/cockpit-v4/domains`)
  ).json();
  const out = {};
  for (const entry of body.domains ?? []) {
    const frequency = entry.reporting_frequency ?? "";
    const noun =
      entry.period_noun ?? (frequency === "quarterly" ? "quarter" : "month");
    out[entry.domain_id] = {
      frequency,
      noun,
      foreign: noun === "quarter" ? "month" : "quarter",
      country: entry.country ?? "",
      money: `${entry.reporting_currency ?? ""} ${entry.amount_scale ?? ""}`
        .trim(),
    };
  }
  return out;
}

/** Home's cover line and its chips, as a reader sees them right now. */
async function homeCalendar(page) {
  const meta = (
    await page.textContent('[data-testid="cockpit-v4-domain-meta"]')
  ) ?? "";
  const chips = await Promise.all(
    (await page.$$('[data-testid="cockpit-v4-prompt"]')).map(
      async (chip) => ((await chip.textContent()) ?? "").trim()),
  );
  return { meta: meta.trim(), chips };
}

/**
 * THE LIVE DEFECT, as a test.
 *
 * Corporate Home read `Saudi Arabia · SAR million · monthly` under a book
 * that reports quarters, and offered "Which sectors deteriorated most this
 * month?" -- while the attention section on the same screen read
 * `Reporting quarter Q2 2026`. This fails on that build.
 */
await test("Home states each book's own frequency, from the release",
  async () => {
    const published = await publishedCalendars();
    const { context, page } = await openCockpit(browser);
    try {
      for (const domain of ["corporate", "retail"]) {
        const book = published[domain];
        assert.ok(book?.frequency, `/domains published no frequency for ${domain}`);
        await switchTo(page, domain);
        await page.waitForFunction(
          (want) =>
            (document
              .querySelector('[data-testid="cockpit-v4-domain-meta"]')
              ?.textContent ?? "").includes(want),
          book.frequency,
          { timeout: 30_000 },
        );
        const { meta } = await homeCalendar(page);
        assert.ok(
          meta.includes(book.frequency),
          `${domain} Home says "${meta}", not ${book.frequency}`,
        );
        assert.ok(
          !meta.includes(domain === "retail" ? "quarterly" : "monthly"),
          `${domain} Home carries the other book's frequency: ${meta}`,
        );
        if (book.country) assert.ok(meta.includes(book.country), meta);
        if (book.money) assert.ok(meta.includes(book.money), meta);
      }
    } finally {
      await context.close();
    }
  },
);

await test("prompt chips are asked in the selected book's own period",
  async () => {
    const published = await publishedCalendars();
    const { context, page } = await openCockpit(browser);
    try {
      for (const domain of ["corporate", "retail"]) {
        const book = published[domain];
        await switchTo(page, domain);
        await page.waitForFunction(
          (want) =>
            Array.from(
              document.querySelectorAll('[data-testid="cockpit-v4-prompt"]'),
            ).some((chip) => (chip.textContent ?? "").includes(want)),
          book.noun,
          { timeout: 30_000 },
        );
        const { chips } = await homeCalendar(page);
        assert.ok(chips.length >= 3, `${domain} offered ${chips.length} chips`);
        assert.ok(
          chips.some((chip) => chip.includes(`latest ${book.noun}`)),
          `${domain} offers no "latest ${book.noun}" chip: ` +
            JSON.stringify(chips),
        );
        assert.ok(
          chips.some((chip) => chip.includes(`this ${book.noun}`)),
          `${domain} offers no "this ${book.noun}" chip: ` +
            JSON.stringify(chips),
        );
        for (const chip of chips) {
          assert.ok(
            !new RegExp(`\\b${book.foreign}`, "i").test(chip),
            `${domain} chip names the other calendar: ${chip}`,
          );
        }
      }
    } finally {
      await context.close();
    }
  },
);

await test("Home and the dashboard under it agree on the calendar",
  async () => {
    // The screenshot had both on one screen: `monthly` in the cover line and
    // `Reporting quarter Q2 2026` eight inches below it.
    const { context, page } = await openCockpit(browser);
    try {
      for (const domain of ["corporate", "retail"]) {
        const { noun } = calendarOf(domain);
        await switchTo(page, domain);
        await page.waitForFunction(
          (want) =>
            (document
              .querySelector('[data-testid="cockpit-v4-domain-meta"]')
              ?.textContent ?? "").includes(want),
          noun === "quarter" ? "quarterly" : "monthly",
          { timeout: 30_000 },
        );
        const heading = (
          await page.textContent('[data-testid="attention-reporting-period"]')
        ) ?? "";
        const { meta, chips } = await homeCalendar(page);
        assert.match(heading.toLowerCase(), new RegExp(`reporting ${noun}`));
        assert.ok(meta.includes(noun === "quarter" ? "quarterly" : "monthly"),
          `cover line ${meta} disagrees with heading ${heading}`);
        for (const chip of chips) {
          assert.ok(
            !new RegExp(`\\b${noun === "quarter" ? "month" : "quarter"}`, "i")
              .test(chip),
            `chip ${chip} disagrees with heading ${heading}`,
          );
        }
      }
    } finally {
      await context.close();
    }
  },
);

await test("five switch cycles leave no calendar behind", async () => {
  // Requirement 7: the frequency comes from the book's metadata, not from
  // whatever was on screen a moment ago. Five round trips, asserted on every
  // leg, because a stale-state defect that survives one switch usually shows
  // up on the second.
  const published = await publishedCalendars();
  const { context, page } = await openCockpit(browser);
  try {
    for (let cycle = 0; cycle < 5; cycle += 1) {
      for (const domain of ["corporate", "retail"]) {
        const book = published[domain];
        await switchTo(page, domain);
        await page.waitForFunction(
          (want) =>
            (document
              .querySelector('[data-testid="cockpit-v4-domain-meta"]')
              ?.getAttribute("data-frequency") ?? "") === want,
          book.frequency,
          { timeout: 30_000 },
        );
        const { meta, chips } = await homeCalendar(page);
        assert.ok(meta.includes(book.frequency),
          `cycle ${cycle}: ${domain} cover line reads ${meta}`);
        for (const chip of chips) {
          assert.ok(
            !new RegExp(`\\b${book.foreign}`, "i").test(chip),
            `cycle ${cycle}: ${domain} chip ${chip}`,
          );
        }
        const heading = (
          await page.textContent('[data-testid="attention-reporting-period"]')
        ) ?? "";
        assert.match(heading.toLowerCase(),
          new RegExp(`reporting ${book.noun}`),
          `cycle ${cycle}: ${domain} heading reads ${heading}`);
      }
    }
  } finally {
    await context.close();
  }
});

await test("the drawer's seed note names the card's own period", async () => {
  const { context, page } = await openCockpit(browser);
  try {
    for (const domain of ["corporate", "retail"]) {
      const { noun } = calendarOf(domain);
      await switchTo(page, domain);
      await openDrawer(page);
      const note = (
        await page.textContent('[data-testid="attention-investigate-note"]')
      ) ?? "";
      assert.ok(note.includes(noun), `${domain} seed note reads ${note}`);
      assert.ok(
        !note.includes(noun === "quarter" ? "month" : "quarter"),
        `${domain} seed note names the other calendar: ${note}`,
      );
      const review = (
        await page.textContent('[data-testid="attention-drawer"]')
      ) ?? "";
      assert.ok(
        !new RegExp(`same ${noun === "quarter" ? "month" : "quarter"}`, "i")
          .test(review),
        `${domain} drawer reviews the other calendar`,
      );
      await page.keyboard.press("Escape");
    }
  } finally {
    await context.close();
  }
});

await test("a question asked in a book opens a thread badged with that book",
  async () => {
    for (const domain of ["corporate", "retail"]) {
      const { context, page } = await openCockpit(browser);
      try {
        await switchTo(page, domain);
        await ask(page, "What is exposure at default this month?");
        await waitForAnswer(page, 90_000);
        const badge = await page.textContent(
          '[data-testid="v4-thread-domain"]',
        );
        assert.match(
          badge ?? "",
          domain === "corporate" ? /Corporate/ : /Retail/,
          `a ${domain} question opened a thread badged ${badge}`,
        );
      } finally {
        await context.close();
      }
    }
  },
);

await test("both dashboards open the same right-hand drawer", async () => {
  const { context, page } = await openCockpit(browser);
  try {
    for (const section of ["segments-requiring-attention", "ecl-highlights"]) {
      const { headline } = await openDrawer(page, section);
      const title = await page.textContent(
        '[data-testid="attention-drawer-title"]',
      );
      assert.ok(
        headline.includes(title ?? "__none__"),
        `${section}: drawer title ${title} must match the card clicked`,
      );
      await page.click('[data-testid="attention-drawer-close"]');
    }
  } finally {
    await context.close();
  }
});

await test("Continue where you left off uses real V4 threads", async () => {
  const { context, page, requests, problems } = await openCockpit(browser);
  try {
    await page.waitForSelector('[data-testid="continue-where-you-left-off"]',
      { timeout: 60_000 });
    // Before anything has run in this browser profile it is either empty or
    // showing threads this tenant really has. It is never invented rows.
    const empty = await page.$('[data-testid="continue-empty"]');
    const before = await page.$$('[data-testid="continue-thread"]');
    assert.ok(empty || before.length > 0);

    await ask(page, "Who are you?");
    await waitForAnswer(page);
    // Asking now opens a thread at its own URL, so the landing page has to
    // be revisited -- reloading would only reload the conversation.
    await page.click('[data-testid="v4-back-to-cockpit"]');
    await page.waitForSelector('[data-testid="continue-where-you-left-off"]',
      { timeout: 60_000 });
    await page.waitForSelector('[data-testid="continue-thread"]',
      { timeout: 60_000 });
    const after = await page.$$('[data-testid="continue-thread"]');
    assert.ok(after.length >= 1, "the conversation just held is listed");
    const label = await after[0].textContent();
    assert.match(label ?? "", /Who are you\?|Cockpit conversation/);
    assertNoLegacyCalls(requests, "for the thread list");
  } finally {
    await context.close();
  }
});

await test("an Arabic answer renders right-to-left without breaking the page",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await ask(page, "ما هو CreditProbe؟");
      await waitForAnswer(page);
      // dir="auto" resolves from the content, so an Arabic question reads
      // right-to-left where it is shown -- in the transcript.
      const direction = await page.evaluate(() => {
        const turn = document.querySelector('[data-testid="v4-turn-user"] p');
        return turn ? getComputedStyle(turn).direction : "";
      });
      assert.equal(direction, "rtl");
      // And the composer that takes the next one flips with its content too.
      const composer = await page.evaluate(() => {
        const box = document.querySelector('[data-testid="v4-composer-input"]');
        return box ? box.getAttribute("dir") : "";
      });
      assert.equal(composer, "auto");
      const body = await page.evaluate(
        () => document.body.scrollWidth <= window.innerWidth + 2,
      );
      assert.ok(body, "an RTL answer must not force a horizontal scroll");
    } finally {
      await context.close();
    }
  },
);

await test("the landing page mounts the V4 Cockpit and nothing legacy",
  async () => {
    const { context, page, requests } = await openCockpit(browser);
    try {
      assert.ok(await page.$('[data-testid="cockpit-v4-home"]'));
      assert.ok(await page.$('[data-testid="cockpit-v4-question"]'));
      // The legacy Cockpit's own markers must be absent, not merely hidden.
      for (const legacy of [
        '[data-testid="cockpit-home"]',
        '[data-testid="agentic-officer"]',
        '[data-testid="investigation-panel"]',
      ]) {
        assert.equal(await page.$(legacy), null, `${legacy} must not mount`);
      }
      const stray = requests.filter((url) => /:8000(\/|$)/.test(url));
      assert.deepEqual(stray, []);
      assertNoLegacyCalls(requests, "for the restored landing page");
    } finally {
      await context.close();
    }
  },
);


/* ---- the demonstration is Saudi ------------------------------------- */
/*
 * A Saudi credit-risk demonstration that says "INR crore" anywhere a reader
 * can see is not a localization bug, it is the wrong product. Checked
 * against the rendered page rather than against the source, because the
 * source was clean while the runtime still fell back to INR.
 */

/*
 * The currency assertions follow the SELECTED release rather than naming one.
 * A Saudi book must not show INR and an INR book must not show SAR, and the
 * failure that started this was a hard-coded default -- so hard-coding the
 * expectation here would test the wrong property.
 */
const RELEASE = process.env.V4_RELEASE ?? "v4-saudi-20q-v1";
const DENOMINATION = {
  "v4-saudi-20q-v1": { expect: "SAR", forbid: ["INR", "crore", "lakh",
                                               "\u20b9", "rupee"] },
  "v4-uat-20q-v1": { expect: "INR", forbid: ["SAR"] },
};
const MONEY = DENOMINATION[RELEASE] ?? { expect: "", forbid: [] };
const INDIA_WORDS = MONEY.forbid;

async function visibleText(page) {
  return page.evaluate(() => document.body.innerText);
}

await test("no page shows a currency the selected release does not use",
  async () => {
    const { context, page, problems } = await openCockpit(browser);
    try {
      await page.waitForSelector('[data-testid="attention-card"]',
        { timeout: 60_000 });
      const text = await visibleText(page);
      for (const word of INDIA_WORDS) {
        assert.ok(
          !new RegExp(word, "i").test(text),
          `the landing page shows ${word}`,
        );
      }
      if (MONEY.expect) {
        assert.ok(
          new RegExp(MONEY.expect).test(text),
          `release ${RELEASE} should show ${MONEY.expect} on the landing page`,
        );
      }
    } finally {
      await context.close();
    }
  },
);

await test("attention cards and ECL highlights use the release's currency",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      await page.waitForSelector('[data-testid="attention-card"]',
        { timeout: 60_000 });
      const money = await page.evaluate(() => {
        const cards = [...document.querySelectorAll(
          '[data-testid="attention-card"]')];
        return cards.map((c) => c.innerText).join(" \n ");
      });
      assert.ok(money.length > 0, "there are attention cards to read");
      for (const word of INDIA_WORDS) {
        assert.ok(!new RegExp(word, "i").test(money),
          `an attention card shows ${word}`);
      }
    } finally {
      await context.close();
    }
  },
);

await test("borrower names in the drawer belong to the selected release",
  async () => {
    if (RELEASE !== "v4-saudi-20q-v1") {
      // The INR book legitimately holds Indian synthetic names. Asserting
      // their absence there would be asserting the wrong thing.
      return;
    }
    const { context, page } = await openCockpit(browser);
    try {
      await openDrawer(page);
      const text = await page.textContent('[data-testid="attention-drawer"]');
      for (const gone of ["Bhavani", "Yamuna", "Aravali", "Deccan",
                          "Narmada", "Sahyadri", "Wardha"]) {
        assert.ok(!(text ?? "").includes(gone),
          `the drawer shows the India-specific name ${gone}`);
      }
      for (const word of INDIA_WORDS) {
        assert.ok(!new RegExp(word, "i").test(text ?? ""),
          `the drawer shows ${word}`);
      }
    } finally {
      await context.close();
    }
  },
);

await test("an answer, its table and its chart use one currency", async () => {
  const { context, page, problems } = await openCockpit(browser);
  try {
    await ask(page, "What is total exposure at default by sector?");
    await waitForAnswer(page);
    const text = await page.textContent('[data-testid="v4-response"]');
    for (const word of INDIA_WORDS) {
      assert.ok(!new RegExp(word, "i").test(text ?? ""),
        `the published answer shows ${word}`);
    }
  } finally {
    await context.close();
  }
});

/* ---- responsive: five real viewports -------------------------------- */
/*
 * The complaint these exist for: a 1728px Mac rendered a 1128px ribbon with
 * two empty thirds. Two separate things can cause that, so two ratios are
 * measured rather than one.
 *
 *   columnFill  the Cockpit's share of the content area the shell gives it.
 *               This is the Cockpit's own responsibility and should be near
 *               1 at every width.
 *   windowFill  the content area's share of the window. This belongs to the
 *               application shell -- the navigation rail and the column cap --
 *               and its floor is set per viewport from the rail's real width,
 *               expanded above 767px and collapsed to its icon rail below.
 *
 * Neither number is allowed to hide behind the other.
 */

const VIEWPORTS = [
  { name: "mac-16-inch", width: 1728, height: 1117, minWindowFill: 0.84 },
  { name: "desktop-1440", width: 1440, height: 900, minWindowFill: 0.82 },
  { name: "laptop-1280", width: 1280, height: 800, minWindowFill: 0.8 },
  { name: "tablet-834", width: 834, height: 1112, minWindowFill: 0.72 },
  { name: "phone-390", width: 390, height: 844, minWindowFill: 0.8 },
];

const MIN_COLUMN_FILL = 0.89;
const responsive = [];

for (const viewport of VIEWPORTS) {
  await test(`the landing page fills a ${viewport.name} window`, async () => {
    const context = await newContext(browser, {
      viewport: { width: viewport.width, height: viewport.height },
    });
    const page = await context.newPage();
    try {
      await page.goto(`${UI}/`, { waitUntil: "domcontentloaded" });
      await page.waitForSelector('[data-testid="cockpit-v4-home"]', {
        timeout: 60_000,
      });
      const measured = await page.evaluate(() => {
        const home = document.querySelector('[data-testid="cockpit-v4-home"]');
        const column = document.querySelector(
          '[data-testid="app-content-column"]',
        );
        const main = document.querySelector("main");
        // When something is narrow, the useful question is WHICH ancestor
        // stopped being wide -- so the chain is measured, not guessed at.
        const chain = [];
        for (let node = home; node && node !== document.documentElement;
             node = node.parentElement) {
          chain.push({
            tag: node.tagName.toLowerCase(),
            testid: node.getAttribute("data-testid") ?? "",
            width: Math.round(node.getBoundingClientRect().width),
            maxWidth: window.getComputedStyle(node).maxWidth,
          });
        }
        return {
          contentWidth: Math.round(home.getBoundingClientRect().width),
          mainWidth: Math.round(main?.getBoundingClientRect().width ?? 0),
          wideRequested: column?.getAttribute("data-wide") === "true",
          navCollapsed:
            document.querySelector("[data-collapsed]")?.getAttribute(
              "data-collapsed",
            ) === "true",
          innerWidth: window.innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          askVisible: !!document.querySelector(
            '[data-testid="cockpit-v4-question"]',
          ),
          chain,
        };
      });
      const columnFill = measured.contentWidth / measured.mainWidth;
      const windowFill = measured.mainWidth / measured.innerWidth;
      responsive.push({
        viewport: viewport.name,
        width: viewport.width,
        height: viewport.height,
        contentWidth: measured.contentWidth,
        mainWidth: measured.mainWidth,
        innerWidth: measured.innerWidth,
        navCollapsed: measured.navCollapsed,
        wideRequested: measured.wideRequested,
        columnFill: Number(columnFill.toFixed(3)),
        windowFill: Number(windowFill.toFixed(3)),
      });

      assert.ok(
        measured.wideRequested,
        `${viewport.name}: the Cockpit did not ask the shell for the wide ` +
          `content column`,
      );
      assert.ok(
        columnFill >= MIN_COLUMN_FILL,
        `${viewport.name}: the Cockpit used ${measured.contentWidth}px of the ` +
          `${measured.mainWidth}px it was given (` +
          `${(columnFill * 100).toFixed(0)}%). Ancestors: ` +
          JSON.stringify(measured.chain),
      );
      assert.ok(
        windowFill >= viewport.minWindowFill,
        `${viewport.name}: the shell gave the page ${measured.mainWidth}px of ` +
          `${measured.innerWidth}px (${(windowFill * 100).toFixed(0)}%), below ` +
          `the ${(viewport.minWindowFill * 100).toFixed(0)}% this width ` +
          `should reach. Navigation collapsed: ${measured.navCollapsed}.`,
      );
      assert.ok(
        measured.scrollWidth <= measured.innerWidth + 1,
        `${viewport.name}: the page scrolls sideways ` +
          `(${measured.scrollWidth} > ${measured.innerWidth})`,
      );
      assert.ok(measured.askVisible, `${viewport.name}: no ask box`);
    } finally {
      await context.close();
    }
  });
}

await test("the navigation rail collapses itself on a phone", async () => {
  const context = await newContext(browser, {
    viewport: { width: 390, height: 844 },
  });
  const page = await context.newPage();
  try {
    await page.goto(`${UI}/`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="cockpit-v4-home"]');
    const collapsed = await page.getAttribute("[data-collapsed]", "data-collapsed");
    assert.equal(
      collapsed,
      "true",
      "at 390px an expanded 212px rail is more than half the screen",
    );
  } finally {
    await context.close();
  }
});

await test("the greeting never addresses a deployment profile as a person",
  async () => {
    const { context, page } = await openCockpit(browser);
    try {
      const heading = await page.textContent(
        '[data-testid="cockpit-v4-greeting"]',
      );
      assert.ok(
        !/Local UAT|Service Account|Demo User/i.test(heading ?? ""),
        `the greeting read "${heading}", which is a profile label, not a name`,
      );
      assert.match(heading ?? "", /Good (morning|afternoon|evening)/);
    } finally {
      await context.close();
    }
  },
);

if (process.env.V4_LANDING_SCREENSHOT) {
  // Through the themed helper, like every other context. This block built
  // its own to set a viewport, so the landing screenshot came out in the
  // default theme however the run was themed -- the one picture a reader
  // is most likely to open, proving the least.
  const context = await newContext(browser, {
    viewport: { width: 1440, height: 1200 },
  });
  const page = await context.newPage();
  try {
    await page.goto(`${UI}/`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('[data-testid="attention-card"]',
      { timeout: 60_000 });
    await page.screenshot({
      path: process.env.V4_LANDING_SCREENSHOT,
      fullPage: true,
    });
    console.log(`  screenshot ${process.env.V4_LANDING_SCREENSHOT}`);
  } finally {
    await context.close();
  }
}

await browser.close();

const summary = {
  label: "REAL BROWSER · REAL UI · REAL BACKEND · MOCK ANALYST",
  note:
    "The analyst model is a stub. This proves the wiring, the run lifecycle " +
    "and the rendering. It is NOT a live Opus validation.",
  ui: UI,
  api: API,
  // WHICH THEME THIS RUN PROVES. The two evidence files are identical in
  // shape and were distinguished only by which directory they sat in, so a
  // file quoted on its own said nothing about what it had rendered. The
  // empty string is the default theme, named rather than implied.
  theme: THEME,
  total: results.length,
  passed: results.filter((r) => r.ok).length,
  failed: failures,
  forbidden_endpoints_checked: FORBIDDEN,
  measurements,
  responsive,
  results,
};
console.log(`\n${summary.passed}/${summary.total} browser tests passed\n`);
// A filtered run is a diagnostic, not evidence: it must never overwrite
// the artifact with a partial result.
if (process.env.V4_BROWSER_EVIDENCE && !ONLY) {
  const { writeFileSync } = await import("node:fs");
  writeFileSync(
    process.env.V4_BROWSER_EVIDENCE,
    JSON.stringify(summary, null, 2) + "\n",
  );
  console.log(`evidence written to ${process.env.V4_BROWSER_EVIDENCE}`);
}
process.exit(failures === 0 ? 0 : 1);
