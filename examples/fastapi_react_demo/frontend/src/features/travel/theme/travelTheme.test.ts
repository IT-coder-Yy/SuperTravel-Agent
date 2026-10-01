import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { travelAccessibilityColors, travelTheme } from './travelTheme';

const indexCss = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

const cssToken = (name: string) => {
  const match = new RegExp(`--${name}:\\s*([^;]+);`).exec(indexCss);
  if (!match) throw new Error(`Missing CSS token: ${name}`);
  return match[1].trim();
};

const relativeLuminance = (hex: string) => {
  const channels = [1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255);
  const [red, green, blue] = channels.map((channel) => (
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
};

const contrastRatio = (foreground: string, background = '#ffffff') => {
  const first = relativeLuminance(foreground);
  const second = relativeLuminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
};

describe('travel accessibility color tokens', () => {
  it('keeps semantic text tokens at WCAG AA contrast on white', () => {
    expect(contrastRatio(travelAccessibilityColors.success)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(travelAccessibilityColors.warning)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(travelAccessibilityColors.error)).toBeGreaterThanOrEqual(4.5);
    expect(travelTheme.token?.colorSuccess).toBe(travelAccessibilityColors.success);
    expect(travelTheme.token?.colorWarning).toBe(travelAccessibilityColors.warning);
    expect(travelTheme.token?.colorError).toBe(travelAccessibilityColors.error);
  });

  it('uses the same high-contrast semantic colors and a two-tone focus ring in CSS', () => {
    expect(cssToken('travel-success')).toBe(travelAccessibilityColors.success);
    expect(cssToken('travel-warning')).toBe(travelAccessibilityColors.warning);
    expect(cssToken('travel-danger')).toBe(travelAccessibilityColors.error);
    expect(cssToken('travel-focus-color')).toBe(travelAccessibilityColors.focus);
    expect(contrastRatio(travelAccessibilityColors.focus)).toBeGreaterThanOrEqual(3);
    expect(cssToken('travel-focus')).toContain('var(--travel-focus-color)');
  });
});
