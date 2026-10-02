import { expect, test } from '@playwright/test';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8001';

test('desktop C1 places the conversation dock over a full-width map', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: '打开旅行工作台' }).click();
  const layout = page.locator('.chat-map-layout');
  const map = layout.locator('.c1-workbench');
  const dock = layout.locator('.chat-panel');
  await expect(map).toBeVisible();
  await expect(layout.locator('.chat-map-boundary')).toBeHidden();
  const [layoutBox, mapBox, dockBox] = await Promise.all([layout.boundingBox(), map.boundingBox(), dock.boundingBox()]);
  expect(Math.abs(mapBox!.width - layoutBox!.width)).toBeLessThanOrEqual(1);
  expect(dockBox!.x).toBeGreaterThan(mapBox!.x + 300);
  expect(dockBox!.y + dockBox!.height).toBeLessThanOrEqual(mapBox!.y + mapBox!.height);
  await page.getByRole('button', { name: '查看对话' }).click();
  await expect(page.getByRole('button', { name: '收起对话' })).toBeVisible();
  await expect(layout.locator('.chat-scroll-region')).toBeVisible();
});

test('mobile chat view has no residual desktop divider width', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });

  const layout = page.locator('.chat-map-layout');
  const chatPanel = layout.locator('.chat-panel');
  await expect(layout.locator('.chat-map-boundary')).toBeHidden();
  await expect(page.getByRole('button', { name: '打开旅行工作台' })).toBeHidden();
  const [layoutBox, chatBox] = await Promise.all([layout.boundingBox(), chatPanel.boundingBox()]);
  expect(layoutBox).not.toBeNull();
  expect(chatBox).not.toBeNull();
  expect(Math.abs((layoutBox?.width || 0) - (chatBox?.width || 0))).toBeLessThanOrEqual(1);
});

test('mobile has a three-view navigation below the header and bottom management navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.clear());
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });

  const layout = page.locator('.chat-map-layout');
  const navigation = page.getByRole('navigation', { name: '主要视图' });
  await expect(navigation).toBeVisible();
  await expect(navigation.getByText('对话', { exact: true })).toBeVisible();
  await expect(navigation.getByText('行程', { exact: true })).toBeVisible();
  await expect(navigation.getByText('地图', { exact: true })).toBeVisible();
  await expect(navigation).toHaveCSS('position', 'absolute');

  await navigation.getByText('行程', { exact: true }).click();
  await expect(layout).toHaveClass(/mobile-view-trip/);
  await expect(layout.locator('.trip-side-panel')).toBeVisible();
  await expect(layout.locator('.chat-map-boundary')).toBeHidden();

  await navigation.getByText('地图', { exact: true }).click();
  await expect(layout).toHaveClass(/mobile-view-map/);
  await expect(layout.locator('.trip-map-panel')).toBeVisible();
  if (process.env.VISUAL_OUTPUT) {
    await page.screenshot({ path: process.env.VISUAL_OUTPUT });
  }
});
