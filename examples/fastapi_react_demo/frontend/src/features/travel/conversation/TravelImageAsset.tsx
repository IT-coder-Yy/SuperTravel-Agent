import { useEffect, useState } from 'react';
import type { ImageAssetV3 } from '../state/travelPlannerTypes';

type ImageRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is ImageRecord => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const textAt = (value: ImageRecord, key: string): string => (
  typeof value[key] === 'string' ? value[key].trim() : ''
);

const normalizedHttpsUrl = (value: string): string => {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'https:' ? parsed.toString() : '';
  } catch {
    return '';
  }
};

export const displayImageUrl = (url: string): string => {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:' && parsed.hostname === 'images.unsplash.com') {
      return `/api/images/unsplash?url=${encodeURIComponent(url)}`;
    }
  } catch {
    return url;
  }
  return url;
};

export const imageDisplayEligibility = (value: unknown): string => {
  if (!isRecord(value)) return 'missing';
  if (value.display_allowed !== true) return 'display-not-allowed';
  const imageId = textAt(value, 'image_id');
  const url = normalizedHttpsUrl(textAt(value, 'url'));
  const sourceRef = textAt(value, 'source_ref');
  const attributionRequired = value.attribution_required === true;
  const attributionText = textAt(value, 'attribution_text');
  const attributionUrl = normalizedHttpsUrl(textAt(value, 'attribution_url'));
  const providerName = textAt(value, 'provider_name');
  const providerUrl = normalizedHttpsUrl(textAt(value, 'provider_url'));

  if (!imageId || !sourceRef) return 'missing-identity';
  if (!url) return 'invalid-url';
  if (attributionRequired && (!attributionText || !attributionUrl || !providerName || !providerUrl)) {
    return 'incomplete-attribution';
  }
  return 'eligible';
};

export const toDisplayableImageAsset = (value: unknown): ImageAssetV3 | null => {
  if (imageDisplayEligibility(value) !== 'eligible' || !isRecord(value)) return null;
  const imageId = textAt(value, 'image_id');
  const url = normalizedHttpsUrl(textAt(value, 'url'));
  const sourceRef = textAt(value, 'source_ref');
  const attributionRequired = value.attribution_required === true;
  const attributionText = textAt(value, 'attribution_text');
  const attributionUrl = normalizedHttpsUrl(textAt(value, 'attribution_url'));
  const providerName = textAt(value, 'provider_name');
  const providerUrl = normalizedHttpsUrl(textAt(value, 'provider_url'));

  return {
    image_id: imageId,
    url,
    alt: textAt(value, 'alt'),
    mime_type: textAt(value, 'mime_type') || null,
    width: typeof value.width === 'number' ? value.width : null,
    height: typeof value.height === 'number' ? value.height : null,
    display_allowed: true,
    export_allowed: value.export_allowed === true,
    attribution_required: attributionRequired,
    attribution_text: attributionText || null,
    attribution_url: attributionUrl || null,
    provider_name: providerName || null,
    provider_url: providerUrl || null,
    download_location: textAt(value, 'download_location') || null,
    source_ref: sourceRef,
    checked_at: textAt(value, 'checked_at') || null,
  };
};

export const displayableImageAssets = (value: unknown, maximum = 3): ImageAssetV3[] => {
  if (!Array.isArray(value) || maximum < 1) return [];
  const seenIds = new Set<string>();
  const images: ImageAssetV3[] = [];
  for (const item of value) {
    const image = toDisplayableImageAsset(item);
    if (!image || seenIds.has(image.image_id)) continue;
    if (image.image_id.startsWith('img_city_context_') || /非.+地点实景/.test(image.alt || '')) continue;
    seenIds.add(image.image_id);
    images.push(image);
    if (images.length >= maximum) break;
  }
  return images;
};

export const legacyUnsplashCoverAsset = (value: unknown, fallbackAlt: string): ImageAssetV3 | null => {
  if (!isRecord(value)) return null;
  const url = normalizedHttpsUrl(textAt(value, 'url'));
  const photographer = textAt(value, 'photographer_name');
  const photographerUrl = normalizedHttpsUrl(textAt(value, 'photographer_url'));
  const unsplashUrl = normalizedHttpsUrl(textAt(value, 'unsplash_url'));
  if (!url || !photographer || !photographerUrl || !unsplashUrl) return null;
  return {
    image_id: `legacy-unsplash-${url}`,
    url,
    alt: textAt(value, 'alt') || fallbackAlt,
    display_allowed: true,
    export_allowed: false,
    attribution_required: true,
    attribution_text: photographer,
    attribution_url: photographerUrl,
    provider_name: 'Unsplash',
    provider_url: unsplashUrl,
    download_location: textAt(value, 'download_location') || null,
    source_ref: textAt(value, 'source_reference_id') || 'legacy_unsplash_cover',
    checked_at: null,
  };
};

interface TravelImageAssetProps {
  image: ImageAssetV3;
  className?: string;
  imageLabel?: string;
  onImageError?: (imageId: string) => void;
}

export const TravelImageAsset = ({
  image,
  className = '',
  imageLabel = '旅行图片',
  onImageError,
}: TravelImageAssetProps) => {
  const [failed, setFailed] = useState(false);
  if (failed || !toDisplayableImageAsset(image)) return null;

  const alt = image.alt || imageLabel;
  return (
    <figure className={`travel-image-asset ${className}`.trim()} data-image-state="loading">
      <img
        src={displayImageUrl(image.url)}
        alt={alt}
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => {
          setFailed(true);
          onImageError?.(image.image_id);
        }}
      />
    </figure>
  );
};

interface TravelImageWithFallbackProps {
  images: ImageAssetV3[];
  className?: string;
  imageLabel?: string;
}

export const TravelImageWithFallback = ({
  images,
  className = '',
  imageLabel = '旅行图片',
}: TravelImageWithFallbackProps) => {
  const candidates = displayableImageAssets(images, 3);
  const signature = candidates.map((image) => image.image_id).join('|');
  const [failedImageIds, setFailedImageIds] = useState<string[]>([]);
  useEffect(() => setFailedImageIds([]), [signature]);
  const current = candidates.find((image) => !failedImageIds.includes(image.image_id));
  if (!current) return null;
  return (
    <TravelImageAsset
      key={current.image_id}
      image={current}
      className={className}
      imageLabel={imageLabel}
      onImageError={(imageId) => setFailedImageIds((values) => [...values, imageId])}
    />
  );
};

export default TravelImageAsset;
