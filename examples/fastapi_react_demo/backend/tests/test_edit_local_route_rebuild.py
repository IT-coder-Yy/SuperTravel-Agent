"""局部编辑保留其他日真实路线；provider 为本地回归替身，不算联网证据。"""
import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT.parent, ROOT.parents[2]):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from services import route_geometry_service as geometry
from services.formal_consistency_service import daily_route_nodes, rebuild_formal_routes
from services.trip_edit_service import apply_trip_edit, _v3_refresh_anchors


@pytest.fixture
def routing(monkeypatch):
    async def route(origin, destination, mode):
        return {
            'provider': 'local-test', 'coordinate_system': 'BD09LL',
            'geometry': {'type': 'LineString', 'coordinates': [origin, destination]},
            'duration_minutes': 1, 'distance_meters': 10,
        }
    provider = SimpleNamespace(name='local-test', coordinate_system='BD09LL', route=AsyncMock(side_effect=route))
    monkeypatch.setattr(geometry, 'BaiduRouteProvider', lambda **kwargs: provider)
    monkeypatch.setattr(geometry, '_ROUTE_CACHE', {})
    monkeypatch.setattr(geometry, '_LEG_CACHE', {})
    doc = json.loads((ROOT / 'tests/fixtures/trip_v3_domestic_3d.json').read_text(encoding='utf-8'))
    _v3_refresh_anchors(doc)
    asyncio.run(rebuild_formal_routes(doc))
    # 冷缓存确保“不调用”来自正式路线复用，而不是进程内 provider 缓存。
    geometry._ROUTE_CACHE.clear()
    geometry._LEG_CACHE.clear()
    provider.route.reset_mock()
    return doc, provider


def edges(day):
    nodes = daily_route_nodes(day)
    return [(left['activity_id'], right['activity_id']) for left, right in zip(nodes, nodes[1:])]


def test_local_edit_reuses_other_days_with_cold_provider_cache(routing):
    doc, provider = routing
    untouched = deepcopy(doc['map_guidance']['day_routes'][1:])
    day = doc['itinerary']['days'][0]
    added = deepcopy(day['activities'][0])
    added.update(activity_id='new-activity', order=2)
    added['place']['coordinates']['longitude'] += .03
    day['activities'].append(added)
    asyncio.run(rebuild_formal_routes(doc, affected_days=[1]))
    assert provider.route.await_count == len(edges(day))
    assert doc['map_guidance']['day_routes'][1:] == untouched
    assert [(leg['from_id'], leg['to_id']) for leg in doc['map_guidance']['day_routes'][0]['legs']] == edges(day)
    assert doc['map_guidance']['status'] == 'ready'
    assert '3/3' in doc['map_guidance']['status_reason']
    assert 'unknown_cost_count' in doc['budget']


@pytest.mark.parametrize('problem', ['missing', 'wrong_node', 'missing_edge'])
def test_unaffected_day_with_incomplete_or_mismatched_route_is_rebuilt(routing, problem):
    doc, provider = routing
    route = doc['map_guidance']['day_routes'][1]
    if problem == 'missing':
        doc['map_guidance']['day_routes'].remove(route)
    elif problem == 'wrong_node':
        route['legs'][0]['from_id'] = 'stale-anchor'
    else:
        route['legs'].pop()
    asyncio.run(rebuild_formal_routes(doc, affected_days=[]))
    assert provider.route.await_count == len(edges(doc['itinerary']['days'][1]))
    assert [(leg['from_id'], leg['to_id']) for leg in doc['map_guidance']['day_routes'][1]['legs']] == edges(doc['itinerary']['days'][1])


def test_initial_generation_still_rebuilds_every_day(routing):
    doc, provider = routing
    asyncio.run(rebuild_formal_routes(doc))
    assert provider.route.await_count == sum(len(edges(day)) for day in doc['itinerary']['days'])


def test_cross_day_move_marks_both_days_and_calls_new_edges(routing):
    doc, provider = routing
    doc['itinerary']['days'][1]['activities'][0].update(start_at='13:00', end_at='15:00')
    operation = {'operation_id': 'cross-day-local-route', 'plan_id': doc['plan_id'],
                 'base_version': doc['revision'], 'type': 'move_activity',
                 'payload': {'activity_id': 'act_hz_2', 'target_day': 1, 'position': 1}}
    result = apply_trip_edit(doc, operation)
    assert result['diff']['affected_days'] == [1, 2]
    updated = result['document']
    untouched = deepcopy(updated['map_guidance']['day_routes'][2])
    asyncio.run(rebuild_formal_routes(updated, affected_days=result['diff']['affected_days']))
    assert provider.route.await_count == sum(len(edges(day)) for day in updated['itinerary']['days'][:2])
    assert updated['map_guidance']['day_routes'][2] == untouched
    for day, route in zip(updated['itinerary']['days'][:2], updated['map_guidance']['day_routes'][:2]):
        assert [(leg['from_id'], leg['to_id']) for leg in route['legs']] == edges(day)


def test_api_passes_affected_days_to_route_rebuild(routing, monkeypatch):
    doc, _ = routing
    main = importlib.import_module('main')
    import httpx
    from services import formal_consistency_service as consistency
    real_rebuild = consistency.rebuild_formal_routes
    rebuild = AsyncMock(side_effect=real_rebuild)
    monkeypatch.setattr(consistency, 'rebuild_formal_routes', rebuild)
    operation = {'operation_id': 'local-route-api', 'plan_id': doc['plan_id'],
                 'base_version': doc['revision'], 'type': 'update_activity_time',
                 'payload': {'activity_id': 'act_hz_2', 'start_at': '09:45', 'end_at': '11:45'}}
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            return await client.post('/api/trip-edit', json={'document': doc, 'operation': operation})
    response = asyncio.run(request())
    assert response.status_code == 200, response.text
    assert rebuild.await_args.kwargs['affected_days'] == [2]
