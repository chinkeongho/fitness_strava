const { test, expect } = require("@playwright/test");

test.describe("analysis interactions", () => {
  test("supports hover explanations, drag zoom, clipping, and reset", async ({ page }) => {
    const pageErrors = [];
    page.on("pageerror", (err) => pageErrors.push(String(err)));

    await page.goto("/web/analysis.html", { waitUntil: "networkidle" });

    await expect(page.getByRole("heading", { name: "Fitness Trend" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Efficiency at HR" })).toBeVisible();

    const explainButtons = page.getByText("Explain");
    await explainButtons.first().hover();
    await expect(page.getByText(/Shows speed over time for each heart-rate zone/i)).toBeVisible();
    await explainButtons.nth(1).hover();
    await expect(page.getByText(/Shows speed per beat of heart rate/i)).toBeVisible();

    const start = page.locator("#zoom-start");
    const end = page.locator("#zoom-end");
    const fitness = page.locator("#fitness-zones-chart");
    const efficiency = page.locator("#efficiency-chart");

    const drag = async (locator, fromXPct, toXPct) => {
      const box = await locator.boundingBox();
      if (!box) throw new Error("missing chart box");
      await page.mouse.move(box.x + box.width * fromXPct, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * toXPct, box.y + box.height / 2, { steps: 12 });
      await page.mouse.up();
    };

    await drag(fitness, 0.10, 0.55);
    await expect(start).not.toHaveValue("");
    await expect(end).not.toHaveValue("");

    const box = await fitness.boundingBox();
    if (!box) throw new Error("missing fitness chart box");
    await page.mouse.move(box.x + box.width * 0.15, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x - 200, box.y + box.height / 2, { steps: 4 });
    await page.mouse.move(box.x + box.width + 200, box.y + box.height / 2, { steps: 10 });
    await page.mouse.up();
    await expect(start).not.toHaveValue("");
    await expect(end).not.toHaveValue("");

    await page.getByRole("button", { name: "Reset Zoom" }).click();
    await expect(start).toHaveValue("");
    await expect(end).toHaveValue("");

    await drag(efficiency, 0.20, 0.70);
    await expect(start).not.toHaveValue("");
    await expect(end).not.toHaveValue("");

    await efficiency.dblclick();
    await expect(start).toHaveValue("");
    await expect(end).toHaveValue("");

    await drag(fitness, 0.05, 0.20);
    const earlyWindow = `${await start.inputValue()}|${await end.inputValue()}`;
    await page.getByRole("button", { name: "Reset Zoom" }).click();

    await drag(fitness, 0.35, 0.50);
    const midWindow = `${await start.inputValue()}|${await end.inputValue()}`;
    await page.getByRole("button", { name: "Reset Zoom" }).click();

    await drag(fitness, 0.65, 0.90);
    const lateWindow = `${await start.inputValue()}|${await end.inputValue()}`;

    expect(earlyWindow).not.toBe(midWindow);
    expect(midWindow).not.toBe(lateWindow);
    expect(pageErrors).toEqual([]);
  });
});
