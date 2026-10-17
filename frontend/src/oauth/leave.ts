/** Where the consent page sends the browser once the person has chosen (#322). An object
 * so a test can replace it: jsdom implements no navigation, and `window.location` cannot
 * be redefined. */
export const leave = { to: (url: string) => window.location.assign(url) };
