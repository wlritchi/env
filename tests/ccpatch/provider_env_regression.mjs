#!/usr/bin/env node

import assert from "node:assert/strict";

const transportKey = "CLAUDE_CODE_PROVIDER_ENV_TRANSIENT";
const providerKeys = [
  "ANTHROPIC_BASE_URL",
  "ANTHROPIC_MODEL",
  "ANTHROPIC_DEFAULT_SONNET_MODEL",
  "ANTHROPIC_AUTH_TOKEN",
  "ANTHROPIC_API_KEY",
];
const requesterProcessEnv = {
  ANTHROPIC_BASE_URL: "https://requester.example",
  ANTHROPIC_MODEL: "requester-model",
  ANTHROPIC_AUTH_TOKEN: "requester-token",
};
const persistedJobEnv = {};
const expectedConsumerEnv = {
  ANTHROPIC_BASE_URL: "https://requester.example",
  ANTHROPIC_MODEL: "requester-model",
  ANTHROPIC_DEFAULT_SONNET_MODEL: undefined,
  ANTHROPIC_AUTH_TOKEN: "requester-token",
  ANTHROPIC_API_KEY: undefined,
};

function requireProviderEnv(providerEnv) {
  assert.ok(providerEnv && typeof providerEnv === "object");
  assert.ok(!Array.isArray(providerEnv));
  for (const [key, value] of Object.entries(providerEnv)) {
    assert.ok(providerKeys.includes(key));
    assert.ok(value === null || typeof value === "string");
  }
  return providerEnv;
}

function snapshotRequesterTransport(processEnv) {
  return Object.fromEntries(
    providerKeys.map((key) => [key, processEnv[key] ?? null]),
  );
}

function retainProviderEnv(providerEnv) {
  return Object.freeze({ ...requireProviderEnv(providerEnv) });
}

function buildWorkerEnv(processEnv, providerEnv) {
  const retainedPayload = retainProviderEnv(providerEnv);
  const workerEnv = { ...processEnv };
  workerEnv[transportKey] = JSON.stringify(retainedPayload);
  applyProviderEnv(workerEnv, retainedPayload);
  nativeProviderScrubs(workerEnv, persistedJobEnv);
  applyProviderEnv(workerEnv, retainedPayload);
  return workerEnv;
}

function captureTransport(workerEnv) {
  const serialized = workerEnv[transportKey];
  delete workerEnv[transportKey];
  assert.notEqual(serialized, undefined);
  return retainProviderEnv(JSON.parse(serialized));
}

function applyProviderEnv(targetEnv, providerEnv) {
  requireProviderEnv(providerEnv);
  for (const key of providerKeys) delete targetEnv[key];
  for (const [key, value] of Object.entries(providerEnv)) {
    if (value !== null) targetEnv[key] = value;
  }
}

function nativeProviderScrubs(targetEnv, jobEnv) {
  for (const key of providerKeys) {
    if (!jobEnv[key]) delete targetEnv[key];
  }
}

function mutateProviderEnv(targetEnv, phase) {
  targetEnv.ANTHROPIC_BASE_URL = `https://${phase}.example`;
  targetEnv.ANTHROPIC_MODEL = `${phase}-model`;
  targetEnv.ANTHROPIC_DEFAULT_SONNET_MODEL = `${phase}-sonnet`;
  targetEnv.ANTHROPIC_AUTH_TOKEN = `${phase}-token`;
  targetEnv.ANTHROPIC_API_KEY = `${phase}-api-key`;
}

function assertConsumerEnv(targetEnv, phase) {
  assert.deepEqual(
    Object.fromEntries(providerKeys.map((key) => [key, targetEnv[key]])),
    expectedConsumerEnv,
    phase,
  );
  assert.equal(targetEnv.UNRELATED, "preserved", phase);
  assert.equal(targetEnv[transportKey], undefined, phase);
}

async function simulateWorkerPath(path) {
  const events = [];
  const requesterPayload = snapshotRequesterTransport(requesterProcessEnv);
  events.push("requester-capture");
  const workerEnv = buildWorkerEnv(
    { UNRELATED: "preserved" },
    requesterPayload,
  );
  events.push(`${path}-transport`);

  const workerPayload = captureTransport(workerEnv);
  events.push("worker-capture");
  assert.ok(Object.isFrozen(workerPayload));
  assert.notStrictEqual(workerPayload, requesterPayload);

  mutateProviderEnv(workerEnv, "before-preAction");
  events.push("before-preAction-mutation");
  applyProviderEnv(workerEnv, workerPayload);
  events.push("I09-post-scrub-reapply");
  assertConsumerEnv(workerEnv, `${path}: I09 post-scrub`);

  mutateProviderEnv(workerEnv, "settings-initializer");
  events.push("settings-initializer");
  applyProviderEnv(workerEnv, workerPayload);
  events.push("preAction-reapply");
  assertConsumerEnv(workerEnv, `${path}: preAction`);

  mutateProviderEnv(workerEnv, "Ko");
  events.push("Ko");
  applyProviderEnv(workerEnv, workerPayload);
  events.push("operational-reapply");
  assertConsumerEnv(workerEnv, `${path}: operational`);

  await Promise.resolve().then(async () => {
    mutateProviderEnv(workerEnv, "delayed-Ko");
    events.push("delayed-Ko");
    applyProviderEnv(workerEnv, workerPayload);
    events.push("delayed-reapply");
    await Promise.resolve();
    events.push("telemetry-init");
    assertConsumerEnv(workerEnv, `${path}: delayed`);
  });

  assert.deepEqual(events, [
    "requester-capture",
    `${path}-transport`,
    "worker-capture",
    "before-preAction-mutation",
    "I09-post-scrub-reapply",
    "settings-initializer",
    "preAction-reapply",
    "Ko",
    "operational-reapply",
    "delayed-Ko",
    "delayed-reapply",
    "telemetry-init",
  ]);
}

for (const path of ["cold", "claimed", "respawn", "retry"]) {
  await simulateWorkerPath(path);
}

console.log("provider environment transient transport ordering: ok");
