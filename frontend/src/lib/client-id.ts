/** Anonymous browser identity (until real accounts, M9): a UUID kept in localStorage and sent
 *  as `X-Client-Id` so history and daily usage can be attributed to this browser. */

const KEY = "qcvn.client_id";

export function getClientId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    let id = window.localStorage.getItem(KEY);
    if (!id) {
      id = crypto.randomUUID();
      window.localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return null;
  }
}

export function clientHeaders(): Record<string, string> {
  const id = getClientId();
  return id ? { "X-Client-Id": id } : {};
}
