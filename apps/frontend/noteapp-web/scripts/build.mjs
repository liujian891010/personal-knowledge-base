import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const dist = join(root, "dist");

if (existsSync(dist)) {
  rmSync(dist, { recursive: true, force: true });
}

mkdirSync(dist, { recursive: true });

for (const entry of [
  "index.html",
  "styles.css",
  "app.js",
  "bridge-client.js",
  "fixtures",
  "bridge.local.example.json",
]) {
  cpSync(join(root, entry), join(dist, entry), { recursive: true });
}

console.log(`Built noteapp-web static shell to ${dist}`);
