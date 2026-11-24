import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const home = () => readFileSync("/tmp/swarm-e2e-home", "utf8").trim();
const cli = (...args: string[]) =>
  execFileSync("uv", ["run", "--quiet", "--project", `${process.cwd()}/..`, "swarm", ...args], { env: { ...process.env, SWARM_HOME: home() } });

test("shows the seeded swarm, workers and board", async ({ page }) => {
  await page.goto("/?token=e2e-token");
  await expect(page.getByText("e2e swarm: build a tiny thing")).toBeVisible();
  await expect(page.getByRole("article", { name: "worker api" })).toBeVisible();
  await expect(page.getByRole("article", { name: "worker ui" })).toBeVisible();
  const board = page.getByRole("region", { name: "board" });
  await expect(board.getByText("split into api and ui")).toBeVisible();
  await expect(board.getByText("need the api shape first")).toBeVisible();
  await expect(page.getByLabel("connection")).toContainText("live");
});

test("events posted from the CLI appear live without a reload", async ({ page }) => {
  await page.goto("/?token=e2e-token");
  await expect(page.getByLabel("connection")).toContainText("live");
  cli("post", "api", "update", "streamed from the cli");
  await expect(page.getByRole("region", { name: "board" }).getByText("streamed from the cli")).toBeVisible({ timeout: 8000 });
});

test("filter by kind", async ({ page }) => {
  await page.goto("/?token=e2e-token");
  const board = page.getByRole("region", { name: "board" });
  await expect(board.getByText("taking the api")).toBeVisible();
  await board.getByRole("button", { name: "blocked", exact: true }).click();
  await expect(board.getByText("need the api shape first")).toBeVisible();
  await expect(board.getByText("taking the api")).toHaveCount(0);
});

test("a human @mention wakes the worker and its reply shows up", async ({ page }) => {
  await page.goto("/?token=e2e-token");
  const card = page.getByRole("article", { name: "worker api" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "@ mention" }).click();
  await page.getByLabel("Message").fill("please add a health endpoint");
  await page.getByRole("button", { name: /Send to @api/ }).click();
  const board = page.getByRole("region", { name: "board" });
  await expect(board.getByText("@api please add a health endpoint")).toBeVisible();
  await expect(board.getByText("got: please add a health endpoint")).toBeVisible({ timeout: 15_000 });
  await expect(card).toContainText("1 · $0.012", { timeout: 15_000 });
});

test("a worker's committed changes show in the diff panel", async ({ page }) => {
  await page.goto("/?token=e2e-token");
  const card = page.getByRole("article", { name: "worker ui" });
  await card.getByRole("button", { name: "diff" }).click();
  const panel = page.getByRole("region", { name: "diff ui" });
  await expect(panel.getByText(/no branch yet/)).toBeVisible();
  await card.getByRole("button", { name: "@ mention" }).click();
  await page.getByLabel("Message").fill("draw the header");
  await page.getByRole("button", { name: /Send to @ui/ }).click();
  await expect(page.getByRole("region", { name: "board" }).getByText("got: draw the header")).toBeVisible({ timeout: 15_000 });
  await expect(card).toContainText("1 · $0.012", { timeout: 15_000 });
  await panel.getByRole("button", { name: "Refresh" }).click();
  await expect(panel.getByLabel("diff summary")).toContainText("1 file changed");
  await expect(panel.getByLabel("patch src/ui/notes.txt")).toContainText("+got:");
  await panel.getByRole("button", { name: "Close" }).click();
  await expect(panel).toHaveCount(0);
});

test("a wrong token shows an error instead of data", async ({ page }) => {
  await page.goto("/?token=nope");
  await expect(page.getByRole("alert")).toContainText(/token/i);
});

test("layout holds at phone width with no horizontal scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/?token=e2e-token");
  await expect(page.getByRole("article", { name: "worker api" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
