import test from "node:test";
import assert from "node:assert/strict";

import {
  assertInboundAuth,
  errorType,
  extractInboundBearer,
  sanitizeLogMessage,
} from "../bin/cc-openai-proxy.js";

function req(headers = {}) {
  return { headers };
}

// Run body with a fresh CC_OPENAI_PROXY_* env, restoring the prior values after.
function withEnv(env, body) {
  const keys = ["CC_OPENAI_PROXY_BEARER", "CC_OPENAI_PROXY_ALLOW_ANON"];
  const saved = Object.fromEntries(keys.map((k) => [k, process.env[k]]));
  try {
    for (const k of keys) delete process.env[k];
    Object.assign(process.env, env);
    body();
  } finally {
    for (const k of keys) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  }
}

test("extractInboundBearer reads Authorization then x-api-key, trims, defaults empty", () => {
  assert.equal(
    extractInboundBearer(req({ authorization: "Bearer  secret " })),
    "secret",
  );
  assert.equal(
    extractInboundBearer(req({ "x-api-key": "  key123 " })),
    "key123",
  );
  assert.equal(extractInboundBearer(req({})), "");
  // Authorization wins over x-api-key when both present.
  assert.equal(
    extractInboundBearer(req({ authorization: "Bearer a", "x-api-key": "b" })),
    "a",
  );
});

test("assertInboundAuth fails secure when no bearer is configured", () => {
  withEnv({}, () => {
    assert.throws(
      () => assertInboundAuth(req({ authorization: "Bearer anything" })),
      (e) => e.status === 401,
    );
  });
});

test("assertInboundAuth allows anonymous only with the explicit escape hatch", () => {
  withEnv({ CC_OPENAI_PROXY_ALLOW_ANON: "1" }, () => {
    assert.doesNotThrow(() => assertInboundAuth(req({})));
  });
  // Any value other than exactly "1" does not open the door.
  withEnv({ CC_OPENAI_PROXY_ALLOW_ANON: "true" }, () => {
    assert.throws(
      () => assertInboundAuth(req({})),
      (e) => e.status === 401,
    );
  });
});

test("assertInboundAuth enforces a configured bearer (constant-time), via either header", () => {
  withEnv({ CC_OPENAI_PROXY_BEARER: "s3cret" }, () => {
    assert.doesNotThrow(() =>
      assertInboundAuth(req({ authorization: "Bearer s3cret" })),
    );
    assert.doesNotThrow(() =>
      assertInboundAuth(req({ "x-api-key": "s3cret" })),
    );
    assert.throws(
      () => assertInboundAuth(req({ authorization: "Bearer wrong" })),
      (e) => e.status === 401,
    );
    assert.throws(
      () => assertInboundAuth(req({})),
      (e) => e.status === 401,
    );
    // A configured bearer wins even if ALLOW_ANON is set: no bypass.
    withEnv(
      { CC_OPENAI_PROXY_BEARER: "s3cret", CC_OPENAI_PROXY_ALLOW_ANON: "1" },
      () => {
        assert.throws(
          () => assertInboundAuth(req({})),
          (e) => e.status === 401,
        );
      },
    );
  });
});

test("errorType maps status classes for the Anthropic error envelope", () => {
  assert.equal(errorType(500), "api_error");
  assert.equal(errorType(502), "api_error");
  assert.equal(errorType(401), "authentication_error");
  assert.equal(errorType(403), "authentication_error");
  assert.equal(errorType(400), "invalid_request_error");
  assert.equal(errorType(404), "invalid_request_error");
});

test("sanitizeLogMessage redacts common secret formats", () => {
  assert.equal(
    sanitizeLogMessage("Authorization: Bearer sk-secret"),
    "Authorization: Bearer [redacted]",
  );
  assert.equal(sanitizeLogMessage("Bearer sk-secret"), "Bearer [redacted]");
  assert.equal(
    sanitizeLogMessage('{"api_key":"sk-secret","ok":true}'),
    '{"api_key":"[redacted]","ok":true}',
  );
  assert.equal(
    sanitizeLogMessage('{"authorization":"Bearer sk-secret"}'),
    '{"authorization":"Bearer [redacted]"}',
  );
});
