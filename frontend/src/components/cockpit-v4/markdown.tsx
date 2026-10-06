"use client";

/**
 * Render a parsed answer as React elements.
 *
 * Every node becomes an element. No HTML string is ever constructed, so there
 * is no `dangerouslySetInnerHTML` on this path and nothing to sanitize: text
 * that looks like markup is displayed as text, because that is all it is.
 *
 * The styling aims at a readable executive answer — a comfortable measure,
 * real paragraph spacing, headings that are distinct without shouting, and
 * tables that scroll rather than break the column.
 *
 * COLOUR COMES FROM TOKENS, NEVER FROM A LITERAL. Every rule in here was
 * once a `slate`/`sky` Tailwind literal, which meant the answer body stayed
 * light-mode whatever theme was chosen: on Midnight Boardroom the paragraph
 * text was #334155 on a #111823 canvas and the reader could barely see the  colour-literal-ok: the two values ARE the bug being described
 * answer they had asked for. `globals.css` says it plainly -- a literal
 * colour value inside a component is a bug -- and this file is the one where
 * it cost the most.
 *
 * `prose-ai` is the role class for model-written prose. It carries the
 * typeface and measure only; the colour is the sibling `text-text-*` token,
 * which is the convention `components/ask/answer.tsx` already follows.
 */

import * as React from "react";

// `markdown-parse` rather than `markdown`: a `.ts` and a `.tsx` with the
// same stem resolve differently in tsc and in the bundler, and the
// bundler resolved this file to itself.
import {
  parseMarkdown,
  type Block,
  type Inline,
} from "./markdown-parse";

function renderInline(nodes: Inline[], keyPrefix = ""): React.ReactNode {
  return nodes.map((node, i) => {
    const key = `${keyPrefix}${i}`;
    switch (node.kind) {
      case "text":
        return <React.Fragment key={key}>{node.value}</React.Fragment>;
      case "strong":
        return (
          <strong key={key} className="font-semibold text-text-primary">
            {renderInline(node.children, `${key}-`)}
          </strong>
        );
      case "em":
        return <em key={key}>{renderInline(node.children, `${key}-`)}</em>;
      case "code":
        return (
          <code
            key={key}
            className="mono rounded bg-surface-sunken px-1 py-0.5 text-[0.85em] text-text-primary"
          >
            {node.value}
          </code>
        );
      case "link":
        return (
          <a
            key={key}
            href={node.href}
            target="_blank"
            rel="noreferrer noopener"
            className="text-accent underline underline-offset-2"
          >
            {renderInline(node.children, `${key}-`)}
          </a>
        );
      default:
        return null;
    }
  });
}

function renderBlock(block: Block, key: string): React.ReactNode {
  switch (block.kind) {
    case "heading": {
      const common = "font-semibold text-text-primary";
      if (block.level === 2) {
        return (
          <h2 key={key} className={`mt-5 mb-2 text-base ${common} first:mt-0`}>
            {renderInline(block.children, `${key}-`)}
          </h2>
        );
      }
      if (block.level === 3) {
        return (
          <h3 key={key} className={`mt-4 mb-1.5 text-sm ${common}`}>
            {renderInline(block.children, `${key}-`)}
          </h3>
        );
      }
      return (
        <h4
          key={key}
          className="meta mt-3 mb-1 text-xs font-semibold uppercase tracking-wide text-text-muted"
        >
          {renderInline(block.children, `${key}-`)}
        </h4>
      );
    }
    case "paragraph":
      return (
        <p key={key} className="my-2 text-sm leading-relaxed text-text-secondary">
          {renderInline(block.children, `${key}-`)}
        </p>
      );
    case "bullets":
      return (
        <ul key={key} className="my-2 space-y-1.5 pl-5">
          {block.items.map((item, i) => (
            <li
              key={`${key}-${i}`}
              className="list-disc text-sm leading-relaxed text-text-secondary marker:text-text-muted"
            >
              {renderInline(item, `${key}-${i}-`)}
            </li>
          ))}
        </ul>
      );
    case "ordered":
      return (
        <ol key={key} start={block.start} className="my-2 space-y-1.5 pl-5">
          {block.items.map((item, i) => (
            <li
              key={`${key}-${i}`}
              className="list-decimal text-sm leading-relaxed text-text-secondary marker:text-text-muted"
            >
              {renderInline(item, `${key}-${i}-`)}
            </li>
          ))}
        </ol>
      );
    case "quote":
      return (
        <blockquote
          key={key}
          className="my-3 border-l-2 border-border-strong pl-3 text-sm italic leading-relaxed text-text-secondary"
        >
          {renderInline(block.children, `${key}-`)}
        </blockquote>
      );
    case "code":
      return (
        <pre
          key={key}
          className="mono my-3 overflow-x-auto rounded border border-border bg-surface-sunken p-3 text-xs leading-relaxed text-text-primary"
        >
          <code>{block.value}</code>
        </pre>
      );
    case "table":
      return (
        <div key={key} className="my-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                {block.header.map((cell, i) => (
                  <th
                    key={`${key}-h${i}`}
                    className="meta border-b border-border-strong px-2 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted"
                  >
                    {renderInline(cell, `${key}-h${i}-`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={`${key}-r${r}`}>
                  {row.map((cell, c) => (
                    <td
                      key={`${key}-r${r}c${c}`}
                      className="border-b border-border px-2 py-1.5 align-top text-text-secondary"
                    >
                      {renderInline(cell, `${key}-r${r}c${c}-`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "rule":
      return <hr key={key} className="my-4 border-border" />;
    default:
      return null;
  }
}

/** An answer, rendered. */
export function Markdown({ source }: { source: string }) {
  const blocks = React.useMemo(() => parseMarkdown(source), [source]);
  return (
    <div data-testid="v4-markdown" className="prose-ai max-w-[68ch] text-text-secondary">
      {blocks.map((block, i) => renderBlock(block, `b${i}`))}
    </div>
  );
}
