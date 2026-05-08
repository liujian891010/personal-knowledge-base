import { createReadStream, existsSync, statSync } from "node:fs";
import { extname, join, normalize, resolve } from "node:path";
import { createServer } from "node:http";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const host = process.env.HOST || "127.0.0.1";
const port = Number(process.env.PORT || "4173");

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

function resolvePath(urlPath) {
  const pathname = new URL(urlPath || "/", `http://${host}:${port}`).pathname;
  const normalized = normalize(pathname === "/" ? "index.html" : pathname.replace(/^[/\\]+/, ""));
  if (normalized.startsWith("..")) {
    return null;
  }
  return join(root, normalized);
}

const server = createServer((request, response) => {
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
  console.log(`noteapp-web static shell running at http://${host}:${port}`);
});
