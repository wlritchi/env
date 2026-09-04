import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const helperPath = process.argv[2];
assert.ok(helperPath, "exact injected helper path is required");
const helper = fs.readFileSync(helperPath, "utf8");

const credentials = {};
const reads = [];
const environment = new Proxy(credentials, {
  get(target, key) {
    reads.push(key);
    return target[key];
  },
});
const fakeProcess = { env: environment };
const constructed = [];
let SDK;
class FakeSDK {
  constructor(options) {
    this.options = options;
    this.timeout = options.timeout;
    this.fetchOptions = options.fetchOptions;
    this.fetch = options.fetch;
    this._options = {
      ...options,
      defaultHeaders: {
        Authorization: "custom-secret",
        "X-Api-Key": "custom-key",
        ...options.defaultHeaders,
      },
    };
    this.calls = [];
    this.beta = {
      messages: {
        create: async (request, requestOptions) => {
          this.calls.push(["create", request, requestOptions]);
          return { provider: options.baseURL };
        },
        countTokens: async (request, requestOptions) => {
          this.calls.push(["countTokens", request, requestOptions]);
          return { input_tokens: 17 };
        },
      },
    };
    constructed.push(this);
  }
}
const context = vm.createContext({
  _ccMultiProviderSDK: () => SDK,
  process: fakeProcess,
});
const script = new vm.Script(
  `${helper};globalThis.__api={route:_ccMultiProviderRoute,modelInfo:_ccMultiProviderModelInfo,modelProvider:_ccMultiProviderModelProvider,inputTokens:_ccMultiProviderInputTokens,clients:_ccMultiProviderClients,catalog:_ccMultiProviderCatalog};`,
  { filename: helperPath },
);
script.runInContext(context);
const api = context.__api;

const fetchOptions = Object.freeze({
  dispatcher: "proxy-and-ca",
  headers: Object.freeze({ Authorization: "native-transport-secret" }),
});
const fetchOverride = Object.freeze(() => {});
const nativeClient = Object.freeze({
  native: true,
  timeout: 1234,
  fetchOptions,
  fetch: fetchOverride,
});
const nativeRequest = Object.freeze({
  model: "claude-sonnet-4-6",
  messages: [],
  fallback_credit_token: "native-credit",
});
const nativeOptions = Object.freeze({
  signal: Object.freeze({ native: true }),
  timeout: 99,
  headers: Object.freeze({ Authorization: "native-secret" }),
});
const nativeRoute = api.route(nativeClient, nativeRequest, nativeOptions);
assert.equal(nativeRoute[0], nativeClient);
assert.equal(nativeRoute[1], nativeRequest);
assert.equal(nativeRoute[2], nativeOptions);
assert.deepEqual(reads, []);

credentials.CC_KIMI_AUTH_TOKEN = " first-token ";
assert.throws(
  () => api.route(nativeClient, { model: "kimi:kimi-k2.7-code" }),
  /not a constructor/,
);
assert.equal(constructed.length, 0);
SDK = FakeSDK;

const request = Object.freeze({
  model: "kimi:kimi-k2.7-code",
  messages: Object.freeze([]),
  fallback_credit_token: "external-credit",
});
const signal = Object.freeze({ external: true });
const options = Object.freeze({
  signal,
  timeout: 77,
  headers: Object.freeze({
    authorization: "bearer-secret",
    "X-API-KEY": "api-secret",
    Cookie: "cookie-secret",
    traceparent: "00-trace",
    TraceState: "state",
    "x-safe-looking-but-not-allowlisted": "drop-me",
  }),
});
const [firstClient, firstOutbound, firstOptions] = api.route(
  nativeClient,
  request,
  options,
);
assert.equal(firstOutbound.model, "kimi-k2.7-code");
assert.ok(!("fallback_credit_token" in firstOutbound));
assert.equal(request.fallback_credit_token, "external-credit");
assert.equal(firstOptions.signal, signal);
assert.equal(firstOptions.timeout, 77);
assert.equal(
  JSON.stringify(firstOptions.headers),
  JSON.stringify({
    traceparent: "00-trace",
    TraceState: "state",
  }),
);
assert.equal(options.headers.authorization, "bearer-secret");
assert.equal(firstClient.options.maxRetries, 0);
assert.equal(firstClient.options.timeout, 1234);
assert.notEqual(firstClient.options.fetchOptions, fetchOptions);
assert.equal(firstClient.options.fetchOptions.dispatcher, "proxy-and-ca");
assert.ok(!("headers" in firstClient.options.fetchOptions));
assert.equal(firstClient.options.fetch, fetchOverride);
assert.equal(
  JSON.stringify(firstClient._options.defaultHeaders),
  JSON.stringify({ "User-Agent": "KimiCLI/1.5" }),
);
assert.equal(firstClient.options.authToken, "first-token");

const [, repeatedOutbound] = api.route(nativeClient, request, options);
assert.notEqual(repeatedOutbound, firstOutbound);
assert.equal(constructed.length, 1);
await Promise.all(
  Array.from({ length: 20 }, async () =>
    api.route(nativeClient, { model: "kimi:kimi-k2.7-code" }),
  ),
);
assert.equal(constructed.length, 1);

credentials.CC_KIMI_AUTH_TOKEN = "second-token";
const [secondClient] = api.route(nativeClient, request, options);
assert.notEqual(secondClient, firstClient);
assert.equal(secondClient.options.authToken, "second-token");
assert.equal(constructed.length, 2);
assert.equal(api.clients.size, 1);
assert.equal(api.clients.get("kimi").client, secondClient);

const changedFetchOptions = Object.freeze({
  dispatcher: "rotated-proxy-and-ca",
});
const changedNativeClient = Object.freeze({
  ...nativeClient,
  fetchOptions: changedFetchOptions,
});
const [transportClient] = api.route(changedNativeClient, request, options);
assert.notEqual(transportClient, secondClient);
assert.notEqual(transportClient.options.fetchOptions, changedFetchOptions);
assert.equal(
  transportClient.options.fetchOptions.dispatcher,
  "rotated-proxy-and-ca",
);
assert.equal(constructed.length, 3);
const changedTimeoutClient = Object.freeze({
  ...changedNativeClient,
  timeout: 4321,
});
const [timeoutClient] = api.route(changedTimeoutClient, request, options);
assert.notEqual(timeoutClient, transportClient);
assert.equal(timeoutClient.options.timeout, 4321);
assert.equal(constructed.length, 4);

credentials.CC_KIMI_AUTH_TOKEN = "   ";
let missingError;
try {
  api.route(nativeClient, request, options);
} catch (error) {
  missingError = error;
}
assert.equal(missingError.code, "EPROVIDERCREDENTIAL");
assert.match(missingError.message, /CC_KIMI_AUTH_TOKEN/);
assert.ok(!missingError.message.includes("first-token"));
assert.ok(!missingError.message.includes("second-token"));
assert.equal(api.clients.has("kimi"), false);
assert.equal(constructed.length, 4);

for (const model of [
  "arn:aws:bedrock:us-east-1:model/example",
  "gateway:model",
  "custom:model",
  "future-unqualified-model",
]) {
  const unknown = Object.freeze({ model });
  assert.equal(api.route(nativeClient, unknown)[1], unknown);
}
for (const model of [
  "kimi:",
  "zai:",
  "minimax:",
  "openai:",
  "zai:unknown",
  "openai:unknown",
  "Kimi:kimi-k2.7-code",
  "KIMI:kimi-k2.7-code",
  "kim:kimi-k2.7-code",
  "gateway:kimi-k2.7-code",
  "kimi-k2.7-code",
  "glm-5.2",
  "minimax:MiniMax-M2.7-typo",
]) {
  assert.throws(
    () => api.route(nativeClient, { model }),
    (error) => error.code === "EPROVIDERMODEL",
    model,
  );
}
assert.equal(api.catalog.length, 8);
for (const model of [
  "opus",
  "claude-opus-4-8",
  "fable",
  "claude-fable-5",
  "future-native-model",
  "gateway:model",
]) {
  assert.equal(api.modelProvider(model), "anthropic", model);
}
for (const [model, provider] of [
  ["kimi:kimi-k2.7-code", "kimi"],
  ["kimi-k2.7-code", "kimi"],
  ["zai:glm-5.2", "zai"],
  ["glm-5.2", "zai"],
  ["zai:glm-5-turbo", "zai"],
  ["glm-5-turbo", "zai"],
  ["minimax:MiniMax-M2.7", "minimax"],
  ["MiniMax-M2.7", "minimax"],
  ["openai:gpt-5.6-sol", "openai"],
  ["gpt-5.6-sol", "openai"],
  ["openai:gpt-5.6-terra", "openai"],
  ["gpt-5.6-terra", "openai"],
  ["openai:gpt-5.6-luna", "openai"],
  ["gpt-5.6-luna", "openai"],
]) {
  assert.equal(api.modelProvider(model), provider, model);
}

const [openAIClient, openAIRequest, openAIOptions] = api.route(
  nativeClient,
  Object.freeze({
    model: "openai:gpt-5.6-sol",
    messages: [],
    fallback_credit_token: "never-forward",
  }),
  options,
);
assert.equal(openAIRequest.model, "gpt-5.6-sol");
assert.ok(!("fallback_credit_token" in openAIRequest));
assert.equal(openAIClient.options.baseURL, "http://127.0.0.1:17780");
assert.equal(openAIClient.options.authToken, "cc-openai-local");
assert.equal(JSON.stringify(openAIClient._options.defaultHeaders), "{}");
assert.equal(
  JSON.stringify(openAIOptions.headers),
  JSON.stringify({ traceparent: "00-trace", TraceState: "state" }),
);
assert.ok(!reads.includes(undefined));

credentials.CC_ZAI_AUTH_TOKEN = "zai-token";
const [countClient, countRequest, countOptions] = api.route(
  nativeClient,
  Object.freeze({ model: "zai:glm-5.2", messages: [] }),
  Object.freeze({ headers: { Authorization: "never-forward" } }),
);
const countResult = await countClient.beta.messages.countTokens(
  countRequest,
  countOptions,
);
assert.equal(countResult.input_tokens, 17);
assert.equal(countClient.calls[0][0], "countTokens");
assert.equal(countClient.calls[0][1], countRequest);
assert.equal(Object.keys(countClient.calls[0][2]).length, 0);
assert.notEqual(countClient, nativeClient);
assert.equal(api.inputTokens("zai:glm-5.2", countResult), 17);
assert.throws(
  () => api.inputTokens("zai:glm-5.2", {}),
  (error) =>
    error.code === "EPROVIDERINCOMPATIBLE" &&
    error.message.includes("numeric input_tokens"),
);
assert.equal(api.inputTokens("claude-sonnet-4-6", {}), undefined);

const allText = JSON.stringify({ constructed, missing: missingError.message });
for (const secret of [
  "native-secret",
  "bearer-secret",
  "api-secret",
  "cookie-secret",
  "custom-secret",
  "custom-key",
]) {
  assert.ok(!firstClient._options.defaultHeaders[secret]);
}
assert.ok(!missingError.message.includes("token".repeat(10)));
assert.ok(reads.length >= 25);
assert.ok(allText.includes("second-token"));

console.log("multi-provider SDK routing: ok");
