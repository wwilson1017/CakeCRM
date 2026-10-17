const UNSAFE = ['javascript:', 'data:', 'vbscript:', 'file:', 'blob:', 'about:'];

/** False for a URL that would run code or read local files in this origin: redirect URIs
 * come from anonymous client registrations. Custom app schemes (cursor://) are fine. */
export function isSafeRedirect(url: string): boolean {
  try {
    return !UNSAFE.includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

/** Where the consent page sends the browser once the person has chosen (#322). An object
 * so a test can replace it: jsdom implements no navigation, and `window.location` cannot
 * be redefined. */
export const leave = { to: (url: string) => { if (isSafeRedirect(url)) window.location.assign(url); } };
