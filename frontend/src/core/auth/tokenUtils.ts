export const TOKEN_KEY = 'cakecrm_token';

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function storeToken(token: string) {
  sessionStorage.setItem(TOKEN_KEY, token);
}
