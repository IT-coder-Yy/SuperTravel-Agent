"""拖动保留活动并使用重算后的路段；所有路段均为本地回归夹具。"""
import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT.parent, ROOT.parents[2]):
    if str(path) not in sys.path:
        sys.path.append(str(path))
from services.trip_edit_service import reschedule_moved_activity
from services.formal_consistency_service import refresh_schedule_validation
from services.trip_draft_validation_service import validate_trip_draft


def document_with_activities(times):
    document = json.loads((ROOT / 'tests/fixtures/trip_v3_domestic_3d.json').read_text(encoding='utf-8'))
    day = document['itinerary']['days'][0]
    template = deepcopy(day['activities'][0])
    day['anchors'] = []
    day['activities'] = []
    for index, (start, end) in enumerate(times):
        item = deepcopy(template)
        item.update(activity_id=f'move_{index}', order=index + 1, start_at=start, end_at=end,
                    fixed_time=False, duration_minutes=60, route_to_next=None)
        item['place']['opening_hours'] = '全天开放'
        day['activities'].append(item)
    document['map_guidance']['formal_location_ids'] = [item['activity_id'] for d in document['itinerary']['days'] for item in d['activities']] + [a['anchor_id'] for d in document['itinerary']['days'] for a in d['anchors']]
    return document


def route(day, left, right, minutes):
    return {'day': day, 'status': 'ready', 'legs': [{
        'from_id': left, 'to_id': right, 'duration_minutes': minutes, 'mode': 'transit',
        'provider': 'fixture', 'coordinate_system': 'WGS84', 'status': 'ready',
        'geometry': {'type': 'LineString', 'coordinates': [[120.1, 30.2], [120.2, 30.3]]},
    }]}


def reschedule(document, activity='move_1', affected=(1,)):
    diff = {'affected_days': list(affected), 'time_changes': []}
    reschedule_moved_activity(document, {'payload': {'activity_id': activity, 'target_day': affected[-1]}}, diff)
    refresh_schedule_validation(document)
    return diff


def test_drag_uses_new_real_route_and_keeps_every_duration():
    document = document_with_activities([('10:00', '11:00'), ('09:00', '10:00')])
    document['map_guidance']['day_routes'][0] = route(1, 'move_0', 'move_1', 122)
    diff = reschedule(document)
    activities = document['itinerary']['days'][0]['activities']
    assert [a['activity_id'] for a in activities] == ['move_0', 'move_1']
    assert (activities[1]['start_at'], activities[1]['end_at']) == ('13:15', '14:15')
    assert diff['time_changes'][0]['after'] == {'start_at': '13:15', 'end_at': '14:15'}
    assert validate_trip_draft(document)['can_apply']


def test_drag_preserves_fixed_time_and_blocks_infeasible_commute():
    document = document_with_activities([('10:00', '11:00'), ('11:15', '12:15')])
    document['itinerary']['days'][0]['activities'][1]['fixed_time'] = True
    document['map_guidance']['day_routes'][0] = route(1, 'move_0', 'move_1', 122)
    reschedule(document)
    assert document['itinerary']['days'][0]['activities'][1]['start_at'] == '11:15'
    validation = validate_trip_draft(document)
    assert not validation['can_apply']
    assert 'ROUTE_TIME_CONFLICT' in {issue['code'] for issue in validation['hard_errors']}


def test_cross_day_drag_reschedules_source_day_new_neighbours():
    document = document_with_activities([('09:00', '10:00'), ('10:15', '11:15')])
    document['map_guidance']['day_routes'][0] = route(1, 'move_0', 'move_1', 95)
    second_activity = document['itinerary']['days'][1]['activities'][0]['activity_id']
    reschedule(document, second_activity, (1, 2))
    assert document['itinerary']['days'][0]['activities'][1]['start_at'] == '11:45'


def test_drag_keeps_closed_or_overflowing_activities_in_blocked_draft():
    for times, hours in [([('21:00', '22:00'), ('22:15', '23:15')], '全天开放'),
                         ([('10:00', '11:00'), ('11:15', '12:15')], '09:00-12:00')]:
        document = document_with_activities(times)
        document['itinerary']['days'][0]['activities'][1]['place']['opening_hours'] = hours
        document['map_guidance']['day_routes'][0] = route(1, 'move_0', 'move_1', 122)
        reschedule(document)
        assert len(document['itinerary']['days'][0]['activities']) == 2
        assert not validate_trip_draft(document)['can_apply']


def test_drag_api_rebuilds_routes_before_rescheduling():
    main = importlib.import_module('main')
    import httpx
    document = document_with_activities([('09:00', '10:00'), ('10:15', '11:15')])
    operation = {'operation_id': 'move_route_regression', 'plan_id': document['plan_id'],
                 'base_version': document['revision'], 'type': 'move_activity',
                 'payload': {'activity_id': 'move_1', 'target_day': 1, 'position': 0}}
    async def rebuild(updated, **kwargs):
        day = updated['itinerary']['days'][0]
        assert [a['activity_id'] for a in day['activities']] == ['move_1', 'move_0']
        updated['map_guidance']['day_routes'][0] = route(1, 'move_1', 'move_0', 122)
    async def run():
        with patch('services.formal_consistency_service.rebuild_formal_routes', side_effect=rebuild):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                return await client.post('/api/trip-edit', json={'document': document, 'operation': operation})
    response = asyncio.run(run())
    assert response.status_code == 200, response.text
    payload = response.json()
    activities = payload['document']['itinerary']['days'][0]['activities']
    assert [(a['start_at'], a['end_at']) for a in activities] == [('09:00', '10:00'), ('12:15', '13:15')]
    assert payload['draft_validation']['can_apply']


def test_conflicting_draft_can_be_edited_but_cannot_become_formal():
    import httpx
    import pytest
    from schemas.trip_v3_models import TravelPlanDocumentV3
    main = importlib.import_module('main')
    document = document_with_activities([('10:00', '11:00'), ('11:15', '12:15')])
    document['itinerary']['days'][0]['activities'][1]['fixed_time'] = True
    async def rebuild(updated, **kwargs):
        updated['map_guidance']['day_routes'][0] = route(1, 'move_0', 'move_1', 122)
    async def run():
        with patch('services.formal_consistency_service.rebuild_formal_routes', side_effect=rebuild):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                operation = {'operation_id': 'blocked-move', 'plan_id': document['plan_id'],
                             'base_version': document['revision'], 'type': 'move_activity',
                             'payload': {'activity_id': 'move_1', 'target_day': 1, 'position': 1}}
                response = await client.post('/api/trip-edit', json={'document': document, 'operation': operation})
                assert response.status_code == 200, response.text
                blocked = response.json()
                assert not blocked['draft_validation']['can_apply']
                assert 'ROUTE_TIME_CONFLICT' in {item['code'] for item in blocked['draft_validation']['hard_errors']}
                with pytest.raises(ValueError, match='必须通过业务校验'):
                    TravelPlanDocumentV3.model_validate(blocked['document'])
                operation.update(operation_id='correct-time', base_version=blocked['document']['revision'],
                                 type='update_activity_time', payload={'activity_id': 'move_1', 'start_at': '13:15', 'end_at': '14:15'})
                corrected = await client.post('/api/trip-edit', json={'document': blocked['document'], 'operation': operation})
                assert corrected.status_code == 200, corrected.text
                assert corrected.json()['draft_validation']['can_apply']
                TravelPlanDocumentV3.model_validate(corrected.json()['document'])
    asyncio.run(run())
