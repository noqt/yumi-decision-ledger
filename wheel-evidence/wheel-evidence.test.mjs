import test, { mock } from "node:test";
import assert from "node:assert/strict";
import { collectWheelEvidence } from "./wheel-evidence.mjs";

const fixedClock = () => new Date("2026-01-02T03:04:05.000Z");
function sourceUrl(project, version) {
  return `https://pypi.org/pypi/${encodeURIComponent(project)}/${encodeURIComponent(version)}/json`;
}
function wheelRecord(filename, overrides = {}) {
  return {
    packagetype: "bdist_wheel", filename,
    url: `https://files.pythonhosted.org/packages/a/${filename}`,
    digests: { sha256: "a".repeat(64) }, size: 123,
    yanked: false, yanked_reason: null, requires_python: null,
    upload_time_iso_8601: "2025-01-01T00:00:00Z", ...overrides,
  };
}
function releaseDocument(project, version, urls, lastSerial = 1) {
  return {
    info: { name: project, version, extra_provider_field: true },
    urls, last_serial: lastSerial, extra_root_field: true,
  };
}
function jsonResponse(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), {
    status, headers: { "content-type": "application/json", ...headers },
  });
}
async function oneItem(fetchImpl, request = {
  releases: [{ project: "demo", version: "1.0" }], tags: ["py3-none-any"],
}) {
  const result = await collectWheelEvidence(request, { fetchImpl, clock: fixedClock });
  return result.items[0];
}
test("strict request shape and duplicate rejection", async () => {
  const neverFetch = () => { throw new Error("fetch must not be called"); };
  await assert.rejects(collectWheelEvidence({ releases: [{ project: "Foo_Bar", version: "1" }, { project: "foo-bar", version: "1" }], tags: ["py3-none-any"] }, { fetchImpl: neverFetch, clock: fixedClock }), /duplicate normalized release/i);
  await assert.rejects(collectWheelEvidence({ releases: [{ project: "demo", version: "1" }], tags: ["py3-none-any", "py3-none-any"] }, { fetchImpl: neverFetch, clock: fixedClock }), /duplicate tag/i);
  await assert.rejects(collectWheelEvidence({ releases: [{ project: "demo", version: "1", extra: true }], tags: ["py3-none-any"] }, { fetchImpl: neverFetch, clock: fixedClock }), /wrong keys/i);
  await assert.rejects(collectWheelEvidence({ releases: [{ project: "demo", version: "1" }], tags: ["cp313.cp313-win_amd64"] }, { fetchImpl: neverFetch, clock: fixedClock }), /canonical/i);
  for (const version of [".", "1?0", "1%0", "1\\0", "a".repeat(65)]) {
    await assert.rejects(collectWheelEvidence({ releases: [{ project: "demo", version }], tags: ["py3-none-any"] }, { fetchImpl: neverFetch, clock: fixedClock }), /safe version/i);
  }
});

test("request options, compressed Cartesian membership, universal and platform matches", async () => {
  const calls = [];
  const urls = [
    wheelRecord("demo_pkg-1.0-cp39.cp313-abi3.cp313-win_amd64.whl"),
    wheelRecord("demo_pkg-1.0-py3-none-any.whl"),
    wheelRecord("demo_pkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"),
    { packagetype: "sdist", filename: "demo_pkg-1.0.tar.gz" },
  ];
  const result = await collectWheelEvidence(
    { releases: [{ project: "Demo_Pkg", version: "1.0" }], tags: ["cp313-cp313-win_amd64", "py3-none-any"] },
    {
      clock: fixedClock,
      fetchImpl: async (url, options) => {
        calls.push({ url, options });
        assert.equal(url, sourceUrl("demo-pkg", "1.0"));
        assert.equal(options.method, "GET");
        assert.equal(options.headers.Accept, "application/json");
        assert.equal(options.credentials, "omit");
        assert.equal(options.redirect, "manual");
        assert.ok(options.signal);
        return jsonResponse(releaseDocument("demo-pkg", "1.0", urls, 17));
      },
    },
  );
  assert.equal(calls.length, 1);
  const item = result.items[0];
  assert.equal(item.status, "MATCHES");
  assert.equal(item.resolved_project, "demo-pkg");
  assert.equal(item.resolved_version, "1.0");
  assert.equal(item.last_serial, 17);
  assert.deepEqual(item.matches.map((match) => match.filename), [
    "demo_pkg-1.0-cp39.cp313-abi3.cp313-win_amd64.whl",
    "demo_pkg-1.0-py3-none-any.whl",
  ]);
  assert.deepEqual(item.matches[0].matched_caller_tags, ["cp313-cp313-win_amd64"]);
  assert.deepEqual(item.matches[1].matched_caller_tags, ["py3-none-any"]);
  assert.equal(item.matching_total, 2);
  assert.equal(item.truncated, false);
});

test("NO_MATCHING_WHEEL and yanked metadata pass-through", async () => {
  const noMatch = await oneItem(async () => jsonResponse(releaseDocument("demo", "1.0", [
    wheelRecord("demo-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"),
  ])));
  assert.equal(noMatch.status, "NO_MATCHING_WHEEL");
  assert.equal(noMatch.matching_total, 0);
  assert.deepEqual(noMatch.matches, []);
  const yanked = await oneItem(async () => jsonResponse(releaseDocument("demo", "1.0", [
    wheelRecord("demo-1.0-py3-none-any.whl", {
      yanked: true, yanked_reason: "provider reason", requires_python: ">=3.9", size: 456,
    }),
  ])));
  assert.equal(yanked.status, "MATCHES");
  assert.equal(yanked.matches[0].yanked, true);
  assert.equal(yanked.matches[0].yanked_reason, "provider reason");
  assert.equal(yanked.matches[0].requires_python, ">=3.9");
  assert.equal(yanked.matches[0].size, 456);
});
test("matching_total counts all matches and truncates at 50", async () => {
  const urls = Array.from({ length: 55 }, (_, index) => wheelRecord(`demo-1.0-${String(index + 1).padStart(3, "0")}-py3-none-any.whl`, { size: index + 1 })).reverse();
  const item = await oneItem(async () => jsonResponse(releaseDocument("demo", "1.0", urls)));
  assert.equal(item.status, "MATCHES");
  assert.equal(item.matching_total, 55);
  assert.equal(item.truncated, true);
  assert.equal(item.matches.length, 50);
  assert.equal(item.matches[0].filename, "demo-1.0-001-py3-none-any.whl");
  assert.equal(item.matches[49].filename, "demo-1.0-050-py3-none-any.whl");
  assert.equal(item.matches[0].size, 1);
  assert.equal(item.matches[49].size, 50);
});

test("concurrency never exceeds two and release order is stable", async () => {
  const versions = ["1.0", "1.1", "1.2", "1.3", "1.4"];
  let active = 0;
  let maximum = 0;
  const result = await collectWheelEvidence({
    releases: versions.map((version) => ({ project: "demo", version })), tags: ["py3-none-any"],
  }, {
    clock: fixedClock,
    fetchImpl: async (url) => {
      active += 1;
      maximum = Math.max(maximum, active);
      await new Promise((resolve) => setTimeout(resolve, 5));
      const match = /\/pypi\/demo\/([^/]+)\/json$/.exec(url);
      const version = decodeURIComponent(match[1]);
      active -= 1;
      return jsonResponse(releaseDocument("demo", version, []));
    },
  });
  assert.ok(maximum <= 2);
  assert.deepEqual(result.items.map((item) => item.requested_version), versions);
  assert.ok(result.items.every((item) => item.status === "NO_MATCHING_WHEEL"));
});
test("404, 429, redirect, AbortError, network failure, and upstream HTTP are distinct", async () => {
  const notFound = await oneItem(async () => new Response("", { status: 404 }));
  assert.equal(notFound.status, "HTTP_404");
  assert.equal(notFound.http_status, 404);
  const rateLimited = await oneItem(async () => new Response("", {
    status: 429, headers: { "retry-after": "999999" },
  }));
  assert.equal(rateLimited.status, "HTTP_429");
  assert.equal(rateLimited.retry_after, 86400);
  const redirect = await oneItem(async () => new Response("", {
    status: 302, headers: { location: "https://example.invalid/" },
  }));
  assert.equal(redirect.status, "REDIRECT");
  const abortError = await oneItem(async () => {
    const error = new Error("aborted"); error.name = "AbortError"; throw error;
  });
  assert.equal(abortError.status, "ABORT_ERROR");
  const networkFailure = await oneItem(async () => { throw new Error("network"); });
  assert.equal(networkFailure.status, "NETWORK_ERROR");
  const upstream = await oneItem(async () => new Response("", { status: 503 }));
  assert.equal(upstream.status, "UPSTREAM_HTTP");
  assert.equal(upstream.http_status, 503);
});
test("timeout is classified", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const pending = collectWheelEvidence(
      { releases: [{ project: "demo", version: "1.0" }], tags: ["py3-none-any"] },
      { clock: fixedClock, fetchImpl: () => new Promise(() => {}) },
    );
    await Promise.resolve();
    mock.timers.tick(8000);
    const result = await pending;
    assert.equal(result.items[0].status, "TIMEOUT");
    assert.equal(result.items[0].timeout_ms, 8000);
  } finally { mock.timers.reset(); }
});

test("content-length and actual streamed body limits are enforced", async () => {
  const declaredTooLarge = await oneItem(async () => new Response("{}", {
    status: 200, headers: { "content-length": String(2 * 1024 * 1024 + 1) },
  }));
  assert.equal(declaredTooLarge.status, "BODY_TOO_LARGE");
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(2 * 1024 * 1024 + 1));
      controller.close();
    },
  });
  const actualTooLarge = await oneItem(async () => new Response(stream, { status: 200 }));
  assert.equal(actualTooLarge.status, "BODY_TOO_LARGE");
});
test("invalid UTF-8, invalid JSON, and invalid provider schema are explicit", async () => {
  const invalidUtf8 = await oneItem(async () => new Response(
    new Uint8Array([0xc3, 0x28]), { status: 200 }));
  assert.equal(invalidUtf8.status, "INVALID_UTF8");
  const invalidJson = await oneItem(async () => new Response("{", { status: 200 }));
  assert.equal(invalidJson.status, "INVALID_JSON");
  const malformedSchema = await oneItem(async () => jsonResponse({
    info: { name: "demo", version: "1.0" },
    urls: [{ filename: "demo-1.0-py3-none-any.whl" }], last_serial: 1,
  }));
  assert.equal(malformedSchema.status, "SCHEMA_ERROR");
});
test("provider identity mismatches are explicit", async () => {
  const projectMismatch = await oneItem(async () => jsonResponse(releaseDocument("other", "1.0", [])));
  assert.equal(projectMismatch.status, "UPSTREAM_IDENTITY_MISMATCH");
  const versionMismatch = await oneItem(async () => jsonResponse(releaseDocument("demo", "1.1", [])));
  assert.equal(versionMismatch.status, "UPSTREAM_IDENTITY_MISMATCH");
});

test("provider artifact URLs are never fetched", async () => {
  const calls = [];
  const result = await collectWheelEvidence({
    releases: [{ project: "demo", version: "1.0" }], tags: ["py3-none-any"],
  }, {
    clock: fixedClock,
    fetchImpl: async (url) => {
      calls.push(url);
      assert.equal(url, sourceUrl("demo", "1.0"));
      return jsonResponse(releaseDocument("demo", "1.0", [
        wheelRecord("demo-1.0-py3-none-any.whl"),
      ]));
    },
  });
  assert.equal(calls.length, 1);
  assert.equal(result.items[0].matches.length, 1);
});

test("unmatched malformed wheel candidates fail schema validation", async () => {
  const item = await oneItem(async () => jsonResponse(releaseDocument("demo", "1.0", [wheelRecord("demo-1.0-cp312-cp312.whl")])));
  assert.equal(item.status, "SCHEMA_ERROR");
});
test("unsupported response bodies are explicit", async () => {
  const item = await oneItem(async () => ({ status: 200, headers: new Headers(), body: null }));
  assert.equal(item.status, "UNSUPPORTED_BODY");
});
test("body-phase timeout cancels the active reader", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  let cancelled = false;
  const body = new ReadableStream({ cancel() { cancelled = true; } });
  try {
    const pending = collectWheelEvidence(
      { releases: [{ project: "demo", version: "1.0" }], tags: ["py3-none-any"] },
      { clock: fixedClock, fetchImpl: async () => ({ status: 200, headers: new Headers(), body }) },
    );
    await Promise.resolve();
    await Promise.resolve();
    mock.timers.tick(8000);
    const result = await pending;
    await Promise.resolve();
    assert.equal(result.items[0].status, "TIMEOUT");
    assert.equal(cancelled, true);
  } finally { mock.timers.reset(); }
});
