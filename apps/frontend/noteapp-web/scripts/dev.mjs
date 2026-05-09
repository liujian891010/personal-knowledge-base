import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, join, normalize, resolve } from "node:path";
import { createServer } from "node:http";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildExecuteActionArgs,
  buildSnapshotCommandArgs,
  listMissingBridgeSettings,
  resolveBridgeConfig,
  runDesktopCliJson,
} from "./desktop-cli-bridge.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const host = process.env.HOST || "127.0.0.1";
const port = Number(process.env.PORT || "4173");
const bridgeConfig = resolveBridgeConfig();

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

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
        rejectBody(new Error("request body too large"));
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
        rejectBody(error);
      }
    });
    request.on("error", rejectBody);
  });
}

function buildBridgeStatusPayload() {
  const missing = listMissingBridgeSettings(bridgeConfig);
  return {
    available: missing.length === 0,
    missing,
    config: {
      vaultRoot: bridgeConfig.vaultRoot,
      baseUrl: bridgeConfig.baseUrl,
      vaultId: bridgeConfig.vaultId,
      deviceId: bridgeConfig.deviceId,
      outputJson: bridgeConfig.outputJson,
      activityLimit: bridgeConfig.activityLimit,
    },
  };
}

function requireBridgeConfig() {
  const status = buildBridgeStatusPayload();
  if (!status.available) {
    throw new Error(`desktop bridge is not configured: ${status.missing.join(", ")}`);
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
  const execution = runDesktopCliJson(buildExecuteActionArgs(actionId, nowMs), {
    config: bridgeConfig,
  });
  const snapshot = runDesktopCliJson(
    buildSnapshotCommandArgs(bridgeConfig, {
      nowMs,
      activityLimit,
    }),
    {
      config: bridgeConfig,
    },
  );
  return { execution, snapshot };
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://${host}:${port}`);

  if (request.method === "GET" && url.pathname === "/api/bridge/status") {
    writeJson(response, 200, buildBridgeStatusPayload());
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/bridge/refresh-snapshot") {
    try {
      const body = await readJsonBody(request);
      const snapshot = executeRefreshSnapshot(body.nowMs, body.activityLimit);
      writeJson(response, 200, { snapshot });
    } catch (error) {
      writeJson(response, 400, {
        error: error instanceof Error ? error.message : String(error),
      });
    }
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/bridge/execute-action") {
    try {
      const body = await readJsonBody(request);
      if (typeof body.actionId !== "string" || !body.actionId) {
        throw new Error("actionId is required");
      }
      const result = executeSyncAction(body.actionId, body.nowMs, body.activityLimit);
      writeJson(response, 200, result);
    } catch (error) {
      writeJson(response, 400, {
        error: error instanceof Error ? error.message : String(error),
      });
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
    console.log(`desktop bridge ready for vault ${status.config.vaultId} at ${status.config.vaultRoot}`);
  } else {
    console.log(`desktop bridge disabled, missing: ${status.missing.join(", ")}`);
  }
});
