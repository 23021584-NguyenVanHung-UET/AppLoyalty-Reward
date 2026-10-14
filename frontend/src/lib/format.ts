export function money(
  value: string | null,
  currency: string,
  locale = "en-US",
): string {
  if (value === null) return "Not set";
  if (!/^-?\d{1,16}(\.\d{1,2})?$/.test(value)) return `${currency} ${value}`;
  // ECMA-402 ToIntlMathematicalValue accepts decimal strings without converting
  // through a binary float. TS's format signature has not caught up with it.
  const formatter = new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    currencyDisplay: "code",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const exactFormat = formatter.format as unknown as (
    decimal: string,
  ) => string;
  return exactFormat(value);
}

export function points(
  value: number,
  signed = false,
  locale = "en-US",
): string {
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: 0,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(value);
}

export function localDate(value: string | null, locale = "en-US"): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Invalid date"
    : new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

export function utcDate(value: string | null): string {
  if (!value) return "No date set";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Invalid date"
    : date.toISOString().replace("T", " ").replace("Z", " UTC");
}

export function toLocalInput(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export const label = (value: string) =>
  value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (letter) => letter.toUpperCase());
