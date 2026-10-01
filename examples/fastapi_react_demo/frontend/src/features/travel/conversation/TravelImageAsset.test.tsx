import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { displayImageUrl, displayableImageAssets, imageDisplayEligibility, toDisplayableImageAsset, TravelImageAsset, TravelImageWithFallback } from './TravelImageAsset';

const unsplashImage = {
  image_id: 'img-hangzhou-cover',
  url: 'https://images.unsplash.com/photo-123',
  alt: '杭州西湖',
  display_allowed: true,
  export_allowed: false,
  attribution_required: true,
  attribution_text: '摄影师甲',
  attribution_url: 'https://unsplash.com/@photographer-a',
  provider_name: 'Unsplash',
  provider_url: 'https://unsplash.com/photos/example',
  source_ref: 'source-unsplash-cover',
  checked_at: '2026-07-26T10:00:00+08:00',
};

describe('TravelImageAsset', () => {
  it('only accepts assets with explicit display permission and complete required attribution', () => {
    expect(toDisplayableImageAsset(unsplashImage)?.image_id).toBe('img-hangzhou-cover');
    expect(imageDisplayEligibility(unsplashImage)).toBe('eligible');
    expect(imageDisplayEligibility({ ...unsplashImage, attribution_url: 'https://unsplash.com/@photographer?utm_source=My Travel Agent' })).toBe('eligible');
    expect(imageDisplayEligibility({ ...unsplashImage, display_allowed: false })).toBe('display-not-allowed');
    expect(toDisplayableImageAsset({ ...unsplashImage, attribution_url: '' })).toBeNull();
    expect(toDisplayableImageAsset({ ...unsplashImage, display_allowed: false })).toBeNull();
  });

  it('keeps source order, de-duplicates, and limits detail images to three', () => {
    const images = displayableImageAssets([
      unsplashImage,
      { ...unsplashImage, image_id: 'img-2' },
      { ...unsplashImage, image_id: 'img-3' },
      { ...unsplashImage, image_id: 'img-4' },
    ], 3);

    expect(images.map((image) => image.image_id)).toEqual(['img-hangzhou-cover', 'img-2', 'img-3']);
  });

  it('uses the local no-store endpoint only for approved Unsplash CDN URLs', () => {
    expect(displayImageUrl(unsplashImage.url)).toBe(`/api/images/unsplash?url=${encodeURIComponent(unsplashImage.url)}`);
    expect(displayImageUrl('https://example.com/photo.jpg')).toBe('https://example.com/photo.jpg');
  });

  it('does not expose attribution in the reading flow and collapses the image after a loading failure', () => {
    const { container } = render(<TravelImageAsset image={unsplashImage} imageLabel="杭州封面" />);
    const image = screen.getByRole('img', { name: '杭州西湖' });

    expect(image.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(screen.queryByRole('link', { name: '摄影师甲' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Unsplash' })).toBeNull();
    fireEvent.error(image);

    expect(container.querySelector('.travel-image-asset')).toBeNull();
  });

  it('shows the next matched image when the preferred image fails to load', () => {
    render(
      <TravelImageWithFallback
        images={[
          unsplashImage,
          { ...unsplashImage, image_id: 'img-fallback', url: 'https://images.unsplash.com/photo-456', alt: '杭州西湖备用图' },
        ]}
        imageLabel="杭州西湖图片"
      />,
    );

    fireEvent.error(screen.getByRole('img', { name: '杭州西湖' }));

    expect(screen.getByRole('img', { name: '杭州西湖备用图' })).toBeTruthy();
  });
});
