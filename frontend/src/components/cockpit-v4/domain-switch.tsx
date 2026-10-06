"use client";

/**
 * Corporate or Retail, on Cockpit Home.
 *
 * This is not a filter over one dataset. Choosing a book changes which
 * release the server opens, which catalogue it reads, which session it
 * queries and which dashboard it computes -- so the control sits beside the
 * Ask box, where the question is asked, rather than in a settings menu.
 *
 * A book with no published release is shown and disabled with its reason,
 * never hidden and never silently replaced by the other one. Hiding it would
 * leave a reader wondering whether Retail exists; substituting would answer
 * a retail question with corporate numbers.
 */

import * as React from "react";

import type { DomainAvailability, DomainId, DomainStatus } from "./client";

export function DomainSwitch({
  availability,
  value,
  onChange,
}: {
  availability: DomainAvailability | null;
  value: DomainId;
  onChange: (domain: DomainId) => void;
}) {
  if (!availability || availability.domains.length < 2) return null;

  return (
    <div
      data-testid="domain-switch"
      data-domain={value}
      role="group"
      aria-label="Analytical domain"
      className="inline-flex items-center rounded-lg border border-border-strong bg-surface p-0.5"
    >
      {availability.domains.map((domain: DomainStatus) => {
        const selected = domain.domain_id === value;
        const short = domain.domain_label.replace(/ Credit$/, "");
        return (
          <button
            key={domain.domain_id}
            type="button"
            data-testid={`domain-${domain.domain_id}`}
            data-selected={selected ? "true" : "false"}
            aria-pressed={selected}
            disabled={!domain.ready}
            title={
              domain.ready
                ? domain.analysis_ready === false
                  ? "Browsable in Data Builder. This runtime cannot open " +
                    "this book's analytical release, so a question here " +
                    "would have nothing to run against."
                  : undefined
                : domain.reason
            }
            onClick={() => domain.ready && onChange(domain.domain_id)}
            className={
              "rounded-md px-3.5 py-1.5 text-sm font-medium transition " +
              (selected
                ? "bg-accent text-accent-contrast"
                : "text-text-secondary hover:bg-surface-sunken") +
              (domain.ready ? "" : " cursor-not-allowed opacity-40")
            }
          >
            {short}
            {domain.ready && domain.analysis_ready === false ? (
              <span
                data-testid={`domain-${domain.domain_id}-browse-only`}
                className="ml-1.5 text-[10px] uppercase tracking-wide opacity-70"
              >
                browse
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** The book a reader last chose, so Home opens where they left it.
 *
 * `sessionStorage`, not `localStorage`: this is about the tab in front of
 * them, and a choice made on one machine in March is not a preference.
 */
const KEY = "cockpit-v4:domain";

export function rememberDomain(domain: DomainId): void {
  try {
    sessionStorage.setItem(KEY, domain);
  } catch {
    // A private window with storage blocked still gets a working switch;
    // it simply opens on the default each time.
  }
}

export function recallDomain(): DomainId | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw === "corporate" || raw === "retail" ? raw : null;
  } catch {
    return null;
  }
}
