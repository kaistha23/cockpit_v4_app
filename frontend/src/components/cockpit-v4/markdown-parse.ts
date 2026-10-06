/**
 * A small Markdown parser that produces a DATA TREE, never HTML.
 *
 * Why not a library
 * -----------------
 * The frontend has no Markdown renderer and no sanitizer. Adding one of each
 * to display an answer would be two dependencies and a new attack surface.
 * This parses the subset an answer actually uses and hands back nodes that
 * `markdown.tsx` renders as React elements.
 *
 * That is the security property, and it is structural rather than filtered:
 * nothing here ever produces an HTML string, so there is no
 * `dangerouslySetInnerHTML` anywhere on this path and no sanitizer to get
 * wrong. A `<script>` in an answer is text, and is displayed as text.
 *
 * Link hrefs are the one place a value reaches an attribute, so schemes are
 * allow-listed.
 */

export type Inline =
  | { kind: "text"; value: string }
  | { kind: "strong"; children: Inline[] }
  | { kind: "em"; children: Inline[] }
  | { kind: "code"; value: string }
  | { kind: "link"; href: string; children: Inline[] };

export type Block =
  | { kind: "heading"; level: 2 | 3 | 4; children: Inline[] }
  | { kind: "paragraph"; children: Inline[] }
  | { kind: "bullets"; items: Inline[][] }
  | { kind: "ordered"; items: Inline[][]; start: number }
  | { kind: "quote"; children: Inline[] }
  | { kind: "code"; value: string; language: string }
  | { kind: "table"; header: Inline[][]; rows: Inline[][][] }
  | { kind: "rule" };

/** Schemes a link may use. Anything else is rendered as plain text. */
const SAFE_SCHEME = /^(https?:\/\/|mailto:|\/|#)/i;

export function safeHref(href: string): string | null {
  const trimmed = href.trim();
  if (!trimmed) return null;
  // `javascript:`, `data:` and friends never reach an href.
  return SAFE_SCHEME.test(trimmed) ? trimmed : null;
}

// ---- inline ------------------------------------------------------------

// `[\s\S]` rather than `.` with the dotAll flag: emphasis can span a wrapped
// line, and this build targets an older ECMAScript than `s` requires.
const INLINE =
  /(\*\*|__)([\s\S]+?)\1|(\*|_)([\s\S]+?)\3|`([^`]+)`|\[([^\]]*)\]\(([^)\s]+)\)/;

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let rest = text;

  while (rest) {
    const match = INLINE.exec(rest);
    if (!match || match.index === undefined) break;
    if (match.index > 0) {
      out.push({ kind: "text", value: rest.slice(0, match.index) });
    }
    const [whole, , strong, , em, code, linkText, href] = match;
    if (strong !== undefined) {
      out.push({ kind: "strong", children: parseInline(strong) });
    } else if (em !== undefined) {
      out.push({ kind: "em", children: parseInline(em) });
    } else if (code !== undefined) {
      out.push({ kind: "code", value: code });
    } else {
      const safe = href !== undefined ? safeHref(href) : null;
      if (safe) {
        out.push({ kind: "link", href: safe, children: parseInline(linkText) });
      } else {
        // An unsafe or malformed link keeps its visible text and loses the
        // href. It is not silently dropped — the reader still sees the words.
        out.push({ kind: "text", value: linkText ?? whole });
      }
    }
    rest = rest.slice(match.index + whole.length);
  }

  if (rest) out.push({ kind: "text", value: rest });
  return out.length ? out : [{ kind: "text", value: "" }];
}

// ---- blocks ------------------------------------------------------------

const HEADING = /^(#{2,4})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/;
const QUOTE = /^>\s?(.*)$/;
const FENCE = /^```(\w*)\s*$/;
const RULE = /^(-{3,}|\*{3,}|_{3,})\s*$/;
const TABLE_DIVIDER = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;

function cells(line: string): string[] {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((c) => c.trim());
}

export function parseMarkdown(source: string): Block[] {
  const lines = (source ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length) {
      blocks.push({
        kind: "paragraph",
        children: parseInline(paragraph.join(" ").trim()),
      });
      paragraph = [];
    }
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    if (!line.trim()) {
      flush();
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      flush();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      blocks.push({
        kind: "code",
        value: body.join("\n"),
        language: fence[1] ?? "",
      });
      continue;
    }

    if (RULE.test(line)) {
      flush();
      blocks.push({ kind: "rule" });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      // `#` and `##` both render as the answer's top level: an answer is not
      // a page, and its headings should not compete with the page's own.
      const hashes = heading[1].length;
      blocks.push({
        kind: "heading",
        level: (hashes <= 2 ? 2 : hashes === 3 ? 3 : 4) as 2 | 3 | 4,
        children: parseInline(heading[2]),
      });
      continue;
    }

    // A single `#` heading is common in model output; treat it as level 2.
    if (/^#\s+/.test(line)) {
      flush();
      blocks.push({
        kind: "heading",
        level: 2,
        children: parseInline(line.replace(/^#\s+/, "")),
      });
      continue;
    }

    if (line.includes("|") && TABLE_DIVIDER.test(lines[i + 1] ?? "")) {
      flush();
      const header = cells(line).map(parseInline);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(cells(lines[i]).map(parseInline));
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: "table", header, rows });
      continue;
    }

    if (BULLET.test(line)) {
      flush();
      const items: Inline[][] = [];
      while (i < lines.length && BULLET.test(lines[i])) {
        const text = [BULLET.exec(lines[i])![1]];
        // A wrapped continuation line belongs to the item above it.
        while (
          i + 1 < lines.length &&
          lines[i + 1].trim() &&
          !BULLET.test(lines[i + 1]) &&
          !ORDERED.test(lines[i + 1]) &&
          !HEADING.test(lines[i + 1]) &&
          /^\s{2,}/.test(lines[i + 1])
        ) {
          i += 1;
          text.push(lines[i].trim());
        }
        items.push(parseInline(text.join(" ")));
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: "bullets", items });
      continue;
    }

    const ordered = ORDERED.exec(line);
    if (ordered) {
      flush();
      const start = Number.parseInt(ordered[1], 10) || 1;
      const items: Inline[][] = [];
      while (i < lines.length && ORDERED.test(lines[i])) {
        items.push(parseInline(ORDERED.exec(lines[i])![2]));
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: "ordered", items, start });
      continue;
    }

    const quote = QUOTE.exec(line);
    if (quote) {
      flush();
      const body = [quote[1]];
      while (i + 1 < lines.length && QUOTE.test(lines[i + 1])) {
        i += 1;
        body.push(QUOTE.exec(lines[i])![1]);
      }
      blocks.push({ kind: "quote", children: parseInline(body.join(" ")) });
      continue;
    }

    paragraph.push(line.trim());
  }

  flush();
  return blocks;
}

/** The plain text of a parsed answer. Used by tests and by accessibility. */
export function inlineText(nodes: Inline[]): string {
  return nodes
    .map((node) => {
      switch (node.kind) {
        case "text":
          return node.value;
        case "code":
          return node.value;
        default:
          return inlineText(node.children);
      }
    })
    .join("");
}
