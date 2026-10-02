import { expect, test, type Page } from '@playwright/test';
import formalFixture from '../../backend/tests/fixtures/trip_v3_domestic_3d.json';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8001';

test.use({ viewport: { width: 390, height: 844 } });

const seedFormalTrip = async (page: Page) => {
  const timestamp = Date.now();
  const tripId = `e2e-mobile-detail-${timestamp}`;
  const tripTitle = `E2E 移动详情回跳 ${timestamp}`;
  await page.request.get(`${baseUrl}/api/device`);
  const saved = await page.request.put(`${baseUrl}/api/trips/${tripId}`, {
    data: {
      title: tripTitle,
      messages: [{
        id: `e2e-mobile-detail-message-${timestamp}`,
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
    data: { operation_id: `e2e-mobile-detail-formal-${timestamp}`, document: formalFixture },
  });
  expect(applied.ok()).toBe(true);
  return { tripId, tripTitle };
};

test('mobile activity detail opens in the itinerary and returns from map to its original card', async ({ page }) => {
  await page.addInitScript(() => localStorage.clear());
  const { tripId, tripTitle } = await seedFormalTrip(page);
  try {
    // 手机端历史栏只展示图标，先在桌面布局选中隔离测试旅程，再切换至手机布局验证主流程。
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '旅程管理', exact: true }).click();
  const historyEntry = page.getByText(tripTitle, { exact: true }).first();
    await expect(historyEntry).toBeVisible({ timeout: 30_000 });
    await historyEntry.click();

    await page.setViewportSize({ width: 390, height: 844 });

    const layout = page.locator('.chat-map-layout');
    const navigation = page.getByRole('navigation', { name: '主要视图' });
    await navigation.getByText('行程', { exact: true }).click();
    await page.getByRole('button', { name: '编辑行程' }).click();
    await page.locator('.trip-workspace-tabs').getByText('日程', { exact: true }).click();
    const activityCard = page.locator('[data-activity-id="act_hz_1"]');
    await expect(activityCard.getByRole('button', { name: '查看杭州契约景点一详情并定位地图' })).toBeVisible();

    await activityCard.getByRole('button', { name: '查看杭州契约景点一详情并定位地图' }).click();
    await expect(layout).toHaveClass(/mobile-view-trip/);
    await expect(page.locator('.trip-workspace--mobile-detail')).toBeVisible();
    await expect(page.getByText('Day 1 · 活动详情')).toBeVisible();
    await expect(layout.locator('.trip-map-panel')).toBeHidden();

    await page.getByRole('button', { name: '在地图中查看' }).click();
    await expect(layout).toHaveClass(/mobile-view-map/);
    const returnButton = page.locator('.trip-map-return');
    await expect(returnButton).toHaveText('返回杭州契约景点一');
    await returnButton.click();

    await expect(layout).toHaveClass(/mobile-view-trip/);
    await expect(page.locator('.trip-workspace--mobile-detail')).toHaveCount(0);
    await expect(activityCard).toBeVisible();
    await expect(activityCard).toHaveClass(/trip-activity-card--selected/);
  } finally {
    await page.request.delete(`${baseUrl}/api/trips/${tripId}`, { timeout: 10_000 }).catch(() => undefined);
  }
});
