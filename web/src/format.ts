// Display only: the amounts and dates come from the API; the page never computes money.

const EURO = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const DAY_TIME = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

export function euro(cents: number): string {
  if (!Number.isInteger(cents)) throw new Error(`not whole cents: ${cents}`);
  return EURO.format(cents / 100);
}

/** `2026-11-16` as "16 Nov". */
export function day(isoDate: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(isoDate)) throw new Error(`not a YYYY-MM-DD date: ${isoDate}`);
  return DAY.format(new Date(`${isoDate}T00:00:00Z`));
}

/** A timestamp as "16 Nov, 09:00" (UTC, as the synthetic month runs). */
export function dayTime(isoDateTime: string): string {
  const at = new Date(isoDateTime);
  if (Number.isNaN(at.getTime())) throw new Error(`not a timestamp: ${isoDateTime}`);
  return `${DAY_TIME.format(at)} UTC`;
}
