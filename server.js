// Minimal Node server to serve static assets and proxy quotes to avoid CORS.
// Usage: node server.js
// PORT env controls listen port (default 8000).

const http = require("http");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const PORT = process.env.PORT ? Number(process.env.PORT) : 8000;
const ROOT = path.resolve(__dirname);
const WEB_ROOT = path.join(ROOT, "web");
const VENV_PY = path.join(ROOT, ".venv", "bin", "python3");
const API_KEY = process.env.API_KEY || "";
const RAW_CACHE = path.join(ROOT, "data", "activities_raw.json");

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

function log(message) {
  const ts = new Date().toISOString();
  console.log(`[${ts}] ${message}`);
}

let refreshInFlight = false;

function unauthorized(res) {
  send(res, 401, JSON.stringify({ error: "unauthorized" }), {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
  });
}

function ensureApiKey(req, res) {
  if (!API_KEY) {
    return true;
  }
  const headerKey = req.headers["x-api-key"];
  const urlKey = new URL(req.url, `http://localhost:${PORT}`).searchParams.get("api_key");
  const supplied = Array.isArray(headerKey) ? headerKey[0] : headerKey;
  if (supplied === API_KEY || urlKey === API_KEY) {
    return true;
  }
  unauthorized(res);
  return false;
}

function readCachedActivities() {
  if (!fs.existsSync(RAW_CACHE)) {
    return [];
  }
  try {
    const raw = fs.readFileSync(RAW_CACHE, "utf8");
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    return [];
  }
}

function filterActivitiesByDate(activities, date) {
  if (!date) return [];
  return activities.filter((act) => (act.start_date || "").startsWith(date));
}

function latestActivity(activities) {
  let latest = null;
  activities.forEach((act) => {
    const ts = Date.parse(act.start_date || "");
    if (isNaN(ts)) return;
    if (!latest || ts > Date.parse(latest.start_date || "")) {
      latest = act;
    }
  });
  return latest;
}

function runFetcher() {
  const pythonCmd = process.env.FETCH_PYTHON || (fs.existsSync(VENV_PY) ? VENV_PY : "python3");
  return new Promise((resolve, reject) => {
    const proc = spawn(pythonCmd, ["fetch_strava.py"], {
      cwd: ROOT,
      env: process.env,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => {
      stdout += d.toString();
    });
    proc.stderr.on("data", (d) => {
      stderr += d.toString();
    });
    proc.on("error", reject);
    proc.on("close", (code) => {
      if (code === 0) {
        resolve(stdout.trim());
      } else {
        reject(new Error(`fetch_strava.py exited with ${code}: ${stderr || stdout}`));
      }
    });
  });
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
  if (req.url === "/refresh") {
    if (req.method !== "POST") {
      return send(res, 405, "Method Not Allowed", { "Content-Type": "text/plain" });
    }
    if (refreshInFlight) {
      return send(
        res,
        429,
        JSON.stringify({ error: "refresh_in_progress" }),
        { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      );
    }
    refreshInFlight = true;
    log("Refresh request received");
    try {
      const output = await runFetcher();
      log("Refresh completed successfully");
      send(
        res,
        200,
        JSON.stringify({ status: "ok", output }),
        { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      );
    } catch (err) {
      log(`Refresh failed: ${err.message || err}`);
      send(
        res,
        500,
        JSON.stringify({ error: err.message || "fetch_failed" }),
        { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      );
    } finally {
      refreshInFlight = false;
    }
    return;
  }

  if (req.url.startsWith("/quote")) {
    return proxyQuote(res);
  }

  if (req.url.startsWith("/api/")) {
    if (!ensureApiKey(req, res)) {
      return;
    }
    const url = new URL(req.url, `http://localhost:${PORT}`);
    const pathName = url.pathname;
    const activities = readCachedActivities();
    if (pathName === "/api/latest") {
      const latest = latestActivity(activities);
      return send(res, 200, JSON.stringify({ latest }), {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache",
      });
    }
    if (pathName === "/api/date") {
      const date = url.searchParams.get("date") || "";
      if (!date) {
        return send(res, 400, JSON.stringify({ error: "missing_date" }), {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        });
      }
      const matches = filterActivitiesByDate(activities, date);
      return send(res, 200, JSON.stringify({ date, activities: matches }), {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache",
      });
    }
    return send(res, 404, JSON.stringify({ error: "not_found" }), {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    });
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
