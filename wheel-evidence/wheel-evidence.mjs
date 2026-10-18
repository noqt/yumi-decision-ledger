const MAX_BODY_BYTES = 2 * 1024 * 1024;
const MAX_MATCHES = 50;
const REQUEST_TIMEOUT_MS = 8000;
const MAX_RETRY_AFTER_SECONDS = 86400;
const MAX_PROVIDER_URL_CHARS = 2048;

const PROJECT_RE = /^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/;
const VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9.!+_-]*$/;
const WHEEL_DISTRIBUTION_RE = /^[A-Za-z0-9_]+$/;
const WHEEL_VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9.!+_]*$/;
const WHEEL_BUILD_RE = /^[0-9][A-Za-z0-9_]*$/;
const TAG_RE = /^[a-z0-9_]+-[a-z0-9_]+-[a-z0-9_]+$/;
const COMPRESSED_TAG_RE = /^[a-z0-9_]+(?:\.[a-z0-9_]+)*$/;
const ISO_8601_RE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;

function isPlainObject(value) {
  if (value === null || typeof value !== "object") return false;
  try {
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  } catch {
    return false;
  }
}

function exactKeys(value, expected, label) {
  const actual = Object.keys(value).sort();
  const wanted = expected.slice().sort();
  if (actual.length !== wanted.length ||
      actual.some((key, index) => key !== wanted[index])) {
    throw new TypeError(`${label} has the wrong keys`);
  }
}

function isAscii(value) {
  for (let index = 0; index < value.length; index += 1) {
    if (value.charCodeAt(index) > 0x7f) return false;
  }
  return true;
}

function validateProject(value, label) {
  if (typeof value !== "string" || value.length < 1 ||
      value.length > 128 || !isAscii(value) || !PROJECT_RE.test(value)) {
    throw new TypeError(`${label} is not a valid Python project name`);
  }
  return value;
}

function normalizeProject(project) {
  return project.toLowerCase().replace(/[-_.]+/g, "-");
}

function validateVersion(value, label) {
  if (typeof value !== "string" || value.length < 1 || value.length > 64 ||
      !isAscii(value) || !VERSION_RE.test(value)) {
    throw new TypeError(`${label} is not a safe version`);
  }
  return value;
}

function validateTags(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 256) {
    throw new TypeError("tags must contain 1 to 256 entries");
  }
  const seen = new Set();
  for (const tag of value) {
    if (typeof tag !== "string" || tag.length < 1 ||
        tag.length > 128 || !TAG_RE.test(tag)) {
      throw new TypeError("tags must be canonical uncompressed triples");
    }
    if (seen.has(tag)) throw new TypeError("duplicate tag");
    seen.add(tag);
  }
  return value.slice();
}

function prepareRequest(request) {
  if (!isPlainObject(request)) throw new TypeError("request must be an object");
  exactKeys(request, ["releases", "tags"], "request");
  if (!Array.isArray(request.releases) || request.releases.length < 1 ||
      request.releases.length > 8) {
    throw new TypeError("releases must contain 1 to 8 entries");
  }
  const tags = validateTags(request.tags);
  const seenPairs = new Set();
  const releases = [];
  for (let index = 0; index < request.releases.length; index += 1) {
    const release = request.releases[index];
    if (!isPlainObject(release)) throw new TypeError(`release ${index} must be an object`);
    exactKeys(release, ["project", "version"], `release ${index}`);
    const project = validateProject(release.project, `release ${index}.project`);
    const version = validateVersion(release.version, `release ${index}.version`);
    const normalizedProject = normalizeProject(project);
    const pair = `${normalizedProject}\u0000${version}`;
    if (seenPairs.has(pair)) throw new TypeError("duplicate normalized release");
    seenPairs.add(pair);
    releases.push({
      project, version, normalizedProject,
      sourceUrl: `https://pypi.org/pypi/${encodeURIComponent(normalizedProject)}/${encodeURIComponent(version)}/json`,
    });
  }
  return {
    releases, tags,
    requestCopy: {
      releases: releases.map(({ project, version }) => ({ project, version })),
      tags: tags.slice(),
    },
  };
}

function providerText(value, limit, label, allowEmpty = false) {
  if (typeof value !== "string" || value.length > limit ||
      (!allowEmpty && value.length === 0)) throw new Error(label);
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x20 || code === 0x7f) throw new Error(label);
  }
  return value;
}

function validateProviderBasename(value) {
  if (typeof value !== "string" || value.length < 1 ||
      value.length > 512 || !isAscii(value)) throw new Error("filename");
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 0x21 || code > 0x7e || code === 0x25 ||
        code === 0x2f || code === 0x5c || code === 0x3f || code === 0x23) {
      throw new Error("filename");
    }
  }
  return value;
}

function parseWheelFilename(filename, expectedNormalizedProject, expectedVersion) {
  const stem = filename.slice(0, -4);
  const fields = stem.split("-");
  if (fields.length !== 5 && fields.length !== 6) throw new Error("wheel filename field count");
  if (fields.some((field) => field.length === 0)) throw new Error("wheel filename empty field");
  const distribution = fields[0];
  const wheelVersion = fields[1];
  if (!WHEEL_DISTRIBUTION_RE.test(distribution)) throw new Error("wheel distribution");
  if (!WHEEL_VERSION_RE.test(wheelVersion)) throw new Error("wheel version");
  if (fields.length === 6 && !WHEEL_BUILD_RE.test(fields[2])) throw new Error("wheel build");
  const wheelProject = normalizeProject(distribution.replace(/_+/g, "-"));
  if (wheelProject !== expectedNormalizedProject) throw new Error("wheel distribution identity");
  if (wheelVersion.replace(/-/g, "_") !== expectedVersion.replace(/-/g, "_")) {
    throw new Error("wheel version identity");
  }
  const interpreterField = fields[fields.length - 3];
  const abiField = fields[fields.length - 2];
  const platformField = fields[fields.length - 1];
  if (!COMPRESSED_TAG_RE.test(interpreterField) || !COMPRESSED_TAG_RE.test(abiField) || !COMPRESSED_TAG_RE.test(platformField)) {
    throw new Error("wheel filename tags");
  }
  return {
    interpreters: interpreterField.split("."),
    abis: abiField.split("."),
    platforms: platformField.split("."),
  };
}

function matchingCallerTags(parsedWheel, callerTags) {
  const matches = [];
  for (const callerTag of callerTags) {
    const [interpreter, abi, platform] = callerTag.split("-");
    if (parsedWheel.interpreters.includes(interpreter) &&
        parsedWheel.abis.includes(abi) && parsedWheel.platforms.includes(platform)) {
      matches.push(callerTag);
    }
  }
  return matches;
}

function validateProviderUrl(value, filename) {
  const url = providerText(value, MAX_PROVIDER_URL_CHARS, "provider URL");
  if (!isAscii(url) || url.includes("\\")) throw new Error("provider URL");
  const authority = /^https:\/\/([^/?#]+)(?:\/|$)/.exec(url);
  if (!authority || authority[1] !== "files.pythonhosted.org") throw new Error("provider URL");
  let parsed;
  try { parsed = new URL(url); } catch { throw new Error("provider URL"); }
  if (parsed.protocol !== "https:" || parsed.hostname !== "files.pythonhosted.org" ||
      parsed.username !== "" || parsed.password !== "" || parsed.port !== "" ||
      parsed.search !== "" || parsed.hash !== "") throw new Error("provider URL");
  const pathParts = parsed.pathname.split("/");
  const finalSegment = pathParts[pathParts.length - 1];
  if (!finalSegment) throw new Error("provider URL");
  let decoded;
  try { decoded = decodeURIComponent(finalSegment); } catch { throw new Error("provider URL"); }
  if (decoded !== filename) throw new Error("provider URL");
  return url;
}

function validateTimestamp(value) {
  const timestamp = providerText(value, 128, "upload_time_iso_8601");
  if (!ISO_8601_RE.test(timestamp) || !Number.isFinite(Date.parse(timestamp))) {
    throw new Error("upload_time_iso_8601");
  }
  return timestamp;
}

function validateMatchingRecord(record, filename) {
  const providerUrl = validateProviderUrl(record.url, filename);
  if (!isPlainObject(record.digests)) throw new Error("digests");
  const reportedSha256 = record.digests.sha256;
  if (typeof reportedSha256 !== "string" || !/^[a-f0-9]{64}$/.test(reportedSha256)) {
    throw new Error("sha256");
  }
  if (!Number.isSafeInteger(record.size) || record.size < 0) throw new Error("size");
  if (typeof record.yanked !== "boolean") throw new Error("yanked");
  const yankedReason = record.yanked_reason === null ? null :
    providerText(record.yanked_reason, 256, "yanked_reason", true);
  const requiresPython = record.requires_python === null ? null :
    providerText(record.requires_python, 256, "requires_python", true);
  const uploadTime = validateTimestamp(record.upload_time_iso_8601);
  return {
    filename, provider_url: providerUrl, reported_sha256: reportedSha256,
    size: record.size, yanked: record.yanked, yanked_reason: yankedReason,
    requires_python: requiresPython, upload_time_iso_8601: uploadTime,
  };
}

class UpstreamIdentityMismatch extends Error {
  constructor() {
    super("upstream identity mismatch");
    this.name = "UpstreamIdentityMismatch";
  }
}
function parseProviderDocument(document, spec, callerTags, base) {
  if (!isPlainObject(document) || !isPlainObject(document.info) || !Array.isArray(document.urls) ||
      document.urls.length > 1000 || !Number.isSafeInteger(document.last_serial) || document.last_serial < 0) {
    throw new Error("provider document shape");
  }
  const resolvedProject = validateProject(document.info.name, "info.name");
  const resolvedVersion = validateVersion(document.info.version, "info.version");
  if (normalizeProject(resolvedProject) !== spec.normalizedProject) throw new UpstreamIdentityMismatch();
  if (resolvedVersion !== spec.version) throw new UpstreamIdentityMismatch();
  const allMatches = [];
  let matchingTotal = 0;
  for (const record of document.urls) {
    if (!isPlainObject(record)) throw new Error("url record");
    const packagetype = providerText(record.packagetype, 64, "packagetype");
    const filename = validateProviderBasename(record.filename);
    if (packagetype !== "bdist_wheel") continue;
    if (filename.length < 5 || !filename.endsWith(".whl")) throw new Error("filename");
    const parsedWheel = parseWheelFilename(filename, spec.normalizedProject, resolvedVersion);
    const match = validateMatchingRecord(record, filename);
    const matchedTags = matchingCallerTags(parsedWheel, callerTags);
    if (matchedTags.length === 0) continue;
    match.matched_caller_tags = matchedTags;
    allMatches.push(match);
    matchingTotal += 1;
  }
  const matches = allMatches.sort((left, right) => left.filename < right.filename ? -1 : left.filename > right.filename ? 1 : 0).slice(0, MAX_MATCHES);
  return {
    ...base, resolved_project: resolvedProject, resolved_version: resolvedVersion,
    last_serial: document.last_serial,
    status: matchingTotal > 0 ? "MATCHES" : "NO_MATCHING_WHEEL",
    matching_total: matchingTotal, truncated: matchingTotal > MAX_MATCHES, matches,
  };
}

function headerGet(headers, name) {
  try {
    if (headers && typeof headers.get === "function") {
      const value = headers.get(name);
      return value === null || value === undefined ? null : String(value);
    }
    if (headers && typeof headers === "object") {
      if (Object.prototype.hasOwnProperty.call(headers, name)) return String(headers[name]);
      const lowerName = name.toLowerCase();
      for (const key of Object.keys(headers)) {
        if (key.toLowerCase() === lowerName) return String(headers[key]);
      }
    }
  } catch { return null; }
  return null;
}

function contentLengthTooLarge(headers) {
  const raw = headerGet(headers, "content-length");
  if (raw === null) return false;
  const value = raw.trim();
  if (!/^\d+$/.test(value)) return false;
  const length = Number(value);
  return !Number.isFinite(length) || length > MAX_BODY_BYTES;
}

function toBytes(value) {
  if (typeof Uint8Array !== "undefined" && value instanceof Uint8Array) return new Uint8Array(value);
  if (typeof ArrayBuffer !== "undefined" && value instanceof ArrayBuffer) return new Uint8Array(value);
  if (typeof ArrayBuffer !== "undefined" && typeof ArrayBuffer.isView === "function" &&
      ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  return null;
}

function cancelReaderSafely(reader) {
  if (!reader || typeof reader.cancel !== "function") return Promise.resolve();
  try { return Promise.resolve(reader.cancel()).catch(() => undefined); }
  catch { return Promise.resolve(); }
}

function decodeChunks(chunks, total) {
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try {
    if (typeof TextDecoder !== "function") return { kind: "invalid_utf8" };
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return { kind: "ok", text };
  } catch { return { kind: "invalid_utf8" }; }
}

async function readResponseBody(response, registerReader) {
  const body = response.body;
  if (!body || typeof body.getReader !== "function") return { kind: "unsupported_body" };
  let reader;
  try { reader = body.getReader(); }
  catch (error) { return { kind: "read_error", error }; }
  registerReader(reader);
  const chunks = [];
  let total = 0;
  try {
    if (contentLengthTooLarge(response.headers)) {
      await cancelReaderSafely(reader);
      return { kind: "too_large" };
    }
    while (true) {
      let part;
      try { part = await reader.read(); }
      catch (error) {
        await cancelReaderSafely(reader);
        return { kind: "read_error", error };
      }
      if (!part || typeof part !== "object") {
        await cancelReaderSafely(reader);
        return { kind: "invalid_chunk" };
      }
      if (part.done) break;
      const bytes = toBytes(part.value);
      if (!bytes) {
        await cancelReaderSafely(reader);
        return { kind: "invalid_chunk" };
      }
      total += bytes.byteLength;
      if (total > MAX_BODY_BYTES) {
        await cancelReaderSafely(reader);
        return { kind: "too_large" };
      }
      chunks.push(bytes);
    }
    return decodeChunks(chunks, total);
  } finally {
    try { reader.releaseLock(); } catch { /* Best effort. */ }
  }
}

function parseRetryAfter(headers) {
  const raw = headerGet(headers, "retry-after");
  if (raw === null) return null;
  const value = raw.trim();
  if (!/^\d{1,12}$/.test(value)) return null;
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return null;
  return Math.min(seconds, MAX_RETRY_AFTER_SECONDS);
}

async function requestWithDeadline(sourceUrl, fetchImpl) {
  if (typeof globalThis.AbortController !== "function") return { kind: "network" };
  const controller = new globalThis.AbortController();
  let timedOut = false;
  let activeReader = null;
  let timer;
  const operation = (async () => {
    const response = await fetchImpl(sourceUrl, {
      method: "GET", headers: { Accept: "application/json" }, credentials: "omit",
      redirect: "manual", signal: controller.signal,
    });
    if (!response || (typeof response !== "object" && typeof response !== "function")) return { kind: "malformed_response" };
    if (response.redirected === true || response.type === "opaqueredirect") {
      return { kind: "redirect", status: Number.isInteger(response.status) ? response.status : null };
    }
    const status = response.status;
    if (!Number.isInteger(status) || status < 100 || status > 999) return { kind: "malformed_response" };
    if (status >= 300 && status < 400) return { kind: "redirect", status };
    if (status === 404) return { kind: "http_404", status };
    if (status === 429) return { kind: "http_429", status, retryAfter: parseRetryAfter(response.headers) };
    if (status !== 200) return { kind: "http", status };
    return { kind: "body", body: await readResponseBody(response, (reader) => { activeReader = reader; }) };
  })();
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      timedOut = true;
      try { controller.abort(); } catch { /* Best effort. */ }
      void cancelReaderSafely(activeReader);
      resolve({ kind: "timeout" });
    }, REQUEST_TIMEOUT_MS);
  });
  let outcome;
  try { outcome = await Promise.race([operation, timeout]); }
  catch (error) { outcome = { kind: "thrown", error }; }
  finally { clearTimeout(timer); }
  if (timedOut || outcome?.kind === "timeout") return { kind: "timeout" };
  if (outcome?.kind === "thrown") {
    if (outcome.error && outcome.error.name === "AbortError") return { kind: "abort" };
    return { kind: "network" };
  }
  return outcome;
}

function failure(base, code, extra = {}) {
  return { ...base, status: code, error_code: code, error: { code }, ...extra };
}

function observedAt(clock) {
  try {
    const value = clock();
    if (!(value instanceof Date) || !Number.isFinite(value.getTime())) return null;
    return value.toISOString();
  } catch { return null; }
}

async function processRelease(spec, callerTags, fetchImpl, clock) {
  const base = {
    requested_project: spec.project, normalized_project: spec.normalizedProject,
    requested_version: spec.version, source_url: spec.sourceUrl, observed_at: observedAt(clock),
  };
  if (base.observed_at === null) return failure(base, "SCHEMA_ERROR", { reason: "invalid_clock" });
  let outcome;
  try { outcome = await requestWithDeadline(spec.sourceUrl, fetchImpl); }
  catch { return failure(base, "NETWORK_ERROR"); }
  if (outcome.kind === "timeout") return failure(base, "TIMEOUT", { timeout_ms: REQUEST_TIMEOUT_MS });
  if (outcome.kind === "abort") return failure(base, "ABORT_ERROR");
  if (outcome.kind === "network") return failure(base, "NETWORK_ERROR");
  if (outcome.kind === "malformed_response") return failure(base, "UPSTREAM_HTTP");
  if (outcome.kind === "redirect") {
    const extra = {};
    if (Number.isInteger(outcome.status)) extra.http_status = outcome.status;
    return failure(base, "REDIRECT", extra);
  }
  if (outcome.kind === "http_404") return failure(base, "HTTP_404", { http_status: 404 });
  if (outcome.kind === "http_429") return failure(base, "HTTP_429", { http_status: 429, retry_after: outcome.retryAfter });
  if (outcome.kind === "http") return failure(base, "UPSTREAM_HTTP", { http_status: outcome.status });
  if (outcome.kind !== "body") return failure(base, "NETWORK_ERROR");
  const body = outcome.body;
  if (body.kind === "unsupported_body") return failure(base, "UNSUPPORTED_BODY");
  if (body.kind === "too_large") return failure(base, "BODY_TOO_LARGE", { max_bytes: MAX_BODY_BYTES });
  if (body.kind === "invalid_utf8" || body.kind === "invalid_chunk") return failure(base, "INVALID_UTF8");
  if (body.kind === "read_error") {
    if (body.error && body.error.name === "AbortError") return failure(base, "ABORT_ERROR");
    return failure(base, "NETWORK_ERROR");
  }
  if (body.kind !== "ok") return failure(base, "NETWORK_ERROR");
  let document;
  try { document = JSON.parse(body.text); } catch { return failure(base, "INVALID_JSON"); }
  try { return parseProviderDocument(document, spec, callerTags, base); }
  catch (error) {
    if (error instanceof UpstreamIdentityMismatch) return failure(base, "UPSTREAM_IDENTITY_MISMATCH");
    return failure(base, "SCHEMA_ERROR");
  }
}

function utf8ByteLength(value) {
  if (typeof TextEncoder !== "function") return value.length;
  return new TextEncoder().encode(value).byteLength;
}

function enforceOutputLimit(value) {
  const serialized = JSON.stringify(value);
  if (utf8ByteLength(serialized) > MAX_BODY_BYTES) throw new Error("result exceeds 2 MiB");
}

export async function collectWheelEvidence(request, options = {}) {
  if (!isPlainObject(options)) throw new TypeError("options must be an object");
  const prepared = prepareRequest(request);
  const fetchImpl = options.fetchImpl === undefined ? globalThis.fetch : options.fetchImpl;
  const clock = options.clock === undefined ? () => new Date() : options.clock;
  if (typeof fetchImpl !== "function") throw new TypeError("fetchImpl must be callable");
  if (typeof clock !== "function") throw new TypeError("clock must be callable");
  const results = new Array(prepared.releases.length);
  let nextIndex = 0;
  async function worker() {
    while (true) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= prepared.releases.length) return;
      results[index] = await processRelease(prepared.releases[index], prepared.tags, fetchImpl, clock);
    }
  }
  const workers = [];
  const workerCount = Math.min(2, prepared.releases.length);
  for (let index = 0; index < workerCount; index += 1) workers.push(worker());
  await Promise.all(workers);
  const result = {
    schema: "yumi.wheel-evidence.v1",
    producer: { name: "Yumi Wheel Evidence", version: "1" },
    request: prepared.requestCopy, items: results,
    assumptions: [
      "Caller tags are canonical lower-case uncompressed interpreter-ABI-platform triples.",
      "Wheel matching is exact member intersection against the supplied caller tags and does not expand a compatibility universe.",
      "The PyPI JSON provider is the underlying fact source; provider-reported metadata is passed through only after validation.",
      "The producer identity is separate from the provider identity represented by each source_url.",
    ],
    limitations: [
      "No artifact URL is fetched, and no artifact download or checksum verification is performed.",
      "No origin, installability, compatibility, dependency, security, or Requires-Python evaluation is performed.",
      "Provider-reported yanked state, digests, sizes, URLs, and timestamps are not independently verified.",
      "Provider identity binding requires normalized project equality and literal requested-version equality; no full PEP 440 parser is used.",
      "Rate-limit retry_after is bounded and informational only; no retry or polling occurs.",
      "matching_total counts matching provider wheel records; matches contains at most the first 50 matching filenames in per-item lexicographic order.",
      "The output is bounded to 2 MiB.",
      "Direct JSON plus packaging are simpler for one-offs.",
    ],
  };
  enforceOutputLimit(result);
  return result;
}
