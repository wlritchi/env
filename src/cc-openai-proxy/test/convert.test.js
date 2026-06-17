import test from "node:test";
import assert from "node:assert/strict";

import {
  anthropicToContext,
  piContentToAnthropic,
  resolveModelId,
  thinkingToReasoning,
} from "../bin/cc-openai-proxy.js";

test("converts Anthropic messages, tools, and tool results to pi context", () => {
  const context = anthropicToContext({
    model: "claude-sonnet-4-5",
    system: [{ type: "text", text: "system" }],
    tools: [
      {
        name: "Read",
        description: "read a file",
        input_schema: { type: "object", properties: { file_path: { type: "string" } } },
      },
    ],
    messages: [
      { role: "user", content: "hello" },
      {
        role: "assistant",
        content: [{ type: "tool_use", id: "toolu_1", name: "Read", input: { file_path: "a.txt" } }],
      },
      {
        role: "user",
        content: [{ type: "tool_result", tool_use_id: "toolu_1", content: "contents" }],
      },
    ],
  });

  assert.equal(context.systemPrompt, "system");
  assert.equal(context.tools[0].name, "Read");
  assert.equal(context.messages[1].content[0].type, "toolCall");
  assert.equal(context.messages[2].role, "toolResult");
  assert.equal(context.messages[2].toolName, "Read");
});

test("maps Anthropic thinking budgets to pi reasoning levels", () => {
  assert.equal(thinkingToReasoning({ type: "disabled" }), "off");
  assert.equal(thinkingToReasoning({ type: "enabled", budget_tokens: 512 }), "low");
  assert.equal(thinkingToReasoning({ type: "enabled", budget_tokens: 4096 }), "medium");
  assert.equal(thinkingToReasoning({ type: "enabled", budget_tokens: 20000 }), "high");
});

test("maps Anthropic family names to OpenAI Codex defaults", () => {
  assert.equal(resolveModelId("claude-3-5-haiku-latest"), "gpt-5.4-mini");
  assert.equal(resolveModelId("claude-sonnet-4-5"), "gpt-5.5");
  assert.equal(resolveModelId("gpt-5.4"), "gpt-5.4");
});

test("converts pi assistant blocks back to Anthropic content blocks", () => {
  const blocks = piContentToAnthropic([
    { type: "text", text: "answer" },
    { type: "toolCall", id: "toolu_1", name: "Bash", arguments: { command: "pwd" } },
  ]);

  assert.deepEqual(blocks, [
    { type: "text", text: "answer" },
    { type: "tool_use", id: "toolu_1", name: "Bash", input: { command: "pwd" } },
  ]);
});
