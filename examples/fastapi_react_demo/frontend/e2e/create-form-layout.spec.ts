import { expect, test, type Page } from '@playwright/test';

const baseUrl = process.env.E2E_BASE_URL || 'http://127.0.0.1:8001';

const openNewTrip = async (page: Page) => {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  await page.locator('.sidebar-menu-light .ant-menu-item[data-menu-id*="new-chat"]').click();
  await expect(page.getByRole('heading', { name: '创建一趟新旅程' })).toBeVisible();
};

const expectSameTop = async (page: Page, firstLabel: string, secondLabel: string) => {
  const formItemFor = (label: string) => page
    .getByText(label, { exact: true })
    .first()
    .locator('xpath=ancestor::*[contains(concat(" ", normalize-space(@class), " "), " ant-form-item ")][1]');
  // The form enters with motion; compare settled rows instead of one frame
  // captured while the animation or location hint is changing the layout.
  await expect.poll(async () => {
    const [first, second] = await Promise.all([
      formItemFor(firstLabel).boundingBox(),
      formItemFor(secondLabel).boundingBox(),
    ]);
    if (!first || !second) return Number.POSITIVE_INFINITY;
    return Math.abs(first.y - second.y);
  }, { message: `${firstLabel} and ${secondLabel} must align` }).toBeLessThanOrEqual(2);
};

test('desktop creation form uses aligned rows and the restored theme', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openNewTrip(page);
  await expect(page.getByText('正在获取你当前所在的城市…')).toHaveCount(0);

  await expectSameTop(page, '出发地', '目的地');
  await expectSameTop(page, '总预算', '旅行日期');
  await expectSameTop(page, '成人', '儿童');
  await expectSameTop(page, '儿童', '老人');
  await expectSameTop(page, '成人', '同行关系（可选）');
  await expect(page.locator('.trip-create-route-arrow')).toHaveCount(0);
  for (const relationship of ['独自旅行', '情侣', '朋友', '亲子', '家庭']) {
    await expect(page.locator('.trip-create-party-type .ant-radio-button-wrapper', { hasText: relationship })).toBeVisible();
  }
  await expect(page.getByText('点选示例，一键体验完整链路。')).toBeVisible();
  await expect(page.getByText('也可以直接在下方描述旅行需求')).toHaveCount(0);
  await expect(page.getByText('或', { exact: true })).toHaveCount(0);

  const background = await page.locator('body').evaluate((element) => getComputedStyle(element).backgroundImage);
  expect(background).toContain('radial-gradient');

  if (process.env.VISUAL_OUTPUT) {
    await page.screenshot({ path: process.env.VISUAL_OUTPUT });
  }
});

test('390px creation form keeps every required field available', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openNewTrip(page);

  for (const label of ['出发地', '目的地', '总预算', '成人数量', '儿童数量', '老人数量']) {
    await expect(page.getByLabel(label)).toBeVisible();
  }
  await expect(page.getByLabel('同行关系')).toBeVisible();
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(horizontalOverflow).toBeLessThanOrEqual(1);
  await expect(page.getByRole('button', { name: '创建并开始规划' })).toBeVisible();
});
