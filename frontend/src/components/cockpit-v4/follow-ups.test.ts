import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  FALLBACK_FOLLOW_UPS,
  MAX_FOLLOW_UPS,
  MAX_OPENING,
  chipsToShow,
  fallbackFollowUps,
} from "./follow-ups.ts";

const q = (...questions: string[]) => questions.map((question) => ({ question }));

describe("what the reader is offered next", () => {
  it("opens a seeded thread on the questions the card carried (§31)", () => {
    const chips = chipsToShow({
      busy: false,
      answered: false,
      domainId: "corporate",
      opening: q(
        "Show the borrowers behind Construction in 2026-08.",
        "What drove the change in ECL for Construction in 2026-08?",
        "Break Construction down by sub sector.",
        "Split Construction by facility type.",
        "How has ECL moved for Construction over the last twelve months?",
      ),
    });
    assert.equal(chips.length, 5);
    assert.ok(chips[0].includes("borrowers behind Construction"));
  });

  it("shows between three and five openers, never more", () => {
    const chips = chipsToShow({
      busy: false,
      answered: false,
      domainId: "corporate",
      opening: q("a", "b", "c", "d", "e", "f", "g"),
    });
    assert.equal(chips.length, MAX_OPENING);
  });

  it("prefers the answer's own suggestions once there is an answer", () => {
    const chips = chipsToShow({
      busy: false,
      answered: true,
      domainId: "corporate",
      offered: q("Which facilities moved to Stage 2?"),
      opening: q("an opener that should no longer be offered"),
    });
    assert.deepEqual(chips, ["Which facilities moved to Stage 2?"]);
  });

  it("falls back to the book's own prompts when an answer offered none (§32)", () => {
    const chips = chipsToShow({
      busy: false,
      answered: true,
      domainId: "corporate",
      offered: [],
      opening: q("an opener that should no longer be offered"),
    });
    assert.deepEqual(chips, FALLBACK_FOLLOW_UPS.corporate);
    assert.equal(chips.length, MAX_FOLLOW_UPS);
  });

  it("falls back to the RETAIL prompts in the retail book", () => {
    const chips = chipsToShow({
      busy: false,
      answered: true,
      domainId: "retail",
      offered: [],
    });
    assert.deepEqual(chips, FALLBACK_FOLLOW_UPS.retail);
    assert.ok(chips.some((c) => c.includes("product")));
    assert.ok(!chips.some((c) => c.includes("sector")));
  });

  it("offers nothing rather than the wrong book's prompts", () => {
    assert.deepEqual(fallbackFollowUps("nonesuch"), []);
    assert.deepEqual(
      chipsToShow({ busy: false, answered: true, domainId: "", offered: [] }),
      [],
    );
  });

  it("offers nothing while a run is working", () => {
    assert.deepEqual(
      chipsToShow({
        busy: true,
        answered: true,
        domainId: "corporate",
        offered: q("something"),
        opening: q("something else"),
      }),
      [],
    );
  });

  it("drops blank and whitespace-only suggestions", () => {
    const chips = chipsToShow({
      busy: false,
      answered: false,
      domainId: "corporate",
      opening: [{ question: "  " }, { question: "" }, { question: " real " }],
    });
    assert.deepEqual(chips, ["real"]);
  });

  it("never mixes an opening set with a follow-up set", () => {
    const chips = chipsToShow({
      busy: false,
      answered: true,
      domainId: "corporate",
      offered: q("one"),
      opening: q("two", "three"),
    });
    assert.deepEqual(chips, ["one"]);
  });

  it("caps follow-ups at four so the strip stays one or two lines", () => {
    const chips = chipsToShow({
      busy: false,
      answered: true,
      domainId: "corporate",
      offered: q("a", "b", "c", "d", "e", "f"),
    });
    assert.equal(chips.length, MAX_FOLLOW_UPS);
  });

  it("has a fallback set for every book the Cockpit serves", () => {
    for (const domainId of ["corporate", "retail"]) {
      const chips = fallbackFollowUps(domainId);
      assert.equal(chips.length, MAX_FOLLOW_UPS, domainId);
      // Each is a whole sentence a reader could have typed themselves.
      for (const chip of chips) assert.ok(/[.?]$/.test(chip), chip);
    }
  });
});
