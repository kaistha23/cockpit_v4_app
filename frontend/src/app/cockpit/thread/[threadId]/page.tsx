"use client";

/**
 * One Cockpit conversation, at its own URL.
 *
 * A thread having a real route is what makes it a place rather than a state
 * the page happens to be in. It can be refreshed, linked, reopened from the
 * home page, and walked back to with the browser's own Back button -- none of
 * which was true while the conversation lived inside the landing page's
 * component state.
 *
 * The id is in the path and the authorization is not. A thread is
 * tenant-checked server-side and answers 404 to anyone else, so typing
 * somebody else's id here reveals nothing, including whether it exists.
 *
 * The QUESTION is not in the path either. It used to be, so the navigation
 * could carry it -- and a refresh then asked it again, which spends the
 * analysis twice. The run is started before the navigation instead, and this
 * page finds one already going and follows it.
 */

import { useParams, useRouter } from "next/navigation";
import * as React from "react";

import { cockpitV4Enabled } from "@/components/cockpit-v4/client";
import { CockpitV4Thread } from "@/components/cockpit-v4/thread-view";
import { NotInThisRuntime } from "@/components/cockpit-v4/not-in-this-runtime";
import { useWideContent } from "@/components/layout/content-width";

export default function CockpitThreadPage() {
  const params = useParams<{ threadId: string }>();
  const router = useRouter();
  // Charts and tables want the workspace, not a centred ribbon.
  useWideContent();

  if (!cockpitV4Enabled()) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16">
        <NotInThisRuntime title="Cockpit" what="The Cockpit conversation" />
      </div>
    );
  }

  const threadId = String(params?.threadId ?? "");
  if (!threadId) return null;

  return (
    <CockpitV4Thread threadId={threadId} onHome={() => router.push("/")} />
  );
}
