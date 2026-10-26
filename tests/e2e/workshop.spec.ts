import { test, expect, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";

async function create(page: Page, title: string) {
  await page.goto("/auth");
  await page.getByLabel("작업실 암호").fill(process.env.APP_PASSWORD!);
  await page.getByRole("button", { name: "작업실 열기" }).click();
  await expect(page).toHaveURL(/\/projects$/);
  await page.getByRole("link", { name: "새 작품", exact: true }).click();
  await page.getByLabel("작품 제목").fill(title);
  await page.getByLabel("세계관").fill("달빛이 닿으면 문이 열린다.");
  await page.getByRole("button", { name: "인물 추가" }).click();
  await page.getByLabel("이름", { exact: true }).fill("하린");
  await page.getByLabel("성격").fill("신중하다");
  await page.getByText("문체 참고 글", { exact: true }).click();
  await page.getByLabel("참고 글 3").fill("달은 조용히 떠올랐다.");
  await page.getByRole("button", { name: "작품 만들기" }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
}

test("login, streamed commit, reload, undo, completion and archive", async ({ page }) => {
  await create(page, `달빛의 문 ${Date.now()}`);
  const url = page.url();
  await page.getByLabel("전개 방향").fill("비밀 문을 발견한다");
  await page.getByRole("checkbox", { name: "이번 요청의 외부 모델 사용 허용" }).check();
  await page.getByRole("button", { name: "첫 회차 생성" }).click();
  await expect(page.getByRole("checkbox", { name: "이번 요청의 외부 모델 사용 허용" })).not.toBeChecked();
  await expect(page.getByText("미확정 초안", { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("원고 저장 완료", { timeout: 20000 });
  await expect(page.locator(".manuscript")).toContainText("하린은 낡은 문");
  await page.reload();
  await expect(page.locator(".manuscript")).toContainText("하린은 낡은 문");
  await page.getByRole("button", { name: "다음 회차 생성" }).click();
  await expect(page.getByRole("status")).toHaveText("원고 저장 완료", { timeout: 20000 });
  await expect(page.locator(".badge")).toHaveText("2회차");
  await page.getByRole("button", { name: "마지막 회차 되돌리기" }).click();
  await expect(page.locator(".badge")).toHaveText("1회차");
  await page.getByRole("button", { name: "생성 없이 완결" }).click();
  await expect(page.getByRole("button", { name: "다음 회차 생성" })).toBeDisabled();
  await mkdir("artifacts/screenshots", { recursive: true });
  await page.screenshot({ path: "artifacts/screenshots/workshop-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await writeFile("artifacts/screenshots/workshop-mobile.png", await page.screenshot({ fullPage: true }), { flush: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "작품 보관", exact: true }).click();
  await expect(page.getByRole("button", { name: "보관 해제" })).toBeVisible();
  await page.goto(url);
  await expect(page.getByRole("button", { name: "보관 해제" })).toBeVisible();
  await page.getByRole("button", { name: "로그아웃" }).click();
  await expect(page).toHaveURL(/\/auth$/);
  await page.goto(url);
  await expect(page).toHaveURL(/\/auth$/);
});

test("cancel saves a draft without committing and stops upstream", async ({ page, request }) => {
  await create(page, `중단 테스트 ${Date.now()}`);
  const before = await (await request.get("http://127.0.0.1:11435/stats")).json();
  await page.getByLabel("전개 방향").fill("SLOW 천천히 진행");
  await page.getByRole("button", { name: "첫 회차 생성" }).click();
  await expect(page.locator(".draft")).toContainText("하린은 낡은 문");
  await page.getByRole("button", { name: "생성 중단" }).click();
  await expect(page.getByRole("status")).toHaveText("생성 중단");
  await expect(page.locator(".badge")).toHaveText("0회차");
  await expect.poll(async () => (await (await request.get("http://127.0.0.1:11435/stats")).json()).cancelledRequests).toBeGreaterThan(before.cancelledRequests);
});

test("length cutoff and invalid memory never commit", async ({ page }) => {
  await create(page, `실패 테스트 ${Date.now()}`);
  await page.getByLabel("전개 방향").fill("LENGTH");
  await page.getByRole("button", { name: "첫 회차 생성" }).click();
  await expect(page.locator("main [role=alert]")).toContainText("출력 길이 제한", { timeout: 20000 });
  await expect(page.locator(".badge")).toHaveText("0회차");
  await page.getByLabel("전개 방향").fill("FAIL_MEMORY");
  await page.getByRole("button", { name: "첫 회차 생성" }).click();
  await expect(page.getByRole("button", { name: "상태 저장 재시도" })).toBeVisible({ timeout: 20000 });
  await expect(page.locator(".draft")).toContainText("하린은 낡은 문");
  await expect(page.locator(".badge")).toHaveText("0회차");
  await page.reload();
  await expect(page.getByRole("button", { name: "상태 저장 재시도" })).toBeVisible({ timeout: 20000 });
  await expect(page.locator(".draft")).toContainText("하린은 낡은 문");
});

test("reload during streaming reuses the saved request", async ({ page, request }) => {
  await create(page, `복구 테스트 ${Date.now()}`);
  const before = await (await request.get("http://127.0.0.1:11435/stats")).json();
  await page.getByLabel("전개 방향").fill("SLOW");
  await page.getByRole("button", { name: "첫 회차 생성" }).click();
  await expect(page.locator(".draft")).toContainText("하린은 낡은 문");
  await page.reload();
  await expect(page.getByRole("status")).toHaveText(/생성 중단|원고 저장 완료|생성이 중단/, { timeout: 20000 });
  const after = await (await request.get("http://127.0.0.1:11435/stats")).json();
  expect(after.narrativeCalls - before.narrativeCalls).toBe(1);
});
