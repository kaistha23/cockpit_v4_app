/**
 * The landing-page greeting.
 *
 * Time of day comes from the READER's clock, not the server's: a Cockpit open
 * in Riyadh at nine in the morning should not say good evening because the
 * process serving it is somewhere else.
 *
 * The name is used when one is available and omitted when it is not. Nothing
 * here invents one, and nothing falls back to a placeholder person.
 */

export type Greeting = { salutation: string; name: string; full: string };

export function timeOfDay(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

export function greeting(displayName: string, now: Date = new Date()): Greeting {
  const salutation = timeOfDay(now.getHours());
  const name = (displayName ?? "").trim();
  return {
    salutation,
    name,
    full: name ? `${salutation}, ${name}` : salutation,
  };
}
