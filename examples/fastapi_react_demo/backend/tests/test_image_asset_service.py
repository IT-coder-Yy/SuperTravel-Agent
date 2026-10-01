import sys
import unittest
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from backend.schemas.trip_v3_models import ImageAssetV3
from backend.services.image_asset_service import build_external_image_asset, build_unsplash_cover_asset, displayable_images
from backend.services.unsplash_image_proxy_service import is_allowed_unsplash_image_url
from backend.services.wikimedia_image_service import (
    fetch_wikipedia_place_coordinates,
    fetch_wikimedia_activity_image,
    fetch_wikimedia_activity_images,
)


class ImageAssetServiceTests(unittest.TestCase):
    def tearDown(self):
        fetch_wikimedia_activity_image.cache_clear()

    def test_unsplash_proxy_accepts_only_the_cdn_host_over_https(self):
        self.assertTrue(is_allowed_unsplash_image_url("https://images.unsplash.com/photo-123?auto=format"))
        self.assertFalse(is_allowed_unsplash_image_url("http://images.unsplash.com/photo-123"))
        self.assertFalse(is_allowed_unsplash_image_url("https://api.unsplash.com/photos/123"))
        self.assertFalse(is_allowed_unsplash_image_url("https://images.unsplash.com.evil.example/photo-123"))

    def test_unsplash_cover_is_web_only_and_carries_complete_attribution(self):
        asset = build_unsplash_cover_asset(
            image_id="img_hangzhou_cover",
            url="https://images.unsplash.com/photo-123",
            alt="杭州西湖",
            photographer_name="摄影师甲",
            photographer_url="https://unsplash.com/@photographer-a",
            unsplash_url="https://unsplash.com/photos/example",
            download_location="https://api.unsplash.com/photos/example/download",
            source_ref="source_unsplash_cover",
            checked_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        self.assertIsNotNone(asset)
        assert asset is not None
        self.assertTrue(asset.display_allowed)
        self.assertFalse(asset.export_allowed)
        self.assertTrue(asset.attribution_required)
        self.assertEqual("Unsplash", asset.provider_name)
        self.assertEqual("source_unsplash_cover", asset.source_ref)

    def test_unsplash_cover_normalizes_unescaped_referral_parameters(self):
        asset = build_unsplash_cover_asset(
            image_id="img_encoded_referral",
            url="https://images.unsplash.com/photo-123",
            alt="杭州西湖",
            photographer_name="摄影师甲",
            photographer_url="https://unsplash.com/@photographer?utm_source=My Travel Agent",
            unsplash_url="https://unsplash.com/photos/example?utm_source=My Travel Agent",
            download_location=None,
            source_ref="source_unsplash_encoded",
            checked_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        )

        assert asset is not None
        self.assertIn("utm_source=My%20Travel%20Agent", asset.attribution_url or "")
        self.assertIn("utm_source=My%20Travel%20Agent", asset.provider_url or "")

    def test_private_use_external_image_is_displayable_and_exportable_with_source_metadata(self):
        asset = build_external_image_asset(
            image_id="img_amap_west_lake",
            url="https://poi.example.com/west-lake.jpg",
            alt="西湖实景",
            provider_name="高德地图 POI 图片",
            provider_url="https://www.amap.com/",
            source_ref="source_amap_west_lake",
            checked_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )

        self.assertIsNotNone(asset)
        assert asset is not None
        self.assertTrue(asset.display_allowed)
        self.assertTrue(asset.export_allowed)
        self.assertFalse(asset.attribution_required)
        self.assertEqual(asset.provider_name, "高德地图 POI 图片")
        self.assertEqual(displayable_images([asset]), [asset])

    def test_unknown_or_incomplete_images_are_not_promoted_to_display_assets(self):
        self.assertIsNone(build_unsplash_cover_asset(
            image_id="img_unknown", url="https://example.com/photo.jpg", alt="未知图片",
            photographer_name="摄影师", photographer_url="https://example.com/p", unsplash_url="https://example.com/photo",
            download_location=None, source_ref="source_unknown", checked_at=datetime.now(timezone.utc),
        ))
        incomplete = ImageAssetV3(
            image_id="img_incomplete", url="https://images.unsplash.com/photo-456", display_allowed=True,
            export_allowed=False, attribution_required=True, attribution_text="摄影师", source_ref="source_unsplash",
        )
        self.assertEqual([], displayable_images([incomplete]))
        self.assertIsNone(build_unsplash_cover_asset(
            image_id="img_missing_profile", url="https://images.unsplash.com/photo-789", alt="未知图片",
            photographer_name="摄影师", photographer_url="https://unsplash.com", unsplash_url="https://unsplash.com",
            download_location=None, source_ref="source_unsplash", checked_at=datetime.now(timezone.utc),
        ))

    def test_displayable_images_keeps_order_deduplicates_and_limits_to_three(self):
        base = build_unsplash_cover_asset(
            image_id="img_1", url="https://images.unsplash.com/photo-1", alt="图 1",
            photographer_name="摄影师", photographer_url="https://unsplash.com/@photographer",
            unsplash_url="https://unsplash.com/photos/one", download_location=None,
            source_ref="source_unsplash", checked_at=datetime.now(timezone.utc),
        )
        assert base is not None
        images = [base, base, base.model_copy(update={"image_id": "img_2"}), base.model_copy(update={"image_id": "img_3"}), base.model_copy(update={"image_id": "img_4"})]
        self.assertEqual(["img_1", "img_2", "img_3"], [image.image_id for image in displayable_images(images)])

    @patch.dict(os.environ, {"WIKIMEDIA_COORDINATES_ENABLED": "true"})
    @patch("backend.services.wikimedia_image_service.requests.get")
    def test_wikipedia_coordinates_follow_official_script_conversion(self, mocked_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "query": {
                "converted": [{"from": "东京晴空塔", "to": "東京晴空塔"}],
                "pages": [{
                    "pageid": 442838,
                    "title": "東京晴空塔",
                    "coordinates": [{"lat": 35.71, "lon": 139.81}],
                    "fullurl": "https://zh.wikipedia.org/wiki/東京晴空塔",
                }],
            }
        }
        mocked_get.return_value = response

        coordinates = fetch_wikipedia_place_coordinates([("东京晴空塔", "东京")])

        self.assertEqual("wikipedia_442838", coordinates["东京晴空塔"]["poi_id"])
        self.assertEqual(35.71, coordinates["东京晴空塔"]["lat"])
        self.assertEqual(1, mocked_get.call_args.kwargs["params"]["converttitles"])

    @patch.dict(os.environ, {"WIKIMEDIA_COORDINATES_ENABLED": "true"})
    @patch("backend.services.wikimedia_image_service.requests.get")
    def test_wikipedia_coordinates_fall_back_to_exact_wikidata_p625(self, mocked_get):
        article_response = Mock()
        article_response.raise_for_status.return_value = None
        article_response.json.return_value = {
            "query": {
                "converted": [{"from": "浅草寺", "to": "淺草寺"}],
                "pages": [{
                    "pageid": 397807,
                    "title": "淺草寺",
                    "pageprops": {"wikibase_item": "Q615183"},
                    "fullurl": "https://zh.wikipedia.org/wiki/淺草寺",
                }],
            }
        }
        entity_response = Mock()
        entity_response.raise_for_status.return_value = None
        entity_response.json.return_value = {
            "entities": {"Q615183": {"claims": {"P625": [{
                "mainsnak": {"datavalue": {"value": {
                    "latitude": 35.71456, "longitude": 139.79664,
                }}},
            }]}}}
        }
        mocked_get.side_effect = [article_response, entity_response]

        coordinates = fetch_wikipedia_place_coordinates([("浅草寺", "东京")])

        self.assertEqual("wikidata_Q615183", coordinates["浅草寺"]["poi_id"])
        self.assertEqual("wikidata_p625", coordinates["浅草寺"]["coordinate_source"])
        self.assertEqual("https://www.wikidata.org/wiki/Q615183", coordinates["浅草寺"]["source_url"])

    @patch.dict(os.environ, {"WIKIMEDIA_IMAGES_ENABLED": "true"})
    @patch("backend.services.wikimedia_image_service.requests.get")
    def test_wikimedia_activity_image_keeps_licence_and_attribution(self, mocked_get):
        article_response = Mock()
        article_response.raise_for_status.return_value = None
        article_response.json.return_value = {
            "query": {"pages": [{"title": "西湖", "pageimage": "West_Lake.jpg"}]}
        }
        image_response = Mock()
        image_response.raise_for_status.return_value = None
        image_response.json.return_value = {
            "query": {"pages": [{"imageinfo": [{
                "url": "https://upload.wikimedia.org/example/West_Lake.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:West_Lake.jpg",
                "mime": "image/jpeg",
                "width": 1600,
                "height": 1200,
                "extmetadata": {
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "Artist": {"value": "<b>摄影师甲</b>"},
                },
            }]}]}
        }
        mocked_get.side_effect = [article_response, image_response]

        asset = fetch_wikimedia_activity_image("西湖", "杭州")

        self.assertTrue(asset["display_allowed"])
        self.assertFalse(asset["export_allowed"])
        self.assertEqual("摄影师甲", asset["attribution_text"])
        self.assertEqual("Wikimedia Commons", asset["provider_name"])

    @patch.dict(os.environ, {"WIKIMEDIA_IMAGES_ENABLED": "true"})
    @patch("backend.services.wikimedia_image_service.requests.get")
    def test_wikimedia_batch_accepts_redirect_title_containing_place_name(self, mocked_get):
        article_response = Mock()
        article_response.raise_for_status.return_value = None
        article_response.json.return_value = {
            "query": {"pages": [{
                "title": "杭州西湖风景名胜区管理委员会",
                "pageimage": "West_Lake.jpg",
                "fullurl": "https://zh.wikipedia.org/wiki/杭州西湖风景名胜区管理委员会",
            }]}
        }
        image_response = Mock()
        image_response.raise_for_status.return_value = None
        image_response.json.return_value = {
            "query": {"pages": [{
                "title": "File:West Lake.jpg",
                "imageinfo": [{
                    "url": "https://upload.wikimedia.org/example/West_Lake.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:West_Lake.jpg",
                    "mime": "image/jpeg",
                    "width": 1600,
                    "height": 1200,
                    "extmetadata": {
                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "Artist": {"value": "摄影师乙"},
                    },
                }],
            }]}
        }
        mocked_get.side_effect = [article_response, image_response]

        assets = fetch_wikimedia_activity_images(
            [("西湖风景名胜区", "杭州")],
            timeout_seconds=15.0,
        )

        self.assertIn("西湖风景名胜区", assets)
        self.assertEqual(15.0, mocked_get.call_args_list[0].kwargs["timeout"])
        self.assertEqual(15.0, mocked_get.call_args_list[1].kwargs["timeout"])

    @patch.dict(os.environ, {"WIKIMEDIA_IMAGES_ENABLED": "true"})
    @patch("backend.services.wikimedia_image_service.requests.get")
    def test_wikimedia_batch_uses_conservative_place_title_alias(self, mocked_get):
        article_response = Mock()
        article_response.raise_for_status.return_value = None
        article_response.json.return_value = {
            "query": {"pages": [{
                "title": "西湖",
                "pageimage": "West_Lake.jpg",
                "fullurl": "https://zh.wikipedia.org/wiki/西湖",
            }]}
        }
        image_response = Mock()
        image_response.raise_for_status.return_value = None
        image_response.json.return_value = {
            "query": {"pages": [{
                "title": "File:West Lake.jpg",
                "imageinfo": [{
                    "url": "https://upload.wikimedia.org/example/West_Lake.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:West_Lake.jpg",
                    "mime": "image/jpeg",
                    "extmetadata": {
                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "Artist": {"value": "摄影师丙"},
                    },
                }],
            }]}
        }
        mocked_get.side_effect = [article_response, image_response]

        assets = fetch_wikimedia_activity_images([("西湖风景名胜区", "杭州")])

        self.assertIn("西湖风景名胜区", assets)
        requested_titles = mocked_get.call_args_list[0].kwargs["params"]["titles"]
        self.assertIn("西湖风景名胜区", requested_titles)
        self.assertIn("西湖", requested_titles)


if __name__ == "__main__":
    unittest.main()
