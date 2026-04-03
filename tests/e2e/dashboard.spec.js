const { test, expect } = require("@playwright/test");

test.describe("dashboard and route boundaries", () => {
  test("shows navigation, run history, and protected api boundaries", async ({ page, request }) => {
    const pageErrors = [];
    page.on("pageerror", (err) => pageErrors.push(String(err)));

    await page.goto("/web/", { waitUntil: "networkidle" });
    await expect(page.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Analysis" })).toBeVisible();
    await expect(page.locator("#runs-list .run-item").first()).toBeVisible();

    await page.locator("#runs-list .run-item").first().click();
    await expect(page.locator("#hr-detail-title")).not.toHaveText("Select a run");

    const streamRes = await request.get("/activity-stream?id=17571564557");
    expect(streamRes.status()).toBe(200);

    const apiRes = await request.get("/api/latest");
    expect(apiRes.status()).toBe(401);
    expect(pageErrors).toEqual([]);
  });
});
