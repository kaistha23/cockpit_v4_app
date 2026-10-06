import type { Metadata } from "next";
import Link from "next/link";
import {
  Geist,
  JetBrains_Mono,
  Plus_Jakarta_Sans,
  Space_Grotesk,
} from "next/font/google";

import { ContentColumn, ContentWidthProvider } from "@/components/layout/content-width";

import "./globals.css";

// Four typefaces, each with one job (see globals.css): CreditProbe prose,
// the user's words, the machine's record, and figures/actions.
const geist = Geist({ variable: "--font-prose-raw", subsets: ["latin"] });
const jakarta = Plus_Jakarta_Sans({
  variable: "--font-user-raw",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});
const jetbrains = JetBrains_Mono({
  variable: "--font-mono-raw",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});
const grotesk = Space_Grotesk({
  variable: "--font-display-raw",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "CreditProbe AI — Credit Portfolio Intelligence",
  description: "Cockpit V4: governed, traceable credit-risk analysis.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${geist.variable} ${jakarta.variable} ${jetbrains.variable} ${grotesk.variable}`}
    >
      <body className="antialiased">
        <header className="border-b border-border bg-surface">
          <nav className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3 text-sm">
            <Link href="/" className="display font-semibold text-text-primary">
              CreditProbe Cockpit
            </Link>
            <Link href="/cockpit/saved" className="text-text-secondary hover:text-text-primary">
              Saved
            </Link>
            <Link href="/cockpit/data" className="text-text-secondary hover:text-text-primary">
              Data
            </Link>
          </nav>
        </header>
        <ContentWidthProvider>
          <ContentColumn>{children}</ContentColumn>
        </ContentWidthProvider>
      </body>
    </html>
  );
}
