import { expect, test } from '@playwright/test';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8001';

// 回放只有约8秒；每个断言记录完整DOM快照可能让后续操作错过回放窗口。
// 保留动作/网络trace与失败截图，减少诊断本身对交互时序的干扰。
test.use({ trace: { mode: 'retain-on-failure', screenshots: false, snapshots: false } });

test('demo replay restores the formal plan message when skipping to the result', async ({ page }) => {
  // 包含真实设备初始化、两次回放及历史核对，独立于单个控件的等待上限。
  test.setTimeout(90_000);
  const device = await page.request.get(`${baseUrl}/api/device`);
  expect(device.ok()).toBe(true);
  const before = await page.request.get(`${baseUrl}/api/trips`);
  expect(before.ok()).toBe(true);
  const originalHistory = await before.json();
  const replayRequests: string[] = [];
  const forbiddenRequests: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/demo-cases/') && url.pathname.endsWith('/events')) {
      replayRequests.push(url.search);
    }
    if (url.pathname.startsWith('/api/chat-stream')
      || url.pathname.startsWith('/api/routes/')
      || (url.pathname.startsWith('/api/trips') && !['GET', 'HEAD'].includes(request.method()))) {
      forbiddenRequests.push(`${request.method()} ${url.pathname}`);
    }
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.locator('.sidebar-menu-light .ant-menu-item[data-menu-id*="new-chat"]').click();

  const replayCase = page.getByRole('button', { name: /杭州三日人文慢游，开始示例回放/ });
  await expect(replayCase).toBeVisible();
  await replayCase.click();
  await page.getByRole('button', { name: '暂停回放' }).click();
  await expect(page.getByRole('region', { name: '示例回放控制' })).toBeVisible();
  await expect(page.getByRole('button', { name: '继续回放' })).toBeVisible();
  await page.getByRole('button', { name: '继续回放' }).click();
  await expect(page.getByRole('button', { name: '暂停回放' })).toBeVisible();
  await page.getByRole('region', { name: '示例回放控制' }).getByText('2 倍', { exact: true }).click();
  await expect.poll(() => replayRequests.some((query) => new URLSearchParams(query).get('speed') === '2')).toBe(true);
  await page.getByRole('button', { name: '跳到结果' }).click();

  await expect(page.locator('.formal-plan-bubble')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: '以此为模板创建行程' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '旅行工作台' })).toBeVisible();
  await page.getByRole('button', { name: '重新播放' }).click();
  await expect(page.getByRole('button', { name: '暂停回放' })).toBeVisible();
  await page.getByRole('button', { name: '跳到结果' }).click();
  await expect(page.locator('.formal-plan-bubble')).toHaveCount(1);
  await page.getByRole('button', { name: '以此为模板创建行程' }).click();
  await expect(page.getByRole('region', { name: '示例回放控制' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '创建一趟新旅程' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '目的地', exact: true })).toHaveValue('杭州');
  // 新建表单使用默认预算，不复用录制时的临时价格。
  await expect(page.getByRole('spinbutton', { name: '总预算', exact: true })).toHaveValue('6000');
  const after = await page.request.get(`${baseUrl}/api/trips`);
  expect(after.ok()).toBe(true);
  expect(await after.json()).toEqual(originalHistory);
  expect(forbiddenRequests).toEqual([]);
});
