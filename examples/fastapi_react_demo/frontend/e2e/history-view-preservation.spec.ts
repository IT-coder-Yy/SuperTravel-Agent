import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import domesticFixture from '../../backend/tests/fixtures/trip_v3_domestic_3d.json';
import internationalFixture from '../../backend/tests/fixtures/trip_v3_international_5d.json';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8002';

test.use({ viewport: { width: 1440, height: 900 } });

test('late same-trip history response preserves itinerary and activity detail; another trip restores independently', async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  const createdIds: string[] = [];
  const observations: Record<string, unknown>[] = [];
  let releaseResponse = () => {};
  const seed = async (label: string, document: unknown) => {
    const id = `e2e-history-view-${randomUUID()}`;
    const title = `E2E 历史上下文 ${label} ${id.slice(-8)}`;
    const saved = await page.request.put(`${baseUrl}/api/trips/${id}`, {
      data: {
        title,
        messages: [{ id: `${id}-message`, role: 'user', content: `${label}契约测试`, displayContent: `${label}契约测试`, timestamp: new Date().toISOString() }],
        change_reason: 'user_message',
      },
    });
    expect(saved.ok(), await saved.text()).toBe(true);
    createdIds.push(id);
    const formal = await page.request.post(`${baseUrl}/api/trips/${id}/formal`, {
      data: { operation_id: `${id}-formal`, document },
    });
    expect(formal.ok(), await formal.text()).toBe(true);
    return { id, title };
  };
  const settleRender = async (target: Page) => target.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  const selectTrip = async (trip: { id: string; title: string }) => {
    const response = page.waitForResponse((item) => item.url().endsWith(`/api/trips/${trip.id}`) && item.request().method() === 'GET');
    await page.getByText(trip.title, { exact: true }).first().click();
    expect((await response).ok()).toBe(true);
    await settleRender(page);
  };

  try {
    expect((await page.request.get(`${baseUrl}/api/device`)).ok()).toBe(true);
    const second = await seed('东京', internationalFixture);
    const first = await seed('杭州', domesticFixture);
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    observations.push({ stage: 'loaded-build', scripts: await page.locator('script[src]').evaluateAll((scripts) => scripts.map((script) => script.getAttribute('src'))) });
    await expect(page.getByText(first.title, { exact: true }).first()).toBeVisible();
    await expect(page.locator('.trip-workspace')).toBeVisible();
    await selectTrip(first);
    const workspace = page.locator('.trip-workspace');
    await expect(workspace.getByText('杭州', { exact: true }).first()).toBeVisible();

    let notifyHeld = () => {};
    const held = new Promise<void>((resolve) => { notifyHeld = resolve; });
    const released = new Promise<void>((resolve) => { releaseResponse = resolve; });
    let intercepted = false;
    await page.route(`**/api/trips/${first.id}`, async (route) => {
      if (intercepted || route.request().method() !== 'GET') return route.continue();
      intercepted = true;
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      observations.push({ stage: 'real-history-response-held', status: response.status(), tripId: first.id });
      notifyHeld();
      await released;
      await route.fulfill({ response });
    });
    await page.getByText(first.title, { exact: true }).first().click();
    await held;
    await workspace.locator('.trip-workspace-tabs').getByRole('tab', { name: '日程', exact: true }).click();
    await workspace.locator('[data-activity-id="act_hz_1"]').getByRole('button', { name: '查看杭州契约景点一详情并定位地图' }).click();
    const detail = workspace.getByRole('region', { name: '杭州契约景点一详情', exact: true });
    await expect(detail).toBeVisible();
    await detail.getByRole('button', { name: '返回 Day 1 日程', exact: true }).click();
    await expect(workspace.locator('[data-activity-id="act_hz_1"]')).toHaveClass(/trip-activity-card--selected/);
    await workspace.locator('[data-activity-id="act_hz_1"]').getByRole('button', { name: '查看杭州契约景点一详情并定位地图' }).click();
    await expect(detail).toBeVisible();
    const workspaceBeforeRestore = await workspace.elementHandle();
    observations.push({ stage: 'detail-open-while-history-pending' });
    const restored = page.waitForEvent('console', { predicate: (message) => message.text().includes('useEffect 检测到 loadedMessages 变化') });
    releaseResponse();
    await restored;
    await settleRender(page);
    observations.push({ stage: 'history-restored', originalWorkspaceStillConnected: await workspaceBeforeRestore?.evaluate((element) => element.isConnected) });
    await expect(detail).toBeVisible();
    await detail.getByRole('button', { name: '返回 Day 1 日程', exact: true }).click();
    await expect(workspace.locator('.trip-workspace-tabs').getByRole('tab', { name: '日程', exact: true })).toHaveAttribute('aria-selected', 'true');
    await expect(workspace.locator('[data-activity-id="act_hz_1"]')).toHaveClass(/trip-activity-card--selected/);
    observations.push({ stage: 'same-trip-preserved-detail-and-itinerary' });

    await selectTrip(second);
    await workspace.locator('.trip-workspace-tabs').getByRole('tab', { name: '日程', exact: true }).click();
    await expect(workspace.locator('[data-activity-id="act_tokyo_1"]')).toBeVisible();
    await expect(workspace.locator('[data-activity-id="act_hz_1"]')).toHaveCount(0);
    await expect(workspace.getByRole('region', { name: '杭州契约景点一详情', exact: true })).toHaveCount(0);
    observations.push({ stage: 'different-trip-restored-without-previous-context' });
  } finally {
    releaseResponse();
    await page.unrouteAll({ behavior: 'wait' });
    for (const id of createdIds) {
      const removed = await page.request.delete(`${baseUrl}/api/trips/${id}`);
      observations.push({ stage: 'fixture-cleanup', tripId: id, status: removed.status() });
      expect(removed.ok()).toBe(true);
    }
    const evidencePath = testInfo.outputPath('history-view-observations.json');
    await writeFile(evidencePath, JSON.stringify(observations, null, 2), 'utf8');
    await testInfo.attach('history-view-observations', { path: evidencePath, contentType: 'application/json' });
  }
});
