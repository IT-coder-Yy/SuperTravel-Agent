import { expect, test } from '@playwright/test';

const baseUrl = process.env.E2E_BASE_URL || process.env.P9_VITE_BASE_URL || 'http://127.0.0.1:8001';

test('keyboard focus uses the high-contrast two-tone travel ring', async ({ page }) => {
  await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
  const focusableButton = page.locator('button.ant-btn').first();
  await expect(focusableButton).toBeVisible();

  await focusableButton.focus();
  const focusStyle = await focusableButton.evaluate((element) => ({
    active: document.activeElement === element,
    boxShadow: getComputedStyle(element).boxShadow,
    focusColor: getComputedStyle(document.documentElement).getPropertyValue('--travel-focus-color').trim(),
  }));

  expect(focusStyle.active).toBe(true);
  expect(focusStyle.focusColor).toBe('#353b9d');
  expect(focusStyle.boxShadow).toContain('rgb(53, 59, 157)');
});
