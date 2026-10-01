"""候选排期的真实路线形状回归；交通响应均为本地夹具。"""
import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT.parent, ROOT.parents[2]):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from services.candidate_route_schedule_service import reschedule_inserted_candidate
from services.trip_edit_service import TripEditError


def fixture():
    document = json.loads((ROOT / 'tests/fixtures/trip_v3_domestic_3d.json').read_text(encoding='utf-8'))
    day = document['itinerary']['days'][0]
    template = deepcopy(day['activities'][0])
    day['anchors'] = []
    day['activities'] = []
    for index, (start, end) in enumerate([('09:00', '10:00'), ('10:00', '11:00'), ('14:00', '15:00')]):
        item = deepcopy(template)
        item.update(activity_id=f'candidate_route_{index}', order=index + 1, start_at=start,
                    end_at=end, fixed_time=False, duration_minutes=60, route_to_next=None)
        item['place']['opening_hours'] = '全天开放'
        day['activities'].append(item)
    document['map_guidance']['day_routes'][0] = {'day': 1, 'status': 'ready', 'legs': [
        leg('candidate_route_0', 'candidate_route_1', 32),
        leg('candidate_route_1', 'candidate_route_2', 20)]}
    return document


def leg(left, right, minutes):
    return {'from_id': left, 'to_id': right, 'duration_minutes': minutes, 'mode': 'transit',
            'provider': 'fixture', 'coordinate_system': 'WGS84', 'status': 'ready',
            'geometry': {'type': 'LineString', 'coordinates': [[120.1, 30.2], [120.2, 30.3]]}}


def run(document):
    diff = {'time_changes': []}
    reschedule_inserted_candidate(document, {'payload': {'activity_id': 'candidate_route_1'}}, diff)
    return diff


def test_real_commutes_shift_only_inserted_candidate():
    document = fixture()
    before = deepcopy(document['itinerary']['days'][0]['activities'])
    diff = run(document)
    activities = document['itinerary']['days'][0]['activities']
    assert activities[0] == before[0] and activities[2] == before[2]
    assert (activities[1]['start_at'], activities[1]['end_at']) == ('10:45', '11:45')
    assert diff['time_changes'][0]['after']['start_at'] == '10:45'


def test_searches_all_opening_windows_in_original_gap():
    document = fixture()
    candidate = document['itinerary']['days'][0]['activities'][1]
    candidate['place']['opening_hours'] = '09:00-11:00; 12:00-14:00'
    run(document)
    assert (candidate['start_at'], candidate['end_at']) == ('12:00', '13:00')


@pytest.mark.parametrize('failure', ['outgoing', 'last_entry', 'closed', 'missing_route'])
def test_infeasible_slot_rejected_without_mutating_activities(failure):
    document = fixture()
    activities = document['itinerary']['days'][0]['activities']
    routes = document['map_guidance']['day_routes'][0]['legs']
    if failure == 'outgoing':
        routes[1]['duration_minutes'] = 200
    elif failure == 'last_entry':
        activities[1]['place']['opening_hours'] = '09:00-18:00; 最晚入场10:30'
    elif failure == 'closed':
        activities[1]['place']['opening_hours'] = '全天不开放'
    else:
        routes[0]['status'] = 'unavailable'
    before = deepcopy(activities)
    with pytest.raises(TripEditError) as error:
        run(document)
    assert error.value.code == 'CANDIDATE_NO_FEASIBLE_SLOT'
    assert activities == before


def test_first_and_last_slot_include_arrival_and_departure_transfer():
    document = fixture()
    day = document['itinerary']['days'][0]
    candidate = day['activities'][1]
    day['activities'] = [candidate]
    day['anchors'] = [{'anchor_id': kind, 'kind': kind, 'place': deepcopy(candidate['place'])}
                      for kind in ('arrival_hub', 'departure_hub')]
    for section, field, time in [('outbound_transport', 'arrival_time', '10:10'),
                                  ('return_transport', 'departure_time', '12:45')]:
        document[section] = {'selected_option_id': 'selected', 'options': [
            {'option_id': 'selected', field: {'local_iso': f'2026-09-25T{time}:00', 'display_text': '时间'}}]}
    document['map_guidance']['day_routes'][0]['legs'] = [
        leg('arrival_hub', candidate['activity_id'], 35), leg(candidate['activity_id'], 'departure_hub', 45)]
    run(document)
    assert (candidate['start_at'], candidate['end_at']) == ('10:45', '11:45')
    document['map_guidance']['day_routes'][0]['legs'][1]['duration_minutes'] = 61
    with pytest.raises(TripEditError, match='目标空档'):
        run(document)


def test_boundary_transfer_sums_hotel_and_hub_legs():
    document = fixture()
    day = document['itinerary']['days'][0]
    candidate = day['activities'][1]
    day['activities'] = [candidate]
    kinds = ('arrival_hub', 'lodging_departure', 'lodging_return', 'departure_hub')
    day['anchors'] = [{'anchor_id': kind, 'kind': kind, 'place': deepcopy(candidate['place'])} for kind in kinds]
    document['outbound_transport']['selected_option_id'] = None
    document['return_transport']['selected_option_id'] = None
    document['map_guidance']['day_routes'][0]['legs'] = [
        leg('arrival_hub', 'lodging_departure', 50),
        leg('lodging_departure', candidate['activity_id'], 40),
        leg(candidate['activity_id'], 'lodging_return', 60),
        leg('lodging_return', 'departure_hub', 30)]
    run(document)
    assert candidate['start_at'] == '10:30'
    candidate['place']['opening_hours'] = '20:00-22:00'
    with pytest.raises(TripEditError):
        run(document)


@pytest.mark.parametrize('outgoing, expected_status', [(20, 200), (200, 422)])
def test_api_rebuilds_routes_before_insertion_and_rejects_without_losing_candidate(outgoing, expected_status):
    import httpx
    main = importlib.import_module('main')
    document = fixture()
    day = document['itinerary']['days'][0]
    candidate = day['activities'].pop(1)
    candidate.update(day=None, order=None, start_at=None, end_at=None)
    day['activities'][1]['order'] = 2
    document['candidate_pool'] = [candidate]
    document['map_guidance']['day_routes'][0]['legs'] = [leg('candidate_route_0', 'candidate_route_2', 20)]
    document['map_guidance']['formal_location_ids'] = [a['activity_id'] for d in document['itinerary']['days'] for a in d['activities']] + [a['anchor_id'] for d in document['itinerary']['days'] for a in d['anchors']]
    document['budget']['total_budget']['amount'] = 100000
    operation = {'operation_id': 'candidate_route_regression', 'plan_id': document['plan_id'],
                 'base_version': document['revision'], 'type': 'insert_candidate',
                 'payload': {'activity_id': candidate['activity_id'], 'target_day': 1, 'position': 1}}
    before = deepcopy(document)
    async def rebuild(updated, **kwargs):
        assert len(updated['itinerary']['days'][0]['activities']) == 3
        updated['map_guidance']['day_routes'][0] = {'day': 1, 'status': 'ready', 'legs': [
            leg('candidate_route_0', 'candidate_route_1', 32), leg('candidate_route_1', 'candidate_route_2', outgoing)]}
    async def request():
        with patch('services.formal_consistency_service.rebuild_formal_routes', side_effect=rebuild) as mocked:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                response = await client.post('/api/trip-edit', json={'document': document, 'operation': operation})
            assert mocked.call_count == 1, response.text
            return response
    response = asyncio.run(request())
    assert response.status_code == expected_status, response.text
    assert document == before
    if expected_status == 200:
        updated = response.json()['document']['itinerary']['days'][0]['activities']
        assert updated[1]['start_at'] == '10:45'
        assert response.json()['draft_validation']['can_apply']
    else:
        assert response.json()['detail']['code'] == 'CANDIDATE_NO_FEASIBLE_SLOT'
        assert 'document' not in response.json()
