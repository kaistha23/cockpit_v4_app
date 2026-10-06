/**
 * UNIT. No network, no DOM.
 *
 * The parser produces a data tree, never HTML. These tests cover the subset
 * an answer uses, and the security property that makes the whole approach
 * safe: markup in an answer is DATA, and the only value that reaches an
 * attribute is a link href, which is allow-listed.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  inlineText,
  parseInline,
  parseMarkdown,
  safeHref,
  type Block,
} from "./markdown-parse.ts";

function kinds(blocks: Block[]): string[] {
  return blocks.map((b) => b.kind);
}

test("a heading, a paragraph and bullets parse as themselves", () => {
  const blocks = parseMarkdown(
    "## The problem\n\nCredit teams have plenty of information.\n\n" +
      "- Where is risk building?\n- Why is it happening?\n",
  );
  assert.deepEqual(kinds(blocks), ["heading", "paragraph", "bullets"]);
  const heading = blocks[0];
  assert.equal(heading.kind === "heading" && heading.level, 2);
  const bullets = blocks[2];
  assert.equal(bullets.kind === "bullets" && bullets.items.length, 2);
});

test("a single-hash heading renders at the answer's top level", () => {
  const blocks = parseMarkdown("# CreditProbe AI\n\nText.");
  assert.equal(blocks[0].kind, "heading");
  assert.equal(blocks[0].kind === "heading" && blocks[0].level, 2,
    "an answer's heading must not compete with the page's own h1");
});

test("bold and italic become nodes, not visible asterisks", () => {
  const nodes = parseInline("**Cockpit** — interrogate the *recorded* book.");
  assert.equal(nodes[0].kind, "strong");
  assert.ok(nodes.some((n) => n.kind === "em"));
  const flattened = inlineText(nodes);
  assert.doesNotMatch(flattened, /\*/,
    "no raw ** may survive into the rendered text");
  assert.match(flattened, /Cockpit — interrogate the recorded book\./);
});

test("the module list style from a real answer renders cleanly", () => {
  const source =
    "**Cockpit** — interrogate the recorded credit book.\n\n" +
    "**Early Warning** — identify emerging deterioration.\n";
  const blocks = parseMarkdown(source);
  assert.deepEqual(kinds(blocks), ["paragraph", "paragraph"]);
  for (const block of blocks) {
    assert.equal(block.kind === "paragraph" && block.children[0].kind, "strong");
  }
});

test("numbered lists keep their start", () => {
  const blocks = parseMarkdown("1. First\n2. Second\n3. Third");
  const ordered = blocks[0];
  assert.equal(ordered.kind, "ordered");
  assert.equal(ordered.kind === "ordered" && ordered.items.length, 3);
  assert.equal(ordered.kind === "ordered" && ordered.start, 1);
});

test("a table parses into a header and rows", () => {
  const blocks = parseMarkdown(
    "| Module | Owns |\n|---|---|\n| Cockpit | recorded analysis |\n" +
      "| What-If | prospective shocks |",
  );
  const table = blocks[0];
  assert.equal(table.kind, "table");
  if (table.kind !== "table") return;
  assert.equal(table.header.length, 2);
  assert.equal(table.rows.length, 2);
  assert.equal(inlineText(table.rows[1][0]), "What-If");
});

test("inline code and fenced code are distinguished", () => {
  const blocks = parseMarkdown(
    "Use `inspect_catalog` first.\n\n```sql\nSELECT 1\n```",
  );
  assert.equal(blocks[0].kind, "paragraph");
  assert.equal(blocks[1].kind, "code");
  assert.equal(blocks[1].kind === "code" && blocks[1].language, "sql");
  assert.equal(blocks[1].kind === "code" && blocks[1].value, "SELECT 1");
});

test("a blockquote and a horizontal rule parse", () => {
  const blocks = parseMarkdown("> It investigates it.\n\n---\n\nAfter.");
  assert.deepEqual(kinds(blocks), ["quote", "rule", "paragraph"]);
});

test("a wrapped bullet continuation stays in its own item", () => {
  const blocks = parseMarkdown(
    "- Early Warning identifies emerging deterioration\n" +
      "  using behavioural and external evidence\n- What-If tests shocks",
  );
  const bullets = blocks[0];
  assert.equal(bullets.kind === "bullets" && bullets.items.length, 2);
  assert.match(
    inlineText(bullets.kind === "bullets" ? bullets.items[0] : []),
    /behavioural and external evidence/,
  );
});

// ---- security ----------------------------------------------------------

test("markup in an answer is data, not markup", () => {
  const blocks = parseMarkdown(
    "<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>",
  );
  // Both become paragraphs whose TEXT is the tag. Nothing constructs HTML,
  // so there is nothing for a browser to execute.
  for (const block of blocks) {
    assert.equal(block.kind, "paragraph");
  }
  const text = blocks
    .map((b) => (b.kind === "paragraph" ? inlineText(b.children) : ""))
    .join(" ");
  assert.match(text, /<script>/, "the tag is shown to the reader as text");
});

test("only safe link schemes reach an href", () => {
  assert.equal(safeHref("https://example.com"), "https://example.com");
  assert.equal(safeHref("http://example.com"), "http://example.com");
  assert.equal(safeHref("mailto:risk@example.com"), "mailto:risk@example.com");
  assert.equal(safeHref("/api/v1/cockpit-v4/runs/r/artifacts/a"),
    "/api/v1/cockpit-v4/runs/r/artifacts/a");
  for (const hostile of [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "vbscript:msgbox(1)",
    "  javascript:alert(1)",
  ]) {
    assert.equal(safeHref(hostile), null, `${hostile} must not become an href`);
  }
});

test("an unsafe link keeps its words and loses its href", () => {
  const nodes = parseInline("See [the report](javascript:alert(1)) for detail.");
  assert.ok(!nodes.some((n) => n.kind === "link"),
    "no link node is produced for an unsafe scheme");
  assert.match(inlineText(nodes), /the report/,
    "the reader still sees the text; only the href is dropped");
});

test("a safe link becomes a link node", () => {
  const nodes = parseInline("See [the artifact](/api/v1/cockpit-v4/x).");
  const link = nodes.find((n) => n.kind === "link");
  assert.ok(link);
  assert.equal(link.kind === "link" && link.href, "/api/v1/cockpit-v4/x");
});

// ---- robustness --------------------------------------------------------

test("empty and whitespace input produce nothing rather than throwing", () => {
  assert.deepEqual(parseMarkdown(""), []);
  assert.deepEqual(parseMarkdown("   \n\n  "), []);
});

test("unbalanced emphasis does not lose the text", () => {
  const text = inlineText(parseInline("**unclosed bold and *italic"));
  assert.match(text, /unclosed bold and/);
});

test("a plain answer with no markup is a single paragraph", () => {
  const blocks = parseMarkdown(
    "CreditProbe is an intelligent credit-investigation layer for risk teams.",
  );
  assert.deepEqual(kinds(blocks), ["paragraph"]);
});
