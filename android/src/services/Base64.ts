// atob is available at runtime via Hermes's global polyfill (same as btoa,
// already used elsewhere in this codebase — TS just doesn't have it in its
// configured lib types, hence the @ts-ignore).

export function base64ToBytes(b64: string): Uint8Array {
  // @ts-ignore
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  // @ts-ignore
  return btoa(binary);
}

/** UTF-8 bytes -> string, chunked so a multi-MB GPX doesn't blow the call stack (the
 *  String.fromCharCode(...bytes) spread does). Falls back to Latin-1 for invalid UTF-8. */
export function bytesToUtf8(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + 8192)));
  try { return decodeURIComponent(escape(binary)); } catch { return binary; }
}
