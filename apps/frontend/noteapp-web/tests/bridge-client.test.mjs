import assert from "node:assert/strict";

import {
  executeBridgeAction,
  fetchAiBoundaryStatus,
  fetchSampleAppSession,
  fetchBridgeStatus,
  requestAiCopilotAnswer,
  requestAiWikiCompile,
  refreshAppSession,
  refreshBridgeSnapshot,
  startBridgeStatusPolling,
} from "../bridge-client.js";

const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
const originalDocument = globalThis.document;

function flushTasks() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function resetGlobals() {
  globalThis.fetch = originalFetch;
  globalThis.window = originalWindow;
  globalThis.document = originalDocument;
}

export async function runBridgeClientTests() {
  const completed = [];
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { available: true, mode: "desktop-cli-local" };
      },
    };
  };

  const status = await fetchBridgeStatus();

  assert.equal(status.available, true);
  assert.equal(calls[0].url, "/api/bridge/status");
  assert.deepEqual(calls[0].options, { cache: "no-store" });
  completed.push("fetchBridgeStatus requests the status endpoint without cache");
  resetGlobals();

  const aiStatusCalls = [];
  globalThis.fetch = async (url, options) => {
    aiStatusCalls.push({ url, options });
    return {
      ok: true,
      status: 200,
      async json() {
        return { available: true, mode: "dev-server-local" };
      },
    };
  };

  const aiStatus = await fetchAiBoundaryStatus();
  assert.equal(aiStatus.available, true);
  assert.equal(aiStatusCalls[0].url, "/api/ai/status");
  assert.deepEqual(aiStatusCalls[0].options, { cache: "no-store" });
  completed.push("fetchAiBoundaryStatus requests the AI boundary status endpoint without cache");
  resetGlobals();

  let request = null;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return {
      ok: true,
      status: 200,
      async json() {
        return { snapshot: { generated_at_ms: 1 } };
      },
    };
  };

  const response = await refreshBridgeSnapshot({ activityLimit: 9 });

  assert.equal(response.snapshot.generated_at_ms, 1);
  assert.equal(request.url, "/api/bridge/refresh-snapshot");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers["content-type"], "application/json");
  assert.equal(request.options.body, JSON.stringify({ activityLimit: 9 }));
  completed.push("refreshBridgeSnapshot posts JSON to refresh endpoint");
  resetGlobals();

  let sampleRequest = null;
  globalThis.fetch = async (url, options) => {
    sampleRequest = { url, options };
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          source: "sample",
          syncPayload: { generated_at_ms: 1 },
          workspaceShell: { sections: [], notes: {} },
        };
      },
    };
  };

  const sampleSession = await fetchSampleAppSession();
  assert.equal(sampleSession.source, "sample");
  assert.equal(sampleRequest.url, "/api/app-session/sample");
  assert.deepEqual(sampleRequest.options, { cache: "no-store" });
  completed.push("fetchSampleAppSession requests the sample session endpoint");
  resetGlobals();

  let refreshSessionRequest = null;
  globalThis.fetch = async (url, options) => {
    refreshSessionRequest = { url, options };
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          source: "desktop-bridge",
          loadedAtMs: 1,
          syncPayload: { generated_at_ms: 1 },
          workspaceShell: { sections: [], notes: {} },
        };
      },
    };
  };

  const refreshedSession = await refreshAppSession({ activityLimit: 5 });
  assert.equal(refreshedSession.source, "desktop-bridge");
  assert.equal(refreshSessionRequest.url, "/api/app-session/refresh");
  assert.equal(refreshSessionRequest.options.method, "POST");
  assert.equal(refreshSessionRequest.options.body, JSON.stringify({ activityLimit: 5 }));
  completed.push("refreshAppSession posts JSON to the app session refresh endpoint");
  resetGlobals();

  let aiAnswerRequest = null;
  globalThis.fetch = async (url, options) => {
    aiAnswerRequest = { url, options };
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          headline: "建议先固化结论",
          scopeLabel: "当前文档",
        };
      },
    };
  };

  const aiAnswer = await requestAiCopilotAnswer({ question: "下一步怎么做？" });
  assert.equal(aiAnswer.headline, "建议先固化结论");
  assert.equal(aiAnswerRequest.url, "/api/ai/copilot-answer");
  assert.equal(aiAnswerRequest.options.method, "POST");
  assert.equal(aiAnswerRequest.options.body, JSON.stringify({ question: "下一步怎么做？" }));
  completed.push("requestAiCopilotAnswer posts JSON to the AI copilot endpoint");
  resetGlobals();

  let aiCompileRequest = null;
  globalThis.fetch = async (url, options) => {
    aiCompileRequest = { url, options };
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          title: "同步桥接知识页",
          body: "# 同步桥接知识页",
        };
      },
    };
  };

  const aiCompile = await requestAiWikiCompile({ targetTitle: "同步桥接知识页" });
  assert.equal(aiCompile.title, "同步桥接知识页");
  assert.equal(aiCompileRequest.url, "/api/ai/compile-wiki");
  assert.equal(aiCompileRequest.options.method, "POST");
  assert.equal(aiCompileRequest.options.body, JSON.stringify({ targetTitle: "同步桥接知识页" }));
  completed.push("requestAiWikiCompile posts JSON to the AI wiki compile endpoint");
  resetGlobals();

  globalThis.fetch = async () => ({
    ok: false,
    status: 400,
    async json() {
      return {
        error: {
          code: "bridge_not_configured",
          message: "Desktop bridge is not configured.",
          details: {
            missing: ["--vault-root"],
          },
        },
      };
    },
  });

  await assert.rejects(
    () => executeBridgeAction("sync-now"),
    (error) =>
      error.code === "bridge_not_configured" &&
      error.details?.missing?.[0] === "--vault-root",
  );
  completed.push("executeBridgeAction surfaces structured bridge errors");
  resetGlobals();

  const statuses = [];
  const intervalCalls = [];
  let visibilityHandler = null;

  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    async json() {
      return {
        available: true,
        mode: "desktop-cli-local",
      };
    },
  });
  globalThis.window = {
    setInterval(handler, intervalMs) {
      intervalCalls.push(intervalMs);
      this.lastHandler = handler;
      return 7;
    },
    clearInterval(id) {
      intervalCalls.push(`clear:${id}`);
    },
  };
  globalThis.document = {
    visibilityState: "hidden",
    addEventListener(eventName, handler) {
      if (eventName === "visibilitychange") {
        visibilityHandler = handler;
      }
    },
    removeEventListener() {},
  };

  const stopPolling = startBridgeStatusPolling({
    intervalMs: 3210,
    onStatus(status) {
      statuses.push(status);
    },
  });

  await flushTasks();
  assert.equal(statuses.length, 1);
  assert.equal(intervalCalls[0], 3210);

  globalThis.document.visibilityState = "visible";
  visibilityHandler?.();
  await flushTasks();
  assert.equal(statuses.length, 2);

  stopPolling();
  assert.equal(intervalCalls[1], "clear:7");
  completed.push("startBridgeStatusPolling polls immediately and on visibility change");
  resetGlobals();

  return completed;
}
