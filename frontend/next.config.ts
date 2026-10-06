import type { NextConfig } from "next";

/**
 * Standalone Cockpit V4 UI. It talks straight to the V4 API.
 *
 * NEXT_PUBLIC_COCKPIT_V4_API is the only address the client reads. It is
 * inlined into the browser bundle at build time; when unset we default it to
 * the V4 API's standard local port (the launcher overrides it with the port it
 * actually selected).
 */
const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_COCKPIT_V4_API:
      process.env.NEXT_PUBLIC_COCKPIT_V4_API ?? "http://127.0.0.1:8414",
  },
};

export default nextConfig;
