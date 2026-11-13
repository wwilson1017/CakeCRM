/**
 * Money in the currency it is actually denominated in.
 *
 * `deals.currency` is user-writable free text, so this must not assume USD and must not
 * assume the code is even valid — `Intl` throws a RangeError on an unknown one, and a
 * money surface is not allowed to blank itself over a typo in a currency field.
 */
export const money = (n: number, currency: string): string => {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency, maximumFractionDigits: 0,
    }).format(n);
  } catch {
    return `${currency} ${Math.round(n).toLocaleString()}`;
  }
};
