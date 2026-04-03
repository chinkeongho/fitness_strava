/** @type {import('@playwright/test').PlaywrightTestConfig} */
module.exports = {
  testDir: "./tests/e2e",
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:4173",
    headless: true,
    viewport: { width: 1440, height: 1100 },
  },
  webServer: {
    command: "API_KEY=secret PORT=4173 node server.js",
    url: "http://127.0.0.1:4173/web/",
    reuseExistingServer: true,
    timeout: 60_000,
  },
};
