// @ts-ignore Frozen pure ESM library intentionally has no declaration file.
import { collectWheelEvidence } from "../../../../wheel-evidence/wheel-evidence.mjs";
export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 65_536;
const MAX_RELEASES = 8;
const MAX_TAGS = 256;
const MAX_PROJECT_LENGTH = 128;
const MAX_VERSION_LENGTH = 64;
const MAX_TAG_LENGTH = 128;
const MAX_UPSTREAM_JSON_BYTES = 2 * 1024 * 1024;
const UPSTREAM_DEADLINE_MS = 8_000;
const MAX_CONCURRENCY = 2;
const MAX_PROVIDER_URLS = 1_000;
const MAX_OUTPUT_MATCHES = 50;
const SERVICE_DESCRIPTOR = {
  brand: "Yumi Wheel Evidence", name: "Yumi Wheel Evidence", version: "1",
  schema: "yumi.wheel-evidence.v1",
  endpoint: "https://noqt.no-qt.chatgpt.site/api/yumi/wheel-evidence",
  docs: "https://noqt.no-qt.chatgpt.site/yumi/wheel-evidence",
  methods: ["GET", "POST", "OPTIONS"],
  source: { provider: "PyPI", data: "Release-specific PyPI JSON" },
  limits: {
    requestBytes: MAX_REQUEST_BYTES,
    releases: { min: 1, max: MAX_RELEASES }, tags: { min: 1, max: MAX_TAGS },
    projectLength: { min: 1, max: MAX_PROJECT_LENGTH },
    versionLength: { min: 1, max: MAX_VERSION_LENGTH },
    tagLength: { min: 1, max: MAX_TAG_LENGTH },
    upstreamReleaseJsonBytes: MAX_UPSTREAM_JSON_BYTES,
    upstreamRequestDeadlineMs: UPSTREAM_DEADLINE_MS,
    concurrentUpstreamRequests: MAX_CONCURRENCY,
    providerUrlsPerItem: MAX_PROVIDER_URLS, outputMatchesPerItem: MAX_OUTPUT_MATCHES,
  },
  requestSchema: {
    topLevelKeys: ["releases", "tags"], releaseKeys: ["project", "version"],
    tags: "Global canonical uncompressed wheel filename tags",
    versionPattern: "^[A-Za-z0-9][A-Za-z0-9.!+_-]*$",
  },
  output: {
    matches: "Filename-selected matches ordered lexicographically by filename",
    matchingTotal: "Exact count before the output cap",
    truncated: "True when more matches exist than are returned",
  },
  assumptions: [
    "Requests contain only caller-specified explicit public PyPI releases and global tags.",
    "Project names use conservative safe grammar and normalization.",
    "Versions are matched literally as reported by the provider.",
    "The upstream release JSON is treated as provider-reported metadata.",
    "At most two upstream release requests are processed concurrently.",
  ],
  limitations: [
    "Matching is based on filename tag intersection and provider-reported metadata.",
    "It is not a compatibility, installability, security, origin, digest-verification, or artifact-availability checker.",
    "NO_MATCHING_WHEEL means no returned provider metadata filename intersects the supplied tags; it does not mean the package is incompatible.",
    "Provider errors remain explicit per-item results rather than being converted to false no-match results.",
    "The service never downloads wheel or artifact files, retries requests, or follows redirects.",
    "Current provider facts can change, and strict literal versions may reject or flag aliases.",
    "GET requests are also subject to host URL limits; use POST for larger requests within the documented body limit.",
    "Callers should submit public non-secret requests.",
    "The application has no account or authentication and does not persist or log requests, add tracking, call a model, or expose Yumi's private assistant, core, or memory; the hosting provider may process request metadata.",
  ],
};
function standardHeaders(includeContentType: boolean): Headers {
  const headers = new Headers();
  if (includeContentType) headers.set("Content-Type", "application/json;charset=utf-8");
  headers.set("Cache-Control", "no-store");
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  headers.set("Access-Control-Allow-Headers", "Content-Type");
  return headers;
}
function jsonResponse(payload: unknown, status: number, extraHeaders?: Record<string, string>): Response {
  try {
    const body = JSON.stringify(payload);
    if (typeof body !== "string") throw new Error();
    const headers = standardHeaders(true);
    for (const [name, value] of Object.entries(extraHeaders ?? {})) headers.set(name, value);
    return new Response(body, { status, headers });
  } catch {
    return new Response('{"error":"INTERNAL_ERROR"}', { status: 500, headers: standardHeaders(true) });
  }
}
function errorResponse(error: string, status: number, extraHeaders?: Record<string, string>): Response {
  return jsonResponse({ error }, status, extraHeaders);
}
function methodNotAllowed(): Response {
  return errorResponse("METHOD_NOT_ALLOWED", 405, { Allow: "GET, POST, OPTIONS" });
}
async function collectFromJson(serializedRequest: string): Promise<Response> {
  let requestData: unknown;
  try { requestData = JSON.parse(serializedRequest); }
  catch { return errorResponse("INVALID_JSON", 400); }
  try { return jsonResponse(await collectWheelEvidence(requestData), 200); }
  catch (error) {
    if (error instanceof TypeError || error instanceof RangeError) return errorResponse("INVALID_REQUEST", 400);
    return errorResponse("INTERNAL_ERROR", 500);
  }
}
async function getResponse(request: Request): Promise<Response> {
  let url: URL;
  try { url = new URL(request.url); }
  catch { return errorResponse("INVALID_QUERY", 400); }
  const keys = Array.from(url.searchParams.keys());
  if (keys.length === 0) return jsonResponse(SERVICE_DESCRIPTOR, 200);
  if (keys.length !== 1 || keys[0] !== "request" || url.searchParams.getAll("request").length !== 1) return errorResponse("INVALID_QUERY", 400);
  const serializedRequest = url.searchParams.get("request");
  if (serializedRequest === null) return errorResponse("INVALID_QUERY", 400);
  if (new TextEncoder().encode(serializedRequest).byteLength > MAX_REQUEST_BYTES) return errorResponse("PAYLOAD_TOO_LARGE", 413);
  return collectFromJson(serializedRequest);
}
function isJsonContentType(value: string | null): boolean {
  return value !== null && value.split(";", 1)[0].trim().toLowerCase() === "application/json";
}
type ContentLengthStatus = number | null | "invalid" | "too-large";
function parseContentLength(request: Request): ContentLengthStatus {
  const rawValue = request.headers.get("content-length");
  if (rawValue === null) return null;
  const value = rawValue.trim();
  if (value.length === 0 || !/^[0-9]+$/.test(value)) return "invalid";
  let integerValue: bigint;
  try { integerValue = BigInt(value); } catch { return "invalid"; }
  if (integerValue > BigInt(Number.MAX_SAFE_INTEGER)) return "invalid";
  if (integerValue > BigInt(MAX_REQUEST_BYTES)) return "too-large";
  return Number(integerValue);
}
async function cancelBody(body: ReadableStream<Uint8Array> | null): Promise<void> {
  if (body === null) return;
  try { await body.cancel(); } catch { /* Cancellation is best effort. */ }
}
async function cancelReader(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<void> {
  try { await reader.cancel(); } catch { /* Cancellation is best effort. */ }
}
async function postResponse(request: Request): Promise<Response> {
  const contentLength = parseContentLength(request);
  if (contentLength === "invalid") {
    await cancelBody(request.body);
    return errorResponse("INVALID_CONTENT_LENGTH", 400);
  }
  if (contentLength === "too-large") {
    await cancelBody(request.body);
    return errorResponse("PAYLOAD_TOO_LARGE", 413);
  }
  if (request.body === null) return collectFromJson("");
  const reader = request.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const parts: string[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      const chunk = result.value;
      if (!(chunk instanceof Uint8Array)) {
        await cancelReader(reader);
        return errorResponse("INVALID_JSON", 400);
      }
      totalBytes += chunk.byteLength;
      if (totalBytes > MAX_REQUEST_BYTES) {
        await cancelReader(reader);
        return errorResponse("PAYLOAD_TOO_LARGE", 413);
      }
      try { parts.push(decoder.decode(chunk, { stream: true })); }
      catch {
        await cancelReader(reader);
        return errorResponse("INVALID_JSON", 400);
      }
    }
    try { parts.push(decoder.decode()); }
    catch {
      await cancelReader(reader);
      return errorResponse("INVALID_JSON", 400);
    }
  } catch {
    await cancelReader(reader);
    return errorResponse("INVALID_JSON", 400);
  } finally { reader.releaseLock(); }
  return collectFromJson(parts.join(""));
}
export async function GET(request: Request): Promise<Response> { return getResponse(request); }
export async function POST(request: Request): Promise<Response> {
  if (!isJsonContentType(request.headers.get("content-type"))) {
    await cancelBody(request.body);
    return errorResponse("UNSUPPORTED_MEDIA_TYPE", 415);
  }
  return postResponse(request);
}
export function OPTIONS(): Response { return new Response(null, { status: 204, headers: standardHeaders(false) }); }
export function HEAD(): Response { return methodNotAllowed(); }
export function PUT(): Response { return methodNotAllowed(); }
export function PATCH(): Response { return methodNotAllowed(); }
export function DELETE(): Response { return methodNotAllowed(); }
