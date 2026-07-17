import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });

test('vague trip request asks one independent clarification question', async ({ page }) => {
  await page.addInitScript(() => localStorage.clear());
  await page.goto(process.env.E2E_BASE_URL || 'http://127.0.0.1:8001/');

  const input = page.getByPlaceholder('发消息...');
  await input.fill('帮我规划一次北京3天2夜的特种兵之旅');
  await input.press('Enter');

  const panel = page.locator('.clarification-dock .clarification-panel');
  await expect(panel).toBeVisible({ timeout: 20_000 });
  await expect(panel.getByText('补充关键信息')).toBeVisible();
  await expect(panel.locator('.clarification-question-card')).toHaveCount(1);
  await expect(page.locator('.chat-input-container .clarification-panel')).toHaveCount(0);
  await expect(page.locator('.chat-input-container textarea')).toBeDisabled();

  const answerButtons = panel.locator('.clarification-option-row button');
  if (await answerButtons.count()) {
    await answerButtons.first().click();
    await expect(panel.getByText('正在结合你的回答继续分析...')).toBeVisible();
  }
});

test('mobile primary view switcher does not overlap the first content block', async ({ page }) => {
  await page.setViewportSize({ width: 412, height: 915 });
  await page.addInitScript(() => localStorage.clear());
  await page.goto(process.env.E2E_BASE_URL || 'http://127.0.0.1:8001/');

  const switcher = page.locator('.mobile-primary-view-switcher');
  const firstHeading = page.getByText('开始一段轻松的旅行规划');
  await expect(switcher).toBeVisible();
  await expect(firstHeading).toBeVisible();
  const switcherBox = await switcher.boundingBox();
  const headingBox = await firstHeading.boundingBox();
  expect(switcherBox && headingBox && switcherBox.y + switcherBox.height <= headingBox.y).toBeTruthy();
  await expect(page.locator('.map-toggle-orb')).toBeHidden();
});
