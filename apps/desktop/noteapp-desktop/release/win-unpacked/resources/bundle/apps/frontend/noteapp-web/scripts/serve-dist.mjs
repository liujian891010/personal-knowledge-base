import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, resolve } from 'node:path';

const root = resolve('dist');
const host = process.env.HOST || '127.0.0.1';
const port = Number(process.env.PORT || 3000);

const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

function resolveRequestPath(requestUrl) {
  const url = new URL(requestUrl || '/', `http://${host}:${port}`);
  const requestedPath = decodeURIComponent(url.pathname);
  const target = resolve(root, `.${requestedPath}`);
  if (!target.startsWith(root)) {
    return null;
  }
  if (!existsSync(target) || statSync(target).isDirectory()) {
    return join(root, 'index.html');
  }
  return target;
}

const server = createServer((request, response) => {
  const target = resolveRequestPath(request.url);
  if (!target) {
    response.writeHead(403, { 'content-type': 'text/plain; charset=utf-8' });
    response.end('Forbidden');
    return;
  }

  response.writeHead(200, {
    'cache-control': 'no-store',
    'content-type': contentTypes[extname(target)] || 'application/octet-stream',
  });
  createReadStream(target).pipe(response);
});

server.listen(port, host, () => {
  console.log(`noteapp-web dist server running at http://${host}:${port}/`);
});
