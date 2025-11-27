// Minimal Node server to serve static assets and proxy quotes to avoid CORS.
// Usage: node server.js
// PORT env controls listen port (default 8000).

const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = process.env.PORT ? Number(process.env.PORT) : 8000;
const ROOT = path.resolve(__dirname);
const WEB_ROOT = path.join(ROOT, "web");

const MIME = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".txt": "text/plain",
};

function send(res, status, body, headers = {}) {
  res.writeHead(status, headers);
  res.end(body);
}

async function proxyQuote(res) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const upstream = "https://zenquotes.io/api/random";
    const resp = await fetch(upstream, { signal: controller.signal });
    clearTimeout(timer);
    if (!resp.ok) throw new Error(`quote upstream ${resp.status}`);
    const data = await resp.text();
    send(res, 200, data, {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-cache",
    });
  } catch (err) {
    clearTimeout(timer);
    const payload = JSON.stringify({ error: "quote_unavailable" });
    send(res, 502, payload, {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    });
  }
}

function resolveFile(urlPath) {
  // Redirect root to /web/
  if (urlPath === "/" || urlPath === "") {
    return path.join(ROOT, "index.html");
  }
  // Normalize and prevent path traversal
  const safePath = path.normalize(urlPath.replace(/^\/+/, ""));
  const candidate = path.join(ROOT, safePath);
  if (candidate.startsWith(ROOT)) {
    return candidate;
  }
  return null;
}

const server = http.createServer(async (req, res) => {
  if (req.url.startsWith("/quote")) {
    return proxyQuote(res);
  }

  const filePath = resolveFile(new URL(req.url, `http://localhost:${PORT}`).pathname);
  if (!filePath) {
    return send(res, 403, "Forbidden");
  }

  fs.stat(filePath, (err, stats) => {
    if (err) {
      return send(res, 404, "Not Found");
    }
    let finalPath = filePath;
    if (stats.isDirectory()) {
      finalPath = path.join(filePath, "index.html");
    }
    fs.readFile(finalPath, (readErr, data) => {
      if (readErr) {
        return send(res, 404, "Not Found");
      }
      const ext = path.extname(finalPath);
      const type = MIME[ext] || "application/octet-stream";
      const cache = ext === ".html" ? "no-cache" : "public, max-age=300";
      send(res, 200, data, { "Content-Type": type, "Cache-Control": cache });
    });
  });
});

server.listen(PORT, () => {
  console.log(`Serving ${ROOT} on http://localhost:${PORT} (proxy /quote)`);
});
