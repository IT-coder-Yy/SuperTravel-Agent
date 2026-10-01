import { expect, test, type Page } from '@playwright/test';
import domesticFixture from '../../backend/tests/fixtures/trip_v3_domestic_3d.json';
import internationalFixture from '../../backend/tests/fixtures/trip_v3_international_5d.json';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8001';

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

const seedFormalTrip = async (
  page: Page,
  label: string,
  document: Record<string, any>,
) => {
  const timestamp = Date.now();
  const tripId = `e2e-p8-${label}-${timestamp}`;
  const tripTitle = `E2E P8 ${label} ${timestamp}`;
  await page.request.get(`${baseUrl}/api/device`);
  const saved = await page.request.put(`${baseUrl}/api/trips/${tripId}`, {
    data: {
      title: tripTitle,
      messages: [{
        id: `e2e-p8-message-${timestamp}`,
        role: 'user',
        content: `${label} 行程`,
        displayContent: `${label} 行程`,
        timestamp: new Date().toISOString(),
      }],
      change_reason: 'user_message',
    },
  });
  expect(saved.ok()).toBe(true);
  const applied = await page.request.post(`${baseUrl}/api/trips/${tripId}/formal`, {
    data: {
      operation_id: `e2e-p8-formal-${timestamp}`,
      document,
    },
  });
  expect(applied.ok()).toBe(true);
  return { tripId, tripTitle };
};

const openFormalTrip = async (page: Page, tripTitle: string, activityId: string) => {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  const historyEntry = page.getByText(tripTitle, { exact: true }).first();
  await expect(historyEntry).toBeVisible({ timeout: 15_000 });
  await historyEntry.click();
  const workspace = page.locator('.trip-workspace');
  await expect(workspace).toBeVisible();
  await workspace.locator('.trip-workspace-tabs').getByText('日程', { exact: true }).click();
  const activityCard = workspace.locator(`[data-activity-id="${activityId}"]`);
  await expect(activityCard).toBeVisible({ timeout: 15_000 });
  return { workspace, activityCard };
};

const deleteTrip = async (page: Page, tripId: string) => {
  await page.request.delete(`${baseUrl}/api/trips/${tripId}`, { timeout: 10_000 }).catch(() => undefined);
};

test('new trip keeps the create form when a late automatic history restore returns', async ({ page }) => {
  const { tripId } = await seedFormalTrip(page, '恢复竞争', clone(domesticFixture) as Record<string, any>);
  try {
    await page.route(`**/api/trips/${tripId}`, async (route) => {
      const response = await route.fetch();
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.fulfill({ response });
    });
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    await page.getByRole('menuitem', { name: /新旅程/ }).click();
    const createHeading = page.getByRole('heading', { name: '创建一趟新旅程' });
    await expect(createHeading).toBeVisible();
    await page.waitForTimeout(700);
    await expect(createHeading).toBeVisible();
  } finally {
    await deleteTrip(page, tripId);
  }
});

test('international formal trip preserves dual time zones and foreign-currency budget', async ({ page }) => {
  const { tripId, tripTitle } = await seedFormalTrip(page, '国际', clone(internationalFixture) as Record<string, any>);
  try {
    const { workspace, activityCard } = await openFormalTrip(page, tripTitle, 'act_tokyo_1');
    await expect(activityCard).toContainText('东京契约地点一');
    await workspace.locator('.trip-workspace-tabs').getByText('概览', { exact: true }).click();
    await expect(workspace.getByText('东京', { exact: true })).toBeVisible();
    await expect(workspace.getByText(/当地时间 09:00 \/ 北京时间 09:00-当地时间 14:00 \/ 北京时间 13:00/)).toBeVisible();
    await expect(workspace.getByText(/JPY 80,000（约¥4,000）/)).toBeVisible();
    await expect(workspace.getByText('JPY 500,000', { exact: true }).first()).toBeVisible();
  } finally {
    await deleteTrip(page, tripId);
  }
});

test('provider transport failure shows verified fallback text without a fabricated service number', async ({ page }) => {
  const fallbackDocument = clone(domesticFixture) as Record<string, any>;
  const outboundReason = '暂无可靠实时票务数据。可先按“上海 → 杭州”比较交通方式。';
  fallbackDocument.status = 'degraded';
  fallbackDocument.outbound_transport = {
    direction: 'outbound',
    scope: 'domestic',
    status: 'unavailable',
    status_reason: outboundReason,
    official_query_url: 'https://www.12306.cn/index/',
    options: [],
  };
  fallbackDocument.return_transport = {
    direction: 'return',
    scope: 'domestic',
    status: 'unavailable',
    status_reason: '返程暂无可靠实时票务数据，请在出发前重新查询。',
    official_query_url: 'https://www.12306.cn/index/',
    options: [],
  };
  fallbackDocument.validation = { valid: true, degraded: true, issues: [] };

  const { tripId, tripTitle } = await seedFormalTrip(page, 'Provider降级', fallbackDocument);
  try {
    const { workspace } = await openFormalTrip(page, tripTitle, 'act_hz_1');
    await workspace.locator('.trip-workspace-tabs').getByText('概览', { exact: true }).click();
    await expect(workspace.getByText(outboundReason, { exact: true })).toBeVisible();
    await expect(workspace.getByText('TEST-G001', { exact: true })).toHaveCount(0);
    await expect(workspace.getByText('规划采用', { exact: true })).toHaveCount(0);
  } finally {
    await deleteTrip(page, tripId);
  }
});

test('editing an activity creates a draft and applies it as a new formal revision', async ({ page }) => {
  const { tripId, tripTitle } = await seedFormalTrip(page, '编辑', clone(domesticFixture) as Record<string, any>);
  const note = 'P8 隔离测试备注';
  try {
    const { workspace, activityCard } = await openFormalTrip(page, tripTitle, 'act_hz_1');
    await activityCard.getByRole('button', { name: '编辑杭州契约景点一的时间、备注或候选状态' }).click();
    await page.getByText('添加活动备注', { exact: true }).click();
    await page.getByLabel('杭州契约景点一活动备注').fill(note);
    const editRequest = page.waitForRequest((request) => (
      request.url().endsWith('/api/trip-edit') && request.method() === 'POST'
    ));
    const editResponse = page.waitForResponse((response) => (
      response.url().endsWith('/api/trip-edit') && response.request().method() === 'POST'
    ));
    await page.getByRole('button', { name: '保存备注', exact: true }).click();
    const request = await editRequest;
    const requestPayload = request.postDataJSON() as { document?: Record<string, unknown> };
    expect(requestPayload.document?.schema_version).toBe('3.0');
    expect(requestPayload.document?.itinerary).toBeTruthy();
    const response = await editResponse;
    expect(response.status(), await response.text()).toBe(200);
    await expect(activityCard).toContainText(note);
    await expect(workspace.getByRole('button', { name: '放弃草稿', exact: true })).toBeVisible();

    await workspace.getByRole('button', { name: '应用修改', exact: true }).click();
    const confirmation = page.locator('.ant-popconfirm').getByRole('button', { name: '应用修改', exact: true });
    await expect(confirmation).toBeVisible();
    await confirmation.click();
    await expect(workspace.getByRole('button', { name: '恢复上一版本', exact: true })).toBeVisible({ timeout: 15_000 });
    const detail = await page.request.get(`${baseUrl}/api/trips/${tripId}`);
    expect(detail.ok()).toBe(true);
    const detailPayload = await detail.json() as { formalSnapshots?: { current?: { revision?: number; document?: { notes?: Array<{ content?: string }> } } }; draft?: unknown };
    expect(detailPayload.formalSnapshots?.current?.revision).toBe(2);
    expect(detailPayload.formalSnapshots?.current?.document?.notes?.some((item) => item.content === note)).toBe(true);
    expect(detailPayload.draft).toBeNull();
    await expect(activityCard).toContainText(note);
  } finally {
    await deleteTrip(page, tripId);
  }
});
