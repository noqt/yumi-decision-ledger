// @ts-expect-error Native Node strip-types test import uses explicit TypeScript extension.
import { POST as collectWheelEvidencePOST } from "../wheel-evidence/route.ts";

export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 65_536;
const ALLOWED_ORIGIN = "https://noqt.no-qt.chatgpt.site";
const WHEEL_EVIDENCE_ENDPOINT = "https://noqt.no-qt.chatgpt.site/api/yumi/wheel-evidence";
const TOOL_NAME = "pypi_wheel_evidence";
const INITIALIZE_METHOD = "initialize";
const DISCOVER_METHOD = "server/discover";
const TOOLS_LIST_METHOD = "tools/list";
const TOOLS_CALL_METHOD = "tools/call";
const PING_METHOD = "ping";
const INITIALIZED_NOTIFICATION = "notifications/initialized";
const SUPPORTED_PROTOCOL_VERSIONS = Object.freeze(["2025-06-18", "2025-11-25"] as const);
const PROJECT_PATTERN = "^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$";
const VERSION_PATTERN = "^[A-Za-z0-9][A-Za-z0-9.!+_-]*$";
const CANONICAL_TAG_PATTERN = "^[a-z0-9_]+-[a-z0-9_]+-[a-z0-9_]+$";

type SupportedProtocolVersion = (typeof SUPPORTED_PROTOCOL_VERSIONS)[number];
type RpcId = string | number;
type JsonObject = Record<string, unknown>;

function hasOwn(value: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function isPlainObject(value: unknown): value is JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasOnlyKeys(value: JsonObject, allowedKeys: readonly string[]): boolean {
  const allowed = new Set(allowedKeys);
  return Object.keys(value).every((key) => allowed.has(key));
}

function hasControlCharacter(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code <= 0x1f || code === 0x7f) return true;
  }
  return false;
}

function isBoundedString(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= maximum && !hasControlCharacter(value);
}

function isSafeRpcId(value: unknown): value is RpcId {
  if (typeof value === "string") return value.length >= 1 && value.length <= 128 && !hasControlCharacter(value);
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isSafeMethod(value: unknown): value is string { return isBoundedString(value, 128); }
function isSupportedProtocolVersion(value: unknown): value is SupportedProtocolVersion {
  return typeof value === "string" && (SUPPORTED_PROTOCOL_VERSIONS as readonly string[]).includes(value);
}

function responseHeaders(additional: Record<string, string> = {}): Headers {
  const headers = new Headers({
    "content-type": "application/json",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  for (const [key, value] of Object.entries(additional)) headers.set(key, value);
  return headers;
}

function jsonResponse(payload: unknown, status = 200, additionalHeaders: Record<string, string> = {}): Response {
  const serialized = JSON.stringify(payload);
  return new Response(serialized === undefined ? "null" : serialized, { status, headers: responseHeaders(additionalHeaders) });
}

function emptyResponse(status: number, additionalHeaders: Record<string, string> = {}): Response {
  return new Response(null, { status, headers: responseHeaders(additionalHeaders) });
}

function rpcErrorResponse(id: RpcId | null, code: number, message: string, status = 400): Response {
  return jsonResponse({ jsonrpc: "2.0", error: { code, message }, id }, status);
}

function httpErrorResponse(status: number, message: string, additionalHeaders: Record<string, string> = {}): Response {
  return jsonResponse({ error: message }, status, additionalHeaders);
}

function hasJsonContentType(request: Request): boolean {
  const value = request.headers.get("content-type");
  return value !== null && value.split(";", 1)[0]?.trim().toLowerCase() === "application/json";
}

function hasAllowedOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  return origin === null || origin === ALLOWED_ORIGIN;
}

function validateContentLength(request: Request): { status: number; message: string } | null {
  const rawValue = request.headers.get("content-length");
  if (rawValue === null) return null;
  const value = rawValue.trim();
  if (!/^\d+$/u.test(value)) return { status: 400, message: "invalid content length" };
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) return { status: 400, message: "invalid content length" };
  if (parsed > MAX_BODY_BYTES) return { status: 413, message: "request body too large" };
  return null;
}

async function cancelReader(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<void> {
  try { await reader.cancel(); } catch { /* The request is already being rejected. */ }
}

async function cancelRequestBody(request: Request): Promise<void> {
  if (request.body === null) return;
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  try {
    reader = request.body.getReader();
    await cancelReader(reader);
  } catch { /* Best-effort cleanup only. */ }
  finally {
    try { reader?.releaseLock(); } catch { /* Best-effort cleanup only. */ }
  }
}

async function rejectPost(request: Request, response: Response): Promise<Response> {
  await cancelRequestBody(request);
  return response;
}

async function readRequestBody(request: Request): Promise<{ bytes: Uint8Array } | { status: number; message: string }> {
  if (request.body === null) return { bytes: new Uint8Array() };
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      const chunk = result.value;
      if (!(chunk instanceof Uint8Array)) {
        await cancelReader(reader);
        return { status: 400, message: "invalid request body" };
      }
      total += chunk.byteLength;
      if (total > MAX_BODY_BYTES) {
        await cancelReader(reader);
        return { status: 413, message: "request body too large" };
      }
      chunks.push(chunk);
    }
  } catch {
    await cancelReader(reader);
    return { status: 400, message: "unable to read request body" };
  } finally {
    try { reader.releaseLock(); } catch { /* Best-effort cleanup only. */ }
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { bytes };
}

function hasValidMeta(value: JsonObject): boolean {
  return !hasOwn(value, "_meta") || isPlainObject(value._meta);
}

function isOptionalMetaParams(value: unknown): boolean {
  if (value === undefined) return true;
  return isPlainObject(value) && hasValidMeta(value) && Object.keys(value).every((key) => key === "_meta");
}

function validateMcpHeaders(headers: Headers, method: string, params: unknown): string | null {
  const protocolVersion = headers.get("mcp-protocol-version");
  if (protocolVersion !== null && !isSupportedProtocolVersion(protocolVersion)) return "unsupported MCP protocol version";
  if (method === INITIALIZE_METHOD && protocolVersion !== null && (!isPlainObject(params) || params.protocolVersion !== protocolVersion)) {
    return "MCP protocol version does not match initialize params";
  }
  const mcpMethod = headers.get("mcp-method");
  if (mcpMethod !== null && mcpMethod !== method) return "Mcp-Method does not match request method";
  const mcpName = headers.get("mcp-name");
  if (mcpName !== null && (mcpName !== TOOL_NAME || method !== TOOLS_CALL_METHOD || !isPlainObject(params) || params.name !== TOOL_NAME)) {
    return "Mcp-Name does not match tool name";
  }
  return null;
}

function validateInitializeParams(value: unknown): string | null {
  if (!isPlainObject(value) || !hasOnlyKeys(value, ["protocolVersion", "capabilities", "clientInfo", "_meta"]) || !hasOwn(value, "protocolVersion") || !hasOwn(value, "capabilities") || !hasOwn(value, "clientInfo") || !hasValidMeta(value)) return "invalid initialize params";
  if (!isSupportedProtocolVersion(value.protocolVersion)) return "unsupported protocol version";
  if (!isPlainObject(value.capabilities)) return "invalid client capabilities";
  if (!isPlainObject(value.clientInfo) || !hasOwn(value.clientInfo, "name") || !hasOwn(value.clientInfo, "version") || !isBoundedString(value.clientInfo.name, 128) || !isBoundedString(value.clientInfo.version, 64)) return "invalid client information";
  if (hasOwn(value.clientInfo, "title") && !isBoundedString(value.clientInfo.title, 128)) return "invalid client information";
  return null;
}

function toolDefinition(): JsonObject {
  return {
    name: TOOL_NAME,
    description: "Reports provider-reported filename/tag intersection for PyPI wheels; it does not perform compatibility, security, or digest verification.",
    inputSchema: {
      type: "object",
      properties: {
        releases: {
          type: "array", minItems: 1, maxItems: 8,
          items: {
            type: "object",
            properties: {
              project: { type: "string", minLength: 1, maxLength: 128, pattern: PROJECT_PATTERN },
              version: { type: "string", minLength: 1, maxLength: 64, pattern: VERSION_PATTERN },
            },
            required: ["project", "version"],
            additionalProperties: false,
          },
        },
        tags: {
          type: "array", minItems: 1, maxItems: 256, uniqueItems: true,
          items: { type: "string", minLength: 1, maxLength: 128, pattern: CANONICAL_TAG_PATTERN },
        },
      },
      required: ["releases", "tags"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  };
}

function stringifyJson(value: unknown): string {
  try {
    const serialized = JSON.stringify(value);
    return serialized === undefined ? "null" : serialized;
  } catch { return '{"error":"invalid_api_response"}'; }
}

async function readApiJson(response: Response): Promise<unknown> {
  try {
    const value = await response.json();
    return value === undefined ? { error: "invalid_api_response" } : value;
  } catch { return { error: "invalid_api_response" }; }
}

async function invokeWheelEvidence(id: RpcId, args: JsonObject): Promise<Response> {
  let status = 502;
  let structuredContent: unknown = { error: "wheel_evidence_unavailable" };
  try {
    const downstreamRequest = new Request(WHEEL_EVIDENCE_ENDPOINT, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(args),
    });
    const downstreamResponse = await collectWheelEvidencePOST(downstreamRequest);
    status = downstreamResponse.status;
    structuredContent = await readApiJson(downstreamResponse);
  } catch { /* Keep the response sanitized and preserve the MCP envelope. */ }
  const result: JsonObject = {
    content: [{ type: "text", text: stringifyJson(structuredContent) }],
    structuredContent,
  };
  if (status < 200 || status >= 300) result.isError = true;
  return jsonResponse({ jsonrpc: "2.0", id, result }, 200);
}

function methodNotAllowedResponse(): Response {
  return httpErrorResponse(405, "method not allowed", { allow: "POST" });
}

export async function POST(request: Request): Promise<Response> {
  if (!hasAllowedOrigin(request)) return rejectPost(request, httpErrorResponse(403, "forbidden origin"));
  if (!hasJsonContentType(request)) return rejectPost(request, httpErrorResponse(415, "unsupported media type"));
  const contentLengthFailure = validateContentLength(request);
  if (contentLengthFailure !== null) return rejectPost(request, httpErrorResponse(contentLengthFailure.status, contentLengthFailure.message));
  const bodyResult = await readRequestBody(request);
  if ("status" in bodyResult) return httpErrorResponse(bodyResult.status, bodyResult.message);
  let text: string;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bodyResult.bytes); }
  catch { return rpcErrorResponse(null, -32700, "parse error"); }
  let parsed: unknown;
  try { parsed = JSON.parse(text); }
  catch { return rpcErrorResponse(null, -32700, "parse error"); }
  if (!isPlainObject(parsed) || !hasOnlyKeys(parsed, ["jsonrpc", "method", "params", "id"]) || parsed.jsonrpc !== "2.0" || !isSafeMethod(parsed.method)) {
    return rpcErrorResponse(null, -32600, "invalid request");
  }
  const method = parsed.method;
  const hasId = hasOwn(parsed, "id");
  if (hasId && !isSafeRpcId(parsed.id)) return rpcErrorResponse(null, -32600, "invalid request id");
  const responseId: RpcId | null = hasId ? (parsed.id as RpcId) : null;
  if (!hasId && method !== INITIALIZED_NOTIFICATION) return rpcErrorResponse(null, -32600, "request id is required");
  const hasParams = hasOwn(parsed, "params");
  const params = hasParams ? parsed.params : undefined;
  const headerFailure = validateMcpHeaders(request.headers, method, params);
  if (headerFailure !== null) return httpErrorResponse(400, headerFailure);

  if (method === INITIALIZED_NOTIFICATION) {
    if (hasId) return rpcErrorResponse(null, -32600, "notifications cannot have ids");
    if (!isOptionalMetaParams(params)) return rpcErrorResponse(null, -32602, "invalid notification params");
    return emptyResponse(202);
  }
  if (!hasId) return rpcErrorResponse(null, -32600, "request id is required");
  if (method === INITIALIZE_METHOD) {
    if (!hasParams) return rpcErrorResponse(responseId, -32602, "params are required");
    const initializeFailure = validateInitializeParams(params);
    if (initializeFailure !== null) return rpcErrorResponse(responseId, -32602, initializeFailure);
    const initializeParams = params as JsonObject;
    return jsonResponse({
      jsonrpc: "2.0", id: responseId,
      result: {
        protocolVersion: initializeParams.protocolVersion,
        serverInfo: { name: "yumi-wheel-evidence", version: "1.0.0" },
        capabilities: { tools: {} },
      },
    });
  }
  if (method === DISCOVER_METHOD) {
    if (!isOptionalMetaParams(params)) return rpcErrorResponse(responseId, -32602, "invalid params");
    return jsonResponse({ jsonrpc: "2.0", id: responseId, result: { supportedVersions: [...SUPPORTED_PROTOCOL_VERSIONS], capabilities: { tools: {} } } });
  }
  if (method === TOOLS_LIST_METHOD) {
    if (!isOptionalMetaParams(params)) return rpcErrorResponse(responseId, -32602, "invalid params");
    return jsonResponse({ jsonrpc: "2.0", id: responseId, result: { tools: [toolDefinition()] } });
  }
  if (method === PING_METHOD) {
    if (!isOptionalMetaParams(params)) return rpcErrorResponse(responseId, -32602, "invalid params");
    return jsonResponse({ jsonrpc: "2.0", id: responseId, result: {} });
  }
  if (method === TOOLS_CALL_METHOD) {
    if (!hasParams || !isPlainObject(params) || !hasOnlyKeys(params, ["name", "arguments", "_meta"]) || !hasOwn(params, "name") || !hasOwn(params, "arguments") || !hasValidMeta(params)) return rpcErrorResponse(responseId, -32602, "invalid tool params");
    if (params.name !== TOOL_NAME) return rpcErrorResponse(responseId, -32602, "unknown tool");
    if (!isPlainObject(params.arguments)) return rpcErrorResponse(responseId, -32602, "invalid tool arguments");
    return invokeWheelEvidence(responseId, params.arguments);
  }
  return rpcErrorResponse(responseId, -32601, "method not found", 200);
}

export async function GET(): Promise<Response> { return methodNotAllowedResponse(); }
export async function HEAD(): Promise<Response> { return methodNotAllowedResponse(); }
export async function PUT(): Promise<Response> { return methodNotAllowedResponse(); }
export async function PATCH(): Promise<Response> { return methodNotAllowedResponse(); }
export async function DELETE(): Promise<Response> { return methodNotAllowedResponse(); }
export async function OPTIONS(): Promise<Response> { return methodNotAllowedResponse(); }
