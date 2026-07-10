#!/usr/bin/env node
import { createServer } from "node:http";
import { readFile, rename, writeFile } from "node:fs/promises";
import { randomUUID, timingSafeEqual } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 17780;
const DEFAULT_PROVIDER = "openai-codex";
const DEFAULT_MODEL = "gpt-5.5";
const DEFAULT_HAIKU_MODEL = "gpt-5.4-mini";
const MAX_BODY_BYTES = 64 * 1024 * 1024;

let piAiPromise;
let piOauthPromise;
let cachedApiKey;
let cachedApiKeyExpires = 0;
const processSessionId = `cc-openai-${randomUUID()}`;

function usage() {
  return `usage: cc-openai-proxy [--host HOST] [--port PORT]

Environment:
  CC_OPENAI_MODEL            Override all requested models
  CC_OPENAI_OPUS_MODEL       Model for Anthropic opus requests (${DEFAULT_MODEL})
  CC_OPENAI_SONNET_MODEL     Model for Anthropic sonnet requests (${DEFAULT_MODEL})
  CC_OPENAI_HAIKU_MODEL      Model for Anthropic haiku requests (${DEFAULT_HAIKU_MODEL})
  CC_OPENAI_AUTH_FILE        Auth file (default ~/.pi/agent/auth.json)
  CC_OPENAI_TRANSPORT        pi-ai transport: auto, sse, websocket, websocket-cached
  CC_OPENAI_CACHE_RETENTION  pi-ai cache retention: short, long, none
`;
}

function parseArgs(argv) {
  const config = {
    host: process.env.CC_OPENAI_PROXY_HOST || DEFAULT_HOST,
    port: Number.parseInt(process.env.CC_OPENAI_PROXY_PORT || String(DEFAULT_PORT), 10),
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--host") {
      config.host = argv[++i];
    } else if (arg === "--port") {
      config.port = Number.parseInt(argv[++i], 10);
    } else if (arg === "--help" || arg === "-h") {
      process.stdout.write(usage());
      process.exit(0);
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }

  if (!config.host) throw new Error("host must not be empty");
  if (!Number.isInteger(config.port) || config.port <= 0 || config.port > 65535) {
    throw new Error(`invalid port: ${config.port}`);
  }
  return config;
}

async function loadPiAi() {
  // getModel/streamSimple/completeSimple live under the /compat subpath as of
  // pi-ai 0.80.x (they moved off the package root). It is a deprecated shim over
  // the newer getBuiltinModel/Models API -- fine for now; migrating off it is a
  // separate follow-up. The bump to 0.80.x is what gives us the gpt-5.6 models
  // (sol/terra/luna) natively, with upstream cost data, instead of synthesizing
  // descriptors ourselves.
  piAiPromise ??= import("@earendil-works/pi-ai/compat");
  return piAiPromise;
}

async function loadPiOauth() {
  piOauthPromise ??= import("@earendil-works/pi-ai/oauth");
  return piOauthPromise;
}

function authPath() {
  return (
    process.env.CC_OPENAI_AUTH_FILE ||
    process.env.PI_AUTH_FILE ||
    join(homedir(), ".pi", "agent", "auth.json")
  );
}

async function readAuthFile() {
  const path = authPath();
  try {
    return { path, data: JSON.parse(await readFile(path, "utf8")) };
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(`missing pi auth file: ${path}. Run pi /login for ChatGPT Plus/Pro first.`);
    }
    throw new Error(
      `failed to read pi auth file ${path}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

async function writeAuthFile(path, data) {
  const tmp = `${path}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(tmp, `${JSON.stringify(data, null, 2)}\n`, { mode: 0o600 });
  await rename(tmp, path);
}

async function resolveOpenAICodexApiKey() {
  const explicit =
    process.env.CC_OPENAI_CODEX_TOKEN ||
    process.env.OPENAI_CODEX_TOKEN ||
    process.env.OPENAI_CODEX_API_KEY;
  if (explicit) return explicit;

  if (cachedApiKey && Date.now() < cachedApiKeyExpires - 60_000) {
    return cachedApiKey;
  }

  const { path, data } = await readAuthFile();
  const entry = data[DEFAULT_PROVIDER];
  if (!entry) {
    throw new Error(
      `missing ${DEFAULT_PROVIDER} credentials in ${path}. Run pi /login for ChatGPT Plus/Pro first.`,
    );
  }

  if (entry.type === "api_key" && typeof entry.key === "string" && entry.key) {
    cachedApiKey = entry.key;
    cachedApiKeyExpires = Date.now() + 10 * 60_000;
    return cachedApiKey;
  }

  if (entry.type !== "oauth") {
    throw new Error(`unsupported ${DEFAULT_PROVIDER} credential type in ${path}: ${entry.type}`);
  }

  const { getOAuthApiKey } = await loadPiOauth();
  const credentials = { [DEFAULT_PROVIDER]: stripType(entry) };
  const result = await getOAuthApiKey(DEFAULT_PROVIDER, credentials);
  if (!result?.apiKey) {
    throw new Error(`failed to resolve ${DEFAULT_PROVIDER} OAuth token from ${path}`);
  }

  data[DEFAULT_PROVIDER] = { type: "oauth", ...result.newCredentials };
  await writeAuthFile(path, data);

  cachedApiKey = result.apiKey;
  cachedApiKeyExpires = Number(result.newCredentials.expires || 0);
  return cachedApiKey;
}

function stripType(value) {
  const { type: _type, ...rest } = value;
  return rest;
}

function resolveModelId(requestedModel) {
  if (process.env.CC_OPENAI_MODEL) return process.env.CC_OPENAI_MODEL;
  const model = String(requestedModel || "").toLowerCase();
  if (model.includes("haiku")) {
    return process.env.CC_OPENAI_HAIKU_MODEL || DEFAULT_HAIKU_MODEL;
  }
  if (model.includes("opus")) {
    return process.env.CC_OPENAI_OPUS_MODEL || process.env.CC_OPENAI_DEFAULT_MODEL || DEFAULT_MODEL;
  }
  if (model.includes("sonnet")) {
    return (
      process.env.CC_OPENAI_SONNET_MODEL || process.env.CC_OPENAI_DEFAULT_MODEL || DEFAULT_MODEL
    );
  }
  return process.env.CC_OPENAI_DEFAULT_MODEL || requestedModel || DEFAULT_MODEL;
}

function thinkingToReasoning(thinking) {
  const forced = process.env.CC_OPENAI_REASONING;
  if (forced) return forced === "none" ? "off" : forced;
  if (!thinking || typeof thinking !== "object") return undefined;
  if (thinking.type === "disabled") return "off";
  if (thinking.type !== "enabled" && thinking.type !== "adaptive") return undefined;

  if (typeof thinking.effort === "string") {
    return thinking.effort === "none" ? "off" : thinking.effort;
  }

  const budget = Number(thinking.budget_tokens || 0);
  if (budget <= 0) return "low";
  if (budget <= 1024) return "low";
  if (budget <= 8192) return "medium";
  if (budget <= 32768) return "high";
  return "xhigh";
}

function normalizeSystemPrompt(system) {
  if (!system) return undefined;
  if (typeof system === "string") return system;
  if (!Array.isArray(system)) return JSON.stringify(system);
  return system
    .filter((block) => block?.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n\n");
}

function anthropicToContext(request) {
  const toolNames = new Map();
  const messages = [];
  let timestamp = Date.now();

  for (const message of request.messages || []) {
    if (message?.role === "assistant") {
      const assistant = anthropicAssistantToPi(message, request.model, toolNames, timestamp++);
      if (assistant.content.length > 0) messages.push(assistant);
    } else if (message?.role === "user") {
      pushUserMessage(messages, message.content, toolNames, timestamp);
      timestamp += 1;
    }
  }

  const context = {
    systemPrompt: normalizeSystemPrompt(request.system),
    messages,
  };
  const tools = anthropicToolsToPi(request.tools);
  if (tools.length > 0) context.tools = tools;
  return context;
}

function pushUserMessage(messages, content, toolNames, timestamp) {
  if (typeof content === "string") {
    messages.push({ role: "user", content, timestamp });
    return;
  }
  if (!Array.isArray(content)) {
    messages.push({ role: "user", content: stringifyUnknown(content), timestamp });
    return;
  }

  let batch = [];
  const flushBatch = () => {
    if (batch.length === 0) return;
    messages.push({ role: "user", content: collapseUserContent(batch), timestamp: timestamp++ });
    batch = [];
  };

  for (const block of content) {
    if (block?.type === "tool_result") {
      flushBatch();
      const toolCallId = String(block.tool_use_id || "");
      messages.push({
        role: "toolResult",
        toolCallId,
        toolName: toolNames.get(toolCallId) || "tool",
        content: anthropicToolResultContentToPi(block.content),
        isError: Boolean(block.is_error),
        timestamp: timestamp++,
      });
    } else {
      const converted = anthropicInputBlockToPi(block);
      if (converted) batch.push(converted);
    }
  }
  flushBatch();
}

function collapseUserContent(blocks) {
  if (blocks.every((block) => block.type === "text")) {
    return blocks.map((block) => block.text).join("\n");
  }
  return blocks;
}

function anthropicInputBlockToPi(block) {
  if (!block) return undefined;
  if (block.type === "text") {
    return { type: "text", text: String(block.text || "") };
  }
  if (block.type === "image" && block.source) {
    if (block.source.type === "base64") {
      return {
        type: "image",
        data: String(block.source.data || ""),
        mimeType: String(block.source.media_type || "image/png"),
      };
    }
    if (block.source.url) {
      return { type: "text", text: `[image: ${block.source.url}]` };
    }
  }
  return { type: "text", text: stringifyUnknown(block) };
}

function anthropicToolResultContentToPi(content) {
  if (typeof content === "string") return [{ type: "text", text: content }];
  if (!Array.isArray(content)) return [{ type: "text", text: stringifyUnknown(content) }];
  const blocks = content.map(anthropicInputBlockToPi).filter(Boolean);
  return blocks.length > 0 ? blocks : [{ type: "text", text: "" }];
}

function anthropicAssistantToPi(message, requestModel, toolNames, timestamp) {
  const content = [];
  const blocks =
    typeof message.content === "string"
      ? [{ type: "text", text: message.content }]
      : message.content || [];

  for (const block of blocks) {
    if (block?.type === "text") {
      content.push({ type: "text", text: String(block.text || "") });
    } else if (block?.type === "thinking") {
      content.push({
        type: "thinking",
        thinking: String(block.thinking || ""),
        ...(block.signature ? { thinkingSignature: String(block.signature) } : {}),
      });
    } else if (block?.type === "redacted_thinking") {
      content.push({
        type: "thinking",
        thinking: "[Reasoning redacted]",
        thinkingSignature: String(block.data || ""),
        redacted: true,
      });
    } else if (block?.type === "tool_use") {
      const id = String(block.id || `toolu_${randomUUID().replaceAll("-", "")}`);
      const name = String(block.name || "tool");
      toolNames.set(id, name);
      content.push({
        type: "toolCall",
        id,
        name,
        arguments: isPlainObject(block.input) ? block.input : {},
      });
    }
  }

  return {
    role: "assistant",
    content,
    api: "anthropic-messages",
    provider: "anthropic",
    model: String(requestModel || "unknown"),
    usage: emptyUsage(),
    stopReason: "stop",
    timestamp,
  };
}

function anthropicToolsToPi(tools) {
  if (!Array.isArray(tools)) return [];
  return tools
    .filter((tool) => tool?.name)
    .map((tool) => ({
      name: String(tool.name),
      description: String(tool.description || ""),
      parameters: tool.input_schema || { type: "object", properties: {} },
    }));
}

function emptyUsage() {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function piContentToAnthropic(content) {
  return (content || []).map((block) => {
    if (block.type === "text") {
      return { type: "text", text: block.text || "" };
    }
    if (block.type === "thinking") {
      if (block.redacted) {
        return { type: "redacted_thinking", data: block.thinkingSignature || block.thinking || "" };
      }
      return {
        type: "thinking",
        thinking: block.thinking || "",
        ...(block.thinkingSignature ? { signature: block.thinkingSignature } : {}),
      };
    }
    return {
      type: "tool_use",
      id: block.id,
      name: block.name,
      input: isPlainObject(block.arguments) ? block.arguments : {},
    };
  });
}

function mapStopReason(reason) {
  if (reason === "length") return "max_tokens";
  if (reason === "toolUse") return "tool_use";
  if (reason === "aborted") return "stop_sequence";
  return "end_turn";
}

function anthropicUsage(usage = emptyUsage()) {
  return {
    input_tokens: usage.input || 0,
    output_tokens: usage.output || 0,
    cache_creation_input_tokens: usage.cacheWrite || 0,
    cache_read_input_tokens: usage.cacheRead || 0,
  };
}

function piMessageToAnthropic(message, fallbackModel) {
  return {
    id: message.responseId || `msg_${randomUUID().replaceAll("-", "")}`,
    type: "message",
    role: "assistant",
    model: message.responseModel || message.model || fallbackModel,
    content: piContentToAnthropic(message.content),
    stop_reason: mapStopReason(message.stopReason),
    stop_sequence: null,
    usage: anthropicUsage(message.usage),
  };
}

async function readJsonBody(req) {
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.byteLength;
    if (total > MAX_BODY_BYTES) throw httpError(413, "request body too large");
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  try {
    return text ? JSON.parse(text) : {};
  } catch (error) {
    throw httpError(400, `invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function httpError(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function sendJson(res, status, body) {
  res.writeHead(status, {
    "content-type": "application/json",
    "cache-control": "no-store",
  });
  res.end(`${JSON.stringify(body)}\n`);
}

function errorType(status) {
  if (status >= 500) return "api_error";
  if (status === 401 || status === 403) return "authentication_error";
  return "invalid_request_error";
}

function sendError(res, error) {
  const status = error?.status || 500;
  sendJson(res, status, {
    type: "error",
    error: {
      type: errorType(status),
      message: error instanceof Error ? error.message : String(error),
    },
  });
}

function writeSse(res, event, data) {
  res.write(`event: ${event}\n`);
  res.write(`data: ${JSON.stringify(data)}\n\n`);
}

function contentBlockFromPartial(event) {
  return event.partial?.content?.[event.contentIndex];
}

async function streamAnthropicResponse(res, piStream, modelId) {
  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  });

  const messageId = `msg_${randomUUID().replaceAll("-", "")}`;
  const openBlocks = new Set();
  const toolDeltaSeen = new Set();
  let messageStarted = false;

  const ensureMessageStart = (partial) => {
    if (messageStarted) return;
    messageStarted = true;
    writeSse(res, "message_start", {
      type: "message_start",
      message: {
        id: partial?.responseId || messageId,
        type: "message",
        role: "assistant",
        content: [],
        model: partial?.responseModel || partial?.model || modelId,
        stop_reason: null,
        stop_sequence: null,
        usage: anthropicUsage(partial?.usage),
      },
    });
  };

  const closeBlock = (index) => {
    if (!openBlocks.has(index)) return;
    writeSse(res, "content_block_stop", { type: "content_block_stop", index });
    openBlocks.delete(index);
  };

  for await (const event of piStream) {
    if (event.type === "start") {
      ensureMessageStart(event.partial);
    } else if (event.type === "text_start") {
      ensureMessageStart(event.partial);
      openBlocks.add(event.contentIndex);
      writeSse(res, "content_block_start", {
        type: "content_block_start",
        index: event.contentIndex,
        content_block: { type: "text", text: "" },
      });
    } else if (event.type === "text_delta") {
      ensureMessageStart(event.partial);
      writeSse(res, "content_block_delta", {
        type: "content_block_delta",
        index: event.contentIndex,
        delta: { type: "text_delta", text: event.delta },
      });
    } else if (event.type === "text_end") {
      closeBlock(event.contentIndex);
    } else if (event.type === "thinking_start") {
      ensureMessageStart(event.partial);
      openBlocks.add(event.contentIndex);
      writeSse(res, "content_block_start", {
        type: "content_block_start",
        index: event.contentIndex,
        content_block: { type: "thinking", thinking: "" },
      });
    } else if (event.type === "thinking_delta") {
      ensureMessageStart(event.partial);
      writeSse(res, "content_block_delta", {
        type: "content_block_delta",
        index: event.contentIndex,
        delta: { type: "thinking_delta", thinking: event.delta },
      });
    } else if (event.type === "thinking_end") {
      const block = contentBlockFromPartial(event);
      if (block?.type === "thinking" && block.thinkingSignature && !block.redacted) {
        writeSse(res, "content_block_delta", {
          type: "content_block_delta",
          index: event.contentIndex,
          delta: { type: "signature_delta", signature: block.thinkingSignature },
        });
      }
      closeBlock(event.contentIndex);
    } else if (event.type === "toolcall_start") {
      ensureMessageStart(event.partial);
      const block = contentBlockFromPartial(event) || {};
      openBlocks.add(event.contentIndex);
      writeSse(res, "content_block_start", {
        type: "content_block_start",
        index: event.contentIndex,
        content_block: {
          type: "tool_use",
          id: block.id || `toolu_${randomUUID().replaceAll("-", "")}`,
          name: block.name || "tool",
          input: {},
        },
      });
    } else if (event.type === "toolcall_delta") {
      ensureMessageStart(event.partial);
      toolDeltaSeen.add(event.contentIndex);
      writeSse(res, "content_block_delta", {
        type: "content_block_delta",
        index: event.contentIndex,
        delta: { type: "input_json_delta", partial_json: event.delta },
      });
    } else if (event.type === "toolcall_end") {
      if (!toolDeltaSeen.has(event.contentIndex)) {
        writeSse(res, "content_block_delta", {
          type: "content_block_delta",
          index: event.contentIndex,
          delta: {
            type: "input_json_delta",
            partial_json: JSON.stringify(event.toolCall?.arguments || {}),
          },
        });
      }
      closeBlock(event.contentIndex);
    } else if (event.type === "done") {
      ensureMessageStart(event.message);
      for (const index of [...openBlocks].sort((a, b) => a - b)) closeBlock(index);
      writeSse(res, "message_delta", {
        type: "message_delta",
        delta: { stop_reason: mapStopReason(event.message.stopReason), stop_sequence: null },
        usage: anthropicUsage(event.message.usage),
      });
      writeSse(res, "message_stop", { type: "message_stop" });
    } else if (event.type === "error") {
      writeSse(res, "error", {
        type: "error",
        error: {
          type: event.reason === "aborted" ? "request_aborted" : "api_error",
          message: event.error?.errorMessage || "upstream error",
        },
      });
    }
  }

  res.end();
}

function buildOptions(request, req, signal) {
  const reasoning = thinkingToReasoning(request.thinking);
  return {
    maxTokens: request.max_tokens,
    temperature: request.temperature,
    apiKey: undefined,
    signal,
    ...(reasoning ? { reasoning } : {}),
    transport: process.env.CC_OPENAI_TRANSPORT || "auto",
    cacheRetention: process.env.CC_OPENAI_CACHE_RETENTION || "short",
    sessionId:
      process.env.CC_OPENAI_SESSION_ID ||
      req.headers["x-claude-session-id"] ||
      req.headers["x-client-request-id"] ||
      processSessionId,
    timeoutMs: process.env.CC_OPENAI_TIMEOUT_MS
      ? Number.parseInt(process.env.CC_OPENAI_TIMEOUT_MS, 10)
      : undefined,
  };
}

function extractInboundBearer(req) {
  const auth = req.headers["authorization"];
  if (typeof auth === "string" && auth.startsWith("Bearer ")) return auth.slice(7).trim();
  const apiKey = req.headers["x-api-key"];
  if (typeof apiKey === "string") return apiKey.trim();
  return "";
}

// Fail-secure inbound auth. If CC_OPENAI_PROXY_BEARER is set, the caller must
// present a matching bearer (constant-time compare). If it is unset, requests
// are rejected unless CC_OPENAI_PROXY_ALLOW_ANON=1 -- the escape hatch for the
// loopback-only local `cc-openai` autostart. /health stays ungated (it is a
// separate GET branch that never reaches this).
function assertInboundAuth(req) {
  const expected = process.env.CC_OPENAI_PROXY_BEARER;
  if (!expected) {
    if (process.env.CC_OPENAI_PROXY_ALLOW_ANON === "1") return;
    throw httpError(
      401,
      "proxy auth not configured: set CC_OPENAI_PROXY_BEARER, or CC_OPENAI_PROXY_ALLOW_ANON=1 to allow anonymous",
    );
  }
  const got = Buffer.from(extractInboundBearer(req));
  const want = Buffer.from(expected);
  if (got.length !== want.length || !timingSafeEqual(got, want)) {
    throw httpError(401, "unauthorized");
  }
}

async function handleMessages(req, res) {
  assertInboundAuth(req);
  const body = await readJsonBody(req);
  const { getModel, streamSimple, completeSimple } = await loadPiAi();
  const modelId = resolveModelId(body.model);
  const model = getModel(DEFAULT_PROVIDER, modelId);
  if (!model) throw httpError(400, `unknown ${DEFAULT_PROVIDER} model: ${modelId}`);

  const controller = new AbortController();
  let complete = false;
  req.on("aborted", () => controller.abort(new Error("request aborted")));
  res.on("close", () => {
    if (!complete) controller.abort(new Error("client disconnected"));
  });

  const options = buildOptions(body, req, controller.signal);
  options.apiKey = await resolveOpenAICodexApiKey();
  const context = anthropicToContext(body);

  if (body.stream !== false) {
    await streamAnthropicResponse(res, streamSimple(model, context, options), modelId);
    complete = true;
    return;
  }

  const message = await completeSimple(model, context, options);
  complete = true;
  if (message.stopReason === "error") {
    throw httpError(502, message.errorMessage || "upstream error");
  }
  sendJson(res, 200, piMessageToAnthropic(message, modelId));
}

async function route(req, res) {
  const url = new URL(req.url || "/", `http://${req.headers.host || "localhost"}`);
  try {
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
      sendJson(res, 200, {
        ok: true,
        provider: DEFAULT_PROVIDER,
        defaultModel: process.env.CC_OPENAI_DEFAULT_MODEL || DEFAULT_MODEL,
      });
    } else if (
      req.method === "POST" &&
      (url.pathname === "/v1/messages" || url.pathname === "/messages")
    ) {
      await handleMessages(req, res);
    } else {
      throw httpError(404, `not found: ${req.method} ${url.pathname}`);
    }
  } catch (error) {
    if (!res.headersSent) {
      sendError(res, error);
    } else {
      writeSse(res, "error", {
        type: "error",
        error: {
          type: "api_error",
          message: error instanceof Error ? error.message : String(error),
        },
      });
      res.end();
    }
  }
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringifyUnknown(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export {
  anthropicToContext,
  anthropicToolsToPi,
  assertInboundAuth,
  errorType,
  extractInboundBearer,
  piContentToAnthropic,
  piMessageToAnthropic,
  resolveModelId,
  thinkingToReasoning,
};

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const config = parseArgs(process.argv.slice(2));
    const server = createServer((req, res) => {
      void route(req, res);
    });
    server.on("error", (error) => {
      process.stderr.write(
        `cc-openai-proxy: ${error instanceof Error ? error.message : String(error)}\n`,
      );
      process.exit(1);
    });
    server.listen(config.port, config.host, () => {
      process.stderr.write(`cc-openai-proxy listening on http://${config.host}:${config.port}\n`);
    });
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(2);
  }
}
