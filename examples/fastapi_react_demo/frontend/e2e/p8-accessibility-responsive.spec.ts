import { expect, test, type Page } from '@playwright/test';
import domesticFixture from '../../backend/tests/fixtures/trip_v3_domestic_3d.json';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8001';

const seedFormalTrip = async (page: Page) => {
  const timestamp = Date.now();
  const tripId = `e2e-p8-a11y-${timestamp}`;
  const tripTitle = `E2E P8 无障碍 ${timestamp}`;
  await page.request.get(`${baseUrl}/api/device`);
  const saved = await page.request.put(`${baseUrl}/api/trips/${tripId}`, {
    data: {
      title: tripTitle,
      messages: [{
        id: `e2e-p8-a11y-message-${timestamp}`,
        role: 'user',
        content: '杭州三日游',
        displayContent: '杭州三日游',
        timestamp: new Date().toISOString(),
      }],
      change_reason: 'user_message',
    },
  });
  expect(saved.ok()).toBe(true);
  const applied = await page.request.post(`${baseUrl}/api/trips/${tripId}/formal`, {
    data: {
      operation_id: `e2e-p8-a11y-formal-${timestamp}`,
      document: JSON.parse(JSON.stringify(domesticFixture)),
    },
  });
  expect(applied.ok()).toBe(true);
  return { tripId, tripTitle };
};

const expectNoHorizontalOverflow = async (page: Page) => {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
};

test('390px mobile workspace keeps controls touch-sized, avoids overflow, and supports keyboard detail-map return', async ({ page }) => {
  await page.addInitScript(() => localStorage.clear());
  const { tripId, tripTitle } = await seedFormalTrip(page);
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '旅程管理', exact: true }).click();
    await page.getByText(tripTitle, { exact: true }).first().click();

    await page.setViewportSize({ width: 390, height: 844 });
    const navigation = page.getByRole('navigation', { name: '主要视图' });
    await expect(navigation).toBeVisible();
    await expectNoHorizontalOverflow(page);

    const targetSizes = await navigation.locator('.ant-segmented-item-label').evaluateAll((items) => (
      items.map((item) => {
        const box = item.getBoundingClientRect();
        return { width: box.width, height: box.height };
      })
    ));
    expect(targetSizes).toHaveLength(3);
    expect(targetSizes.every((size) => size.width >= 40 && size.height >= 40)).toBe(true);

    await navigation.getByText('行程', { exact: true }).click();
    await page.getByRole('button', { name: '编辑行程' }).click();
    const workspace = page.locator('.trip-workspace');
    await workspace.locator('.trip-workspace-tabs').getByText('日程', { exact: true }).click();
    const detailButton = page.getByRole('button', { name: '查看杭州契约景点一详情并定位地图' });
    await detailButton.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.trip-workspace--mobile-detail')).toBeVisible();

    const mapButton = page.getByRole('button', { name: '在地图中查看' });
    await mapButton.focus();
    await page.keyboard.press('Enter');
    const returnButton = page.getByRole('button', { name: '返回杭州契约景点一' });
    await expect(returnButton).toBeVisible();
    const returnBox = await returnButton.boundingBox();
    expect(returnBox && returnBox.height >= 44).toBe(true);

    await returnButton.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-activity-id="act_hz_1"]')).toHaveClass(/trip-activity-card--selected/);
    await expectNoHorizontalOverflow(page);
    if (process.env.VISUAL_OUTPUT) {
      await page.screenshot({ path: process.env.VISUAL_OUTPUT });
    }
  } finally {
    await page.request.delete(`${baseUrl}/api/trips/${tripId}`, { timeout: 10_000 }).catch(() => undefined);
  }
});
