import { expect, test, type Page } from '@playwright/test';
import formalFixture from '../../backend/tests/fixtures/trip_v3_domestic_3d.json';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8001';

test.use({ viewport: { width: 1440, height: 900 } });

const seedFormalTrip = async (page: Page) => {
  const timestamp = Date.now();
  const tripId = `e2e-planning-protection-${timestamp}`;
  const tripTitle = `E2E 正式方案保护 ${timestamp}`;
  await page.request.get(`${baseUrl}/api/device`);
  const saved = await page.request.put(`${baseUrl}/api/trips/${tripId}`, {
    data: {
      title: tripTitle,
      messages: [{
        id: `e2e-user-${timestamp}`,
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
      operation_id: `e2e-formal-${timestamp}`,
      document: formalFixture,
    },
  });
  expect(applied.ok()).toBe(true);
  return { tripId, tripTitle };
};

const openFormalTrip = async (page: Page, tripTitle: string) => {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  const historyEntry = page.getByText(tripTitle, { exact: true }).first();
  await expect(historyEntry).toBeVisible({ timeout: 30_000 });
  await historyEntry.click();
  await page.locator('.trip-workspace-tabs').getByText('日程', { exact: true }).click();
  const formalActivity = page.locator('[data-activity-id="act_hz_1"]');
  await expect(formalActivity).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: '继续调整' }).click();
  await expect(page.getByPlaceholder('发消息...')).toBeVisible();
  return formalActivity;
};

test('formal workspace survives stopping a replanning request', async ({ page }) => {
  test.setTimeout(30_000);
  await page.addInitScript(() => localStorage.clear());
  const { tripId, tripTitle } = await seedFormalTrip(page);
  try {
    await page.route('**/api/planning-runs/active', (route) => (
      route.fulfill({ json: { active_run: null } })
    ));
    let chatRequestCount = 0;
    await page.route('**/api/chat-stream', async (route) => {
      chatRequestCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 3_000));
      await route.abort('aborted');
    });
    const formalActivity = await openFormalTrip(page, tripTitle);
    const input = page.getByPlaceholder('发消息...');
    const sendButton = page.locator('.chat-input-container button.ant-btn-primary');

    await input.fill('请重新规划这份正式方案');
    await input.press('Enter');
    await expect(sendButton.locator('.anticon-stop')).toBeVisible();
    await sendButton.click();

    await expect(input).toBeEnabled();
    await expect(input).toHaveValue('请重新规划这份正式方案');
    await expect(formalActivity).toBeVisible();
    expect(chatRequestCount).toBe(1);
  } finally {
    await page.request.delete(`${baseUrl}/api/trips/${tripId}`, { timeout: 10_000 }).catch(() => undefined);
  }
});

test('active run preflight blocks a second tab and keeps its formal workspace', async ({ page }) => {
  test.setTimeout(30_000);
  await page.addInitScript(() => localStorage.clear());
  const { tripId, tripTitle } = await seedFormalTrip(page);
  try {
    let activeCheckCount = 0;
    let chatRequestCount = 0;
    await page.route('**/api/planning-runs/active', (route) => {
      activeCheckCount += 1;
      return route.fulfill({
        json: {
          active_run: {
            run_id: 'run-tab-a',
            trip_id: 'trip-tab-a',
            request_id: 'request-tab-a',
            status: 'running',
            started_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        },
      });
    });
    await page.route('**/api/chat-stream', (route) => {
      chatRequestCount += 1;
      return route.abort('blockedbyclient');
    });
    const formalActivity = await openFormalTrip(page, tripTitle);
    const input = page.getByPlaceholder('发消息...');
    const sendButton = page.locator('.chat-input-container button.ant-btn-primary');

    await input.fill('标签页 B 的第二次规划');
    await sendButton.click();

    await expect.poll(() => activeCheckCount).toBeGreaterThanOrEqual(1);
    expect(activeCheckCount).toBeLessThanOrEqual(2);
    await expect(page.getByText('另一标签页正在规划旅程。当前设备一次只能运行一项规划，请等待其完成，或在原标签页停止后再试。')).toBeVisible();
    await expect(formalActivity).toBeVisible();
    await expect(input).toHaveValue('标签页 B 的第二次规划');
    expect(chatRequestCount).toBe(0);
  } finally {
    await page.request.delete(`${baseUrl}/api/trips/${tripId}`, { timeout: 10_000 }).catch(() => undefined);
  }
});
