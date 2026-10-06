"use client";

import { cockpitV4Enabled } from "@/components/cockpit-v4/client";
import { CockpitV4Home } from "@/components/cockpit-v4/cockpit-v4-home";
import { NotInThisRuntime } from "@/components/cockpit-v4/not-in-this-runtime";

/** The Cockpit V4 home. This app serves nothing else. */
export default function CockpitPage() {
  if (!cockpitV4Enabled()) {
    return <NotInThisRuntime title="Cockpit" what="The Cockpit" />;
  }
  return <CockpitV4Home />;
}
