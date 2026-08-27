import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer, request } from "node:http";
import { fileURLToPath } from "node:url";

import {
  assertInboundAuth,
  errorType,
  extractInboundBearer,
  logError,
} from "../bin/cc-openai-proxy.js";

function req(headers = {}) {
  return { headers };
}

async function unusedPort() {
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.notEqual(address, null);
  assert.equal(typeof address, "object");
  const port = address.port;
  server.close();
  await once(server, "close");
  return port;
}

function get(port, path, host) {
  return new Promise((resolve, reject) => {
    const req = request(
      {
        host: "127.0.0.1",
        port,
        path,
        headers: { host },
      },
      (res) => {
        res.resume();
        res.on("end", () => resolve(res.statusCode));
      },
    );
    req.on("error", reject);
    req.end();
  });
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
  assert.equal(extractInboundBearer(req({ authorization: "Bearer  secret " })), "secret");
  assert.equal(extractInboundBearer(req({ "x-api-key": "  key123 " })), "key123");
  assert.equal(extractInboundBearer(req({})), "");
  // Authorization wins over x-api-key when both present.
  assert.equal(extractInboundBearer(req({ authorization: "Bearer a", "x-api-key": "b" })), "a");
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
    assert.doesNotThrow(() => assertInboundAuth(req({ authorization: "Bearer s3cret" })));
    assert.doesNotThrow(() => assertInboundAuth(req({ "x-api-key": "s3cret" })));
    assert.throws(
      () => assertInboundAuth(req({ authorization: "Bearer wrong" })),
      (e) => e.status === 401,
    );
    assert.throws(
      () => assertInboundAuth(req({})),
      (e) => e.status === 401,
    );
    // A configured bearer wins even if ALLOW_ANON is set: no bypass.
    withEnv({ CC_OPENAI_PROXY_BEARER: "s3cret", CC_OPENAI_PROXY_ALLOW_ANON: "1" }, () => {
      assert.throws(
        () => assertInboundAuth(req({})),
        (e) => e.status === 401,
      );
    });
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

test("malformed Host and secret query neither crash nor leak", async (t) => {
  const port = await unusedPort();
  const proxyPath = fileURLToPath(new URL("../bin/cc-openai-proxy.js", import.meta.url));
  const child = spawn(
    process.execPath,
    [proxyPath, "--host", "127.0.0.1", "--port", String(port)],
    {
      env: { ...process.env, CC_OPENAI_PROXY_ALLOW_ANON: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let output = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    output += chunk;
  });
  child.stderr.on("data", (chunk) => {
    output += chunk;
  });
  t.after(async () => {
    if (child.exitCode !== null) return;
    child.kill();
    await once(child, "exit");
  });

  await new Promise((resolve, reject) => {
    const onData = (chunk) => {
      if (chunk.includes('"category":"server_started"')) {
        child.stderr.off("data", onData);
        resolve();
      }
    };
    child.stderr.on("data", onData);
    child.once("exit", (code) =>
      reject(new Error(`proxy exited before startup with code ${code}`)),
    );
  });

  const malformedHost = "host-secret.invalid:bad-port";
  const querySecret = "query-secret-value";
  assert.equal(await get(port, `/health?token=${querySecret}`, malformedHost), 200);
  assert.equal(await get(port, "/health", "localhost"), 200);
  assert.equal(child.exitCode, null);
  assert.equal(output.includes(malformedHost), false);
  assert.equal(output.includes(querySecret), false);
});

test("logError writes only allowlisted request metadata", (t) => {
  let output = "";
  t.mock.method(process.stderr, "write", (chunk) => {
    output += chunk;
    return true;
  });

  logError(502, {
    method: "POST",
    url: "/v1/messages?api_key=query-secret&prompt=private-message",
    headers: { authorization: "Bearer header-secret" },
    body: { token: "body-secret", message: "private-message" },
    error: new Error("arbitrary upstream message"),
  });

  assert.deepEqual(JSON.parse(output), {
    category: "request_error",
    method: "POST",
    pathname: "/v1/messages",
    status: 502,
    errorType: "api_error",
  });
  for (const secret of [
    "query-secret",
    "private-message",
    "header-secret",
    "body-secret",
    "arbitrary upstream message",
  ]) {
    assert.equal(output.includes(secret), false);
  }
});

test("logError replaces unallowlisted request metadata", (t) => {
  let output = "";
  t.mock.method(process.stderr, "write", (chunk) => {
    output += chunk;
    return true;
  });

  logError(200, {
    method: "PRIVATE-METHOD",
    url: "/customer/alice@example.com?token=query-secret",
  });

  assert.deepEqual(JSON.parse(output), {
    category: "request_error",
    method: "UNKNOWN",
    pathname: "<unknown>",
    status: 500,
    errorType: "api_error",
  });
  assert.equal(output.includes("alice@example.com"), false);
  assert.equal(output.includes("query-secret"), false);
  assert.equal(output.includes("PRIVATE-METHOD"), false);
});
