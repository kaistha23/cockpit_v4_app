"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  listSavedAnalyses,
  type SavedAnalysis,
} from "@/components/cockpit-v4/client";

/** Saved analyses, newest first. Each opens its conversation. */
export default function SavedAnalysesPage() {
  const [items, setItems] = useState<SavedAnalysis[] | null>(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let live = true;
    listSavedAnalyses()
      .then((saved) => live && setItems(saved))
      .catch((error) => live && setProblem(String(error?.message ?? error)));
    return () => {
      live = false;
    };
  }, []);

  return (
    <div className="mx-auto max-w-3xl py-6">
      <h1 className="display mb-4 text-[length:var(--text-page)] font-semibold">
        Saved analyses
      </h1>
      {problem ? <p className="text-negative">{problem}</p> : null}
      {items === null && !problem ? (
        <p className="text-text-muted">Loading…</p>
      ) : null}
      {items?.length === 0 ? (
        <p className="text-text-muted">Nothing saved yet.</p>
      ) : null}
      <ul className="divide-y divide-border">
        {items?.map((item) => (
          <li key={item.saved_id} className="py-3">
            <Link
              href={`/cockpit/saved/${encodeURIComponent(item.saved_id)}`}
              className="text-accent hover:underline"
            >
              {item.title || item.question || item.saved_id}
            </Link>
            {item.note ? (
              <p className="text-sm text-text-secondary">{item.note}</p>
            ) : null}
            <p className="meta text-text-muted">{item.created_at}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
