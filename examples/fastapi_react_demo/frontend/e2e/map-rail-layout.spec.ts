import { expect, test } from '@playwright/test';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8001';

test('desktop map control uses the resize divider as its only open boundary', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });

  const layout = page.locator('.chat-map-layout');
  const chatPanel = layout.locator('.chat-panel');
  const openButton = page.getByRole('button', { name: '打开旅行工作台' });

  await expect(openButton).toBeVisible();
  await expect(layout.locator('.chat-map-boundary')).toHaveCount(0);
  const [closedLayoutBox, closedChatBox, closedButtonBox] = await Promise.all([
    layout.boundingBox(),
    chatPanel.boundingBox(),
    openButton.boundingBox(),
  ]);
  expect(closedLayoutBox).not.toBeNull();
  expect(closedChatBox).not.toBeNull();
  expect(closedButtonBox?.width).toBe(44);
  expect(closedButtonBox?.height).toBe(44);
  expect(Math.abs((closedChatBox?.width || 0) - (closedLayoutBox?.width || 0))).toBeLessThanOrEqual(1);
  expect(Math.abs((closedButtonBox?.x || 0) + (closedButtonBox?.width || 0) - ((closedLayoutBox?.x || 0) + (closedLayoutBox?.width || 0) - 12))).toBeLessThanOrEqual(1);

  await openButton.click();
  const closeButton = page.getByRole('button', { name: '关闭旅行工作台' });
  const boundary = layout.locator('.chat-map-boundary');
  const workspace = layout.locator('.trip-side-panel');
  await expect(closeButton).toBeVisible();
  await expect(boundary).toBeVisible();
  await expect(workspace).toBeVisible();

  const [openLayoutBox, openChatBox, boundaryBox, workspaceBox, openButtonBox] = await Promise.all([
    layout.boundingBox(),
    chatPanel.boundingBox(),
    boundary.boundingBox(),
    workspace.boundingBox(),
    closeButton.boundingBox(),
  ]);
  expect(boundaryBox?.width).toBe(1);
  expect(openButtonBox?.width).toBe(24);
  expect(openButtonBox?.height).toBe(48);
  expect(Math.abs((openChatBox?.x || 0) + (openChatBox?.width || 0) - (boundaryBox?.x || 0))).toBeLessThanOrEqual(1);
  expect(Math.abs((boundaryBox?.x || 0) + (boundaryBox?.width || 0) - (workspaceBox?.x || 0))).toBeLessThanOrEqual(1);
  expect(Math.abs((workspaceBox?.x || 0) + (workspaceBox?.width || 0) - ((openLayoutBox?.x || 0) + (openLayoutBox?.width || 0)))).toBeLessThanOrEqual(1);

  if (process.env.VISUAL_OUTPUT) {
    await page.screenshot({ path: process.env.VISUAL_OUTPUT });
  }
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

test('mobile has a fixed three-view primary navigation without a desktop rail', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.clear());
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });

  const layout = page.locator('.chat-map-layout');
  const navigation = page.getByRole('navigation', { name: '主要视图' });
  await expect(navigation).toBeVisible();
  await expect(navigation.getByText('对话', { exact: true })).toBeVisible();
  await expect(navigation.getByText('行程', { exact: true })).toBeVisible();
  await expect(navigation.getByText('地图', { exact: true })).toBeVisible();
  await expect(navigation).toHaveCSS('position', 'fixed');

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
