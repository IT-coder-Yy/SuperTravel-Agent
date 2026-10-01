"""图片说明必须来自同一张图片的来源页，不能信任搜索引擎生成的说明。"""
import sys
from pathlib import Path
from unittest.mock import patch
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from services import chat_service as chat
from services.image_source_service import image_descriptions_from_html, fetch_image_source_descriptions


def test_only_matching_img_alt_is_evidence():
    html = '''<title>涩谷景点</title><img src="/wrong.jpg" alt="涩谷街景">
    <img src="/actual.jpg" alt="浅草寺建筑"><p>涩谷旅游</p>'''
    assert image_descriptions_from_html(html, 'https://travel.example/guide',
                                        'https://travel.example/actual.jpg') == ['浅草寺建筑']


def test_lazy_relative_and_srcset_images_keep_their_own_alt():
    html = '''<img data-src="/photo.jpg?a=1&amp;b=2" alt="西湖湖岸">
    <img srcset="/small.jpg 1x, /large.jpg 2x" alt="西湖全景">'''
    assert image_descriptions_from_html(html, 'https://travel.example/article',
                                        'https://travel.example/photo.jpg?a=1&b=2') == ['西湖湖岸']
    assert image_descriptions_from_html(html, 'https://travel.example/article',
                                        'https://travel.example/large.jpg') == ['西湖全景']


def test_title_and_missing_alt_do_not_identify_image():
    assert image_descriptions_from_html('<img src="/a.jpg" title="西湖">',
        'https://travel.example/guide', 'https://travel.example/a.jpg') == []


def test_search_without_source_does_not_invent_cdn_root():
    candidate = chat._normalize_image_search_candidates({'images': [{
        'url': 'https://cdn.example/photo.jpg', 'description': '涩谷寺庙',
    }]}, 'tavily_search')[0]
    assert candidate['source_url'] == ''


def _search(payload, descriptions):
    with patch.object(chat, '_first_available_tool_name', side_effect=['tavily_search', None]), \
         patch.object(chat, '_run_tool_with_arg_candidates', return_value=payload), \
         patch.object(chat, 'fetch_image_source_descriptions', side_effect=descriptions):
        return chat._search_activity_image_candidates(place_name='涩谷', city='东京',
            category='景点', tool_manager=object(), message_history=[], session_id='image-evidence-test')


def test_generated_description_cannot_override_source_image_alt():
    payload = {'images': [{'url': 'https://cdn.example/a.jpg', 'description': '涩谷寺庙',
                          'source_url': 'https://travel.example/article'}]}
    assert _search(payload, [['浅草寺建筑群']]) == []
    assert _search(payload, [[]]) == []


def test_verified_source_alt_is_preserved_in_asset_and_reference():
    payload = {'images': [{'url': 'https://cdn.example/a.jpg', 'description': '涩谷攻略',
                          'source_url': 'https://travel.example/article'}]}
    rows = _search(payload, [['涩谷十字路口夜景']])
    asset, reference = chat._external_activity_image(place_name='涩谷',
        image_url=rows[0]['image_url'], provider_name=rows[0]['provider'],
        provider_url=rows[0]['source_url'], image_description=rows[0]['title'])
    assert asset['alt'] == '涩谷十字路口夜景'
    assert '涩谷十字路口夜景' in reference['snippet']


def test_missing_or_root_source_never_requests_page():
    with patch('httpx.Client.stream') as request:
        assert fetch_image_source_descriptions('', 'https://cdn.example/a.jpg') == []
        assert fetch_image_source_descriptions('https://cdn.example/', 'https://cdn.example/a.jpg') == []
        request.assert_not_called()


def test_private_source_is_rejected_before_request():
    with patch('services.pdf_export_service._is_public_host', return_value=False), \
         patch('httpx.Client.stream') as request:
        assert fetch_image_source_descriptions('https://127.0.0.1/article',
                                              'https://cdn.example/a.jpg') == []
        request.assert_not_called()


def test_redirect_to_private_source_is_rejected():
    visited = []
    def response(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={'location': 'https://127.0.0.1/private'})
    client = httpx.Client(transport=httpx.MockTransport(response))
    with patch('services.image_source_service.httpx.Client', return_value=client), \
         patch('services.pdf_export_service._is_public_host', side_effect=lambda host: host != '127.0.0.1'):
        assert fetch_image_source_descriptions('https://travel.example/article',
                                              'https://cdn.example/a.jpg') == []
    assert visited == ['https://travel.example/article']


def test_source_page_without_requested_image_is_rejected():
    def response(request):
        return httpx.Response(200, headers={'content-type': 'text/html'},
            text='<title>涩谷</title><img src="/other.jpg" alt="涩谷街景">')
    client = httpx.Client(transport=httpx.MockTransport(response))
    with patch('services.image_source_service.httpx.Client', return_value=client), \
         patch('services.pdf_export_service._is_public_host', return_value=True):
        assert fetch_image_source_descriptions('https://travel.example/article',
                                              'https://cdn.example/a.jpg') == []
