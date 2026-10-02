import { expect, test, type Page } from '@playwright/test';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8001';

const openNewTrip = async (page: Page) => {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: '新旅程', exact: true }).click();
  await expect(page.getByRole('heading', { name: '创建一趟新旅程' })).toBeVisible();
};

test('incomplete natural-language request shows and replaces one clarification card at a time', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openNewTrip(page);

  const naturalInput = page.getByPlaceholder('直接描述目的地和天数…');
  await naturalInput.fill('帮我规划杭州三日游');
  await naturalInput.press('Enter');

  const clarificationPanel = page.locator('.clarification-panel');
  await expect(clarificationPanel).toHaveCount(1, { timeout: 20_000 });
  await expect(page.getByText('你从哪里出发？', { exact: true })).toBeVisible();
  await expect(page.locator('.clarification-question-card')).toHaveCount(1);
  await expect(page.getByPlaceholder('请先回答或跳过上方问题')).toBeDisabled();

  await page.getByRole('button', { name: '选择“杭州本地出发”' }).click();

  await expect(page.getByText('3天行程，具体什么时候出发？', { exact: true })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('你从哪里出发？', { exact: true })).toHaveCount(0);
  await expect(page.locator('.clarification-panel')).toHaveCount(1);
  await expect(page.locator('.clarification-question-card')).toHaveCount(1);
  await expect(page.getByRole('button', { name: '选择“日期暂未确定”' })).toBeVisible();
  await expect(page.getByPlaceholder('请先回答上方问题')).toBeDisabled();
  await expect(page.getByRole('button', { name: /跳过问题/ })).toHaveCount(0);
  await expect(page.getByText('已补充 1 项', { exact: true })).toBeVisible();
});
