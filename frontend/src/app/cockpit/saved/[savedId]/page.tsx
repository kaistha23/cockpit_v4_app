"use client";

/**
 * Open an analysis a colleague sent you.
 *
 * A share carries a SAVED ANALYSIS id, and a reader who wants to look at
 * it wants the conversation it came from -- the question, the answer, the
 * charts and the trace, not a detached snapshot. So this resolves the
 * saved record and sends the reader to its thread.
 *
 * It exists because the inbox linked somewhere. A message that says
 * "Open the analysis" and goes nowhere is worse than one that does not
 * offer.
 */

import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";

import { readSavedAnalysis } from "@/components/cockpit-v4/client";

export default function SavedAnalysisPage({ params }: {
  params: Promise<{ savedId: string }>;
}) {
  const { savedId } = use(params);
  const router = useRouter();
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let live = true;
    readSavedAnalysis(savedId)
      .then((saved) => {
        if (!live) return;
        if (saved.thread_id) {
          router.replace(`/cockpit/thread/${encodeURIComponent(saved.thread_id)}`);
          return;
        }
        // Saved before threads carried an id, or saved from a context
        // that had none. Said, rather than redirecting nowhere.
        setProblem("This analysis is not attached to a conversation.");
      })
      .catch(() => {
        if (live) setProblem("This analysis could not be opened.");
      });
    return () => { live = false; };
  }, [router, savedId]);

  return (
    <main className="mx-auto w-full max-w-2xl px-4 py-12">
      <p data-testid="v4-saved-status"
         className={problem ? "text-sm text-negative" : "text-sm text-text-muted"}>
        {problem || "Opening the analysis…"}
      </p>
    </main>
  );
}
