import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { extname, join, normalize, resolve } from "node:path";
import { createServer } from "node:http";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildExecuteActionAndSnapshotArgs,
  buildSnapshotCommandArgs,
  listMissingBridgeSettings,
  printBridgeUsage,
  readArgMap,
  resolveBridgeConfig,
  runDesktopCliJson,
} from "./desktop-cli-bridge.mjs";
import {
  buildAiCopilotAnswerFromPayload,
  buildAiWikiCompileFromPayload,
} from "../ai-boundary.js";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const host = process.env.HOST || "127.0.0.1";
const port = Number(process.env.PORT || "4173");
const args = readArgMap(process.argv.slice(2));
if (args.has("--help")) {
  printBridgeUsage();
  process.exit(0);
}

const bridgeConfig = resolveBridgeConfig({ args });
const sampleSyncSnapshotPath = join(root, "fixtures", "sync-shell-snapshot.sample.json");
const sampleWorkspaceShellPath = join(root, "fixtures", "workspace-shell.sample.json");

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

function createBridgeError(code, message, details = {}, statusCode = 400) {
  const error = new Error(message);
  error.code = code;
  error.details = details;
  error.statusCode = statusCode;
  return error;
}

function writeJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  response.end(JSON.stringify(payload, null, 2));
}

function resolvePath(urlPath) {
  const pathname = new URL(urlPath || "/", `http://${host}:${port}`).pathname;
  const normalized = normalize(pathname === "/" ? "index.html" : pathname.replace(/^[/\\]+/, ""));
  if (normalized.startsWith("..")) {
    return null;
  }
  return join(root, normalized);
}

function readJsonBody(request) {
  return new Promise((resolveBody, rejectBody) => {
    let raw = "";
    request.setEncoding("utf-8");
    request.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 1024 * 1024) {
        rejectBody(
          createBridgeError("request_body_too_large", "Request body exceeded 1MB.", {}, 413),
        );
      }
    });
    request.on("end", () => {
      if (!raw.trim()) {
        resolveBody({});
        return;
      }
      try {
        resolveBody(JSON.parse(raw));
      } catch (error) {
        rejectBody(
          createBridgeError(
            "invalid_json_body",
            "Request body must be valid JSON.",
            {
              reason: error instanceof Error ? error.message : String(error),
            },
            400,
          ),
        );
      }
    });
    request.on("error", (error) => {
      rejectBody(
        createBridgeError(
          "request_stream_failed",
          "Request body stream failed.",
          {
            reason: error instanceof Error ? error.message : String(error),
          },
          400,
        ),
      );
    });
  });
}

function buildBridgeStatusPayload() {
  const missing = listMissingBridgeSettings(bridgeConfig);
  return {
    mode: bridgeConfig.bridgeMode,
    available: missing.length === 0,
    missing,
    config: {
      vaultRoot: bridgeConfig.vaultRoot,
      baseUrl: bridgeConfig.baseUrl,
      vaultId: bridgeConfig.vaultId,
      deviceId: bridgeConfig.deviceId,
      outputJson: bridgeConfig.outputJson,
      activityLimit: bridgeConfig.activityLimit,
      configSource: bridgeConfig.configSource,
      sourceByField: bridgeConfig.sourceByField,
    },
    diagnostics: bridgeConfig.diagnostics,
  };
}

function readJsonFile(path) {
  return JSON.parse(readFileSync(path, "utf-8"));
}

function detectSyncPayloadKind(payload) {
  if (
    payload &&
    typeof payload === "object" &&
    payload.sync_center &&
    payload.activity_feed &&
    typeof payload.generated_at_ms === "number"
  ) {
    return "sync-shell-snapshot";
  }
  if (
    payload &&
    typeof payload === "object" &&
    Array.isArray(payload.cards) &&
    payload.panel &&
    payload.summary
  ) {
    return "sync-center";
  }
  if (
    payload &&
    typeof payload === "object" &&
    Array.isArray(payload.records) &&
    typeof payload.total_count === "number"
  ) {
    return "sync-activity";
  }
  return "unknown";
}

function summarizeWorkspaceShell(workspaceShell) {
  const sections = Array.isArray(workspaceShell?.sections) ? workspaceShell.sections : [];
  const notes = workspaceShell?.notes && typeof workspaceShell.notes === "object" ? workspaceShell.notes : {};
  return {
    sectionCount: sections.length,
    noteCount: Object.keys(notes).length,
  };
}

function buildAppSessionPayload({ source, bridgeStatus, syncPayload, workspaceShell }) {
  const loadedAtMs = Date.now();
  return {
    source,
    loadedAtMs,
    bridgeStatus,
    aiBoundaryStatus: buildAiBoundaryStatusPayload(),
    syncPayload,
    workspaceShell,
    meta: {
      sessionId: `${source}-${loadedAtMs}`,
      payloadKind: detectSyncPayloadKind(syncPayload),
      workspace: summarizeWorkspaceShell(workspaceShell),
    },
  };
}

function buildAiBoundaryStatusPayload() {
  return {
    available: true,
    mode: "dev-server-local",
    checkedAtMs: Date.now(),
    capabilities: ["copilot-answer", "compile-wiki"],
  };
}

function buildSampleAppSession() {
  return buildAppSessionPayload({
    source: "sample",
    bridgeStatus: buildBridgeStatusPayload(),
    syncPayload: readJsonFile(sampleSyncSnapshotPath),
    workspaceShell: readJsonFile(sampleWorkspaceShellPath),
  });
}

function requireBridgeConfig() {
  const status = buildBridgeStatusPayload();
  if (!status.available) {
    throw createBridgeError(
      "bridge_not_configured",
      `Desktop bridge is not configured: ${status.missing.join(", ")}`,
      {
        missing: status.missing,
        configSource: status.config.configSource,
      },
      400,
    );
  }
  return status;
}

function executeRefreshSnapshot(nowMs, activityLimit) {
  requireBridgeConfig();
  return runDesktopCliJson(
    buildSnapshotCommandArgs(bridgeConfig, {
      nowMs,
      activityLimit,
    }),
    {
      config: bridgeConfig,
    },
  );
}

function executeSyncAction(actionId, nowMs, activityLimit) {
  requireBridgeConfig();
  return runDesktopCliJson(
    buildExecuteActionAndSnapshotArgs(
      actionId,
      nowMs,
      activityLimit || bridgeConfig.activityLimit,
    ),
    {
      config: bridgeConfig,
    },
  );
}

function writeBridgeError(response, error) {
  const normalized = {
    code:
      error && typeof error === "object" && "code" in error && typeof error.code === "string"
        ? error.code
        : "bridge_request_failed",
    message:
      error instanceof Error ? error.message : typeof error === "string" ? error : "Bridge request failed.",
    details:
      error && typeof error === "object" && "details" in error && error.details
        ? error.details
        : null,
  };
  const statusCode =
    error && typeof error === "object" && "statusCode" in error && Number.isInteger(error.statusCode)
      ? error.statusCode
      : 400;
  writeJson(response, statusCode, { error: normalized });
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://${host}:${port}`);

  if (request.method === "GET" && url.pathname === "/api/bridge/status") {
    writeJson(response, 200, buildBridgeStatusPayload());
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/app-session/sample") {
    writeJson(response, 200, buildSampleAppSession());
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/ai/status") {
    writeJson(response, 200, buildAiBoundaryStatusPayload());
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/app-session/refresh") {
    try {
      const body = await readJsonBody(request);
      const snapshot = executeRefreshSnapshot(body.nowMs, body.activityLimit);
      writeJson(
        response,
        200,
        buildAppSessionPayload({
          source: "desktop-bridge",
          bridgeStatus: buildBridgeStatusPayload(),
          syncPayload: snapshot,
          workspaceShell: readJsonFile(sampleWorkspaceShellPath),
        }),
      );
    } catch (error) {
      writeBridgeError(response, error);
    }
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/bridge/refresh-snapshot") {
    try {
      const body = await readJsonBody(request);
      const snapshot = executeRefreshSnapshot(body.nowMs, body.activityLimit);
      writeJson(response, 200, { snapshot });
    } catch (error) {
      writeBridgeError(response, error);
    }
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/bridge/execute-action") {
    try {
      const body = await readJsonBody(request);
      if (typeof body.actionId !== "string" || !body.actionId) {
        throw createBridgeError("action_id_required", "actionId is required.", {}, 400);
      }
      const result = executeSyncAction(body.actionId, body.nowMs, body.activityLimit);
      writeJson(response, 200, result);
    } catch (error) {
      writeBridgeError(response, error);
    }
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/ai/copilot-answer") {
    try {
      const body = await readJsonBody(request);
      writeJson(response, 200, buildAiCopilotAnswerFromPayload(body));
    } catch (error) {
      writeBridgeError(response, error);
    }
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/ai/compile-wiki") {
    try {
      const body = await readJsonBody(request);
      writeJson(response, 200, buildAiWikiCompileFromPayload(body));
    } catch (error) {
      writeBridgeError(response, error);
    }
    return;
  }

  const target = resolvePath(request.url || "/");
  if (!target || !existsSync(target) || statSync(target).isDirectory()) {
    response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    response.end("Not found");
    return;
  }

  response.writeHead(200, {
    "content-type": contentTypes[extname(target)] || "application/octet-stream",
    "cache-control": "no-store",
  });
  createReadStream(target).pipe(response);
});

server.listen(port, host, () => {
  const status = buildBridgeStatusPayload();
  console.log(`noteapp-web static shell running at http://${host}:${port}`);
  if (status.available) {
    console.log(
      `desktop bridge ready for vault ${status.config.vaultId} at ${status.config.vaultRoot} (${status.config.configSource})`,
    );
  } else {
    console.log(`desktop bridge disabled, missing: ${status.missing.join(", ")}`);
  }
});
