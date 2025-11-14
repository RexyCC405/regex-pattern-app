export function getBackendOriginFromApiBase(apiBase: string): string {
  try {
    const u = new URL(apiBase);
    return `${u.protocol}//${u.host}`;
  } catch {
    return "";
  }
}