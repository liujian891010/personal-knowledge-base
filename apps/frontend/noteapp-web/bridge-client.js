function normalizeBridgeErrorPayload(error, fallbackCode = "bridge_client_error") {
  if (!error) {
    return {
      code: fallbackCode,
      message: "桥接请求发生了未预期错误。",
      details: null,
    };
  }

  if (error instanceof Error) {
    return {
      code: fallbackCode,
      message: error.message,
      details: null,
    };
  }

  if (typeof error === "object") {
    return {
      code: typeof error.code === "string" ? error.code : fallbackCode,
      message:
        typeof error.message === "string" ? error.message : "桥接请求发生了未预期错误。",
      details: "details" in error ? error.details : null,
    };
  }

  return {
    code: fallbackCode,
    message: String(error),
    details: null,
  };
}

async function readJsonResponse(response, fallbackCode) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    throw normalizeBridgeErrorPayload(
      payload?.error || {
        code: fallbackCode,
        message: `桥接请求失败，HTTP 状态码 ${response.status}。`,
        details: {
          status: response.status,
        },
      },
      fallbackCode,
    );
  }

  if (!payload) {
    throw normalizeBridgeErrorPayload({
      code: "bridge_empty_response",
      message: "桥接服务返回了空响应体。",
      details: {
        status: response.status,
      },
    });
  }

  return payload;
}

export async function fetchBridgeStatus() {
  const response = await fetch("/api/bridge/status", {
    cache: "no-store",
  });
  return readJsonResponse(response, "bridge_status_failed");
}

export async function refreshBridgeSnapshot(payload = {}) {
  const response = await fetch("/api/bridge/refresh-snapshot", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return readJsonResponse(response, "bridge_refresh_failed");
}

export async function executeBridgeAction(actionId, payload = {}) {
  const response = await fetch("/api/bridge/execute-action", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify({
      ...payload,
      actionId,
    }),
  });
  return readJsonResponse(response, "bridge_execute_failed");
}

export async function fetchSampleAppSession() {
  const response = await fetch("/api/app-session/sample", {
    cache: "no-store",
  });
  return readJsonResponse(response, "app_session_sample_failed");
}

export async function refreshAppSession(payload = {}) {
  const response = await fetch("/api/app-session/refresh", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return readJsonResponse(response, "app_session_refresh_failed");
}

export async function requestAiCopilotAnswer(payload = {}) {
  const response = await fetch("/api/ai/copilot-answer", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return readJsonResponse(response, "ai_copilot_answer_failed");
}

export async function requestAiWikiCompile(payload = {}) {
  const response = await fetch("/api/ai/compile-wiki", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return readJsonResponse(response, "ai_wiki_compile_failed");
}

export function startBridgeStatusPolling({
  intervalMs = 15000,
  onStatus,
  onError,
} = {}) {
  let isActive = true;
  let inFlight = false;
  let timerId = null;

  async function pollOnce() {
    if (!isActive || inFlight) {
      return;
    }
    inFlight = true;
    try {
      const status = await fetchBridgeStatus();
      onStatus?.(status);
    } catch (error) {
      onError?.(normalizeBridgeErrorPayload(error, "bridge_status_failed"));
    } finally {
      inFlight = false;
    }
  }

  function handleVisibilityChange() {
    if (document.visibilityState === "visible") {
      pollOnce();
    }
  }

  timerId = window.setInterval(pollOnce, intervalMs);
  document.addEventListener("visibilitychange", handleVisibilityChange);
  pollOnce();

  return () => {
    isActive = false;
    if (timerId !== null) {
      window.clearInterval(timerId);
    }
    document.removeEventListener("visibilitychange", handleVisibilityChange);
  };
}
