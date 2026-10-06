"use client";

/**
 * Who to send an analysis to.
 *
 * The share panel asked for a free-text "Colleague or group inside the
 * bank" -- a field the reader has to already know the answer to, with no
 * validation, no completion and no way to discover that the person they
 * meant spells their handle differently. Whatever they typed was stored
 * as the audience and nobody was told anything.
 *
 * This is the directory it should have been offering: the people who have
 * used CreditProbe on this tenant, and the reader's own teams. It says
 * what it does NOT contain, because a list that quietly omits people is
 * worse than one that admits it.
 */

import * as React from "react";

import { readRecipients, type Recipient } from "./client";

export function RecipientPicker({ value, onChange }: {
  /** The chosen handle: `user:<id>`, `team:<id>`, or empty. */
  value: string;
  onChange: (handle: string) => void;
}) {
  const [people, setPeople] = React.useState<Recipient[]>([]);
  const [teams, setTeams] = React.useState<Recipient[]>([]);
  const [note, setNote] = React.useState("");
  const [problem, setProblem] = React.useState("");

  React.useEffect(() => {
    let live = true;
    readRecipients()
      .then((body) => {
        if (!live) return;
        setPeople(body.people);
        setTeams(body.teams);
        setNote(body.note);
      })
      .catch(() => {
        if (!live) return;
        // A directory that cannot be read is not a reason to block the
        // share: the handle can still be typed.
        setProblem("The directory could not be read. Type a handle instead.");
      });
    return () => { live = false; };
  }, []);

  const empty = !people.length && !teams.length;

  return (
    <div className="space-y-1">
      <label className="block text-xs text-text-secondary">
        Send to a colleague or team in CreditProbe
        {empty ? (
          <input
            data-testid="v4-recipient-handle"
            value={value}
            placeholder="user:kamal.hassan"
            onChange={(event) => onChange(event.target.value)}
            className="mt-1 w-full rounded border border-border-strong bg-surface px-2 py-1 text-sm text-text-primary"
          />
        ) : (
          <select
            data-testid="v4-recipient-picker"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            className="mt-1 w-full rounded border border-border-strong bg-surface px-2 py-1 text-sm text-text-primary"
          >
            <option value="">Nobody — just save the share</option>
            {teams.length ? (
              <optgroup label="Teams">
                {teams.map((team) => (
                  <option key={team.handle} value={team.handle}>
                    {team.label}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {people.length ? (
              <optgroup label="People">
                {people.map((person) => (
                  <option key={person.handle} value={person.handle}>
                    {person.label}
                  </option>
                ))}
              </optgroup>
            ) : null}
          </select>
        )}
      </label>
      {problem ? (
        <p className="text-xs text-warning">{problem}</p>
      ) : note ? (
        <p data-testid="v4-recipient-note" className="text-xs text-text-muted">
          {note}
        </p>
      ) : null}
    </div>
  );
}
