/** Small text helpers shared by the screens (plain TS so tests can run them). */

/** Minutes until predicted danger, as the crew lead sees it: null "--", 0 "now", else "in N min". */
export function dangerText(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return "--";
  if (minutes <= 0) return "now";
  return `in ${Math.round(minutes)} min`;
}

/** Thermometer reading from the text box. Checked before anything is sent. */
export function parseThermometer(input: string): { ok: true; value: number } | { ok: false; message: string } {
  const text = input.trim().replace(",", ".");
  if (!text) return { ok: false, message: "Type the thermometer reading first." };
  if (!/^-?\d+(\.\d+)?$/.test(text)) return { ok: false, message: "That's not a number. Type it like 37.4" };
  const value = Number(text);
  if (value < 34 || value > 42) return { ok: false, message: "Body temperature readings are between 34.0 and 42.0 °C. Check the thermometer and try again." };
  return { ok: true, value };
}
