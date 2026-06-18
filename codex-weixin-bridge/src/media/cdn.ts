import { createDecipheriv } from "node:crypto";

function buildCdnDownloadUrl(encryptedQueryParam: string, cdnBaseUrl: string): string {
  return `${ensureTrailingSlash(cdnBaseUrl)}download?encrypted_query_param=${encodeURIComponent(encryptedQueryParam)}`;
}

function ensureTrailingSlash(value: string): string {
  return value.endsWith("/") ? value : `${value}/`;
}

function parseAesKey(aesKeyBase64: string): Buffer {
  const decoded = Buffer.from(aesKeyBase64, "base64");
  if (decoded.length === 16) {
    return decoded;
  }
  if (decoded.length === 32 && /^[0-9a-fA-F]{32}$/.test(decoded.toString("ascii"))) {
    return Buffer.from(decoded.toString("ascii"), "hex");
  }
  throw new Error(`invalid aes_key: expected 16 raw bytes or 32-char hex string, got ${decoded.length} bytes`);
}

async function fetchCdnBytes(url: string): Promise<Buffer> {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await response.text().catch(() => "(unreadable)");
    throw new Error(`CDN download ${response.status} ${response.statusText}: ${body.slice(0, 500)}`);
  }
  return Buffer.from(await response.arrayBuffer());
}

function decryptAesEcb(ciphertext: Buffer, key: Buffer): Buffer {
  const decipher = createDecipheriv("aes-128-ecb", key, null);
  return Buffer.concat([decipher.update(ciphertext), decipher.final()]);
}

export async function downloadAndDecryptBuffer(params: {
  encryptedQueryParam: string;
  aesKeyBase64: string;
  cdnBaseUrl: string;
  fullUrl?: string | null;
}): Promise<Buffer> {
  const key = parseAesKey(params.aesKeyBase64);
  const url = params.fullUrl?.trim() || buildCdnDownloadUrl(params.encryptedQueryParam, params.cdnBaseUrl);
  const encrypted = await fetchCdnBytes(url);
  return decryptAesEcb(encrypted, key);
}

export async function downloadPlainCdnBuffer(params: {
  encryptedQueryParam: string;
  cdnBaseUrl: string;
  fullUrl?: string | null;
}): Promise<Buffer> {
  const url = params.fullUrl?.trim() || buildCdnDownloadUrl(params.encryptedQueryParam, params.cdnBaseUrl);
  return fetchCdnBytes(url);
}
