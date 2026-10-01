import asyncio
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[4]))

import pytest
from schemas.trip_v3_models import TravelPlanDocumentV3
from services.formal_consistency_service import recalculate_formal_budget, refresh_schedule_validation, daily_route_nodes, schedule_issues, rebuild_formal_routes
from services.editable_place_service import remember_place
from services.trip_edit_service import apply_trip_edit, TripEditError
from services.trip_draft_validation_service import validate_trip_draft


def document():
    return json.loads((Path(__file__).parent / 'fixtures/trip_v3_domestic_3d.json').read_text(encoding='utf-8'))


def edit(doc, kind, **payload):
    return apply_trip_edit(doc, {'operation_id': 'qa-' + kind, 'plan_id': doc['plan_id'], 'base_version': doc['revision'], 'type': kind, 'payload': payload})


def test_budget_recomputed_after_removal_with_unknown_transport_and_lodging():
    doc = document()
    doc['intent']['travelers'] = {'adults': 2, 'children': 1, 'seniors': 0}
    activity = doc['itinerary']['days'][0]['activities'][0]
    for day in doc['itinerary']['days']:
        for item in day['activities']:
            item['estimated_cost'] = {'amount': 0, 'currency': 'CNY', 'source_status': 'official_reference'}
    activity.update(estimated_cost={'amount': 100, 'currency': 'JPY', 'cny_reference_amount': 5, 'exchange_rate_as_of': '2026-09-04', 'source_status': 'official_reference'},
                    estimated_cost_unit='per_traveler', official_traveler_prices={'child': {'amount': 50, 'currency': 'JPY', 'cny_reference_amount': 2.5, 'exchange_rate_as_of': '2026-09-04', 'source_status': 'official_reference'}})
    doc['outbound_transport']['selected_option_id'] = None
    doc['return_transport']['selected_option_id'] = None
    doc['lodging_plan']['planning_lodging_id'] = None
    recalculate_formal_budget(doc)
    assert doc['budget']['estimated_total']['amount'] == 12.5
    assert doc['budget']['unknown_cost_count'] >= 3
    child = next(item for item in doc['budget']['traveler_costs'] if item['traveler_type'] == 'child')
    assert child['estimated_total']['amount'] == 2.5
    assert child['pricing_status'] == 'official_discount_verified'
    doc['itinerary']['days'][0]['activities'].remove(activity)
    recalculate_formal_budget(doc)
    assert doc['budget']['estimated_total']['amount'] == 0


def test_full_pool_keeps_multiple_removed_places_with_opening_evidence():
    from services.formal_consistency_service import repair_initial_schedule
    doc = document()
    day = doc['itinerary']['days'][0]
    prototype = deepcopy(day['activities'][0])
    doc['itinerary']['days'] = [day]
    day['anchors'] = []
    doc['map_guidance']['day_routes'] = []
    doc['candidate_pool'] = []
    for index in range(15):
        candidate = deepcopy(prototype)
        candidate.update(activity_id=f'candidate-{index}', day=None, order=None)
        candidate['place']['opening_hours'] = None
        doc['candidate_pool'].append(candidate)
    day['activities'] = []
    for index in range(3):
        activity = deepcopy(prototype)
        activity.update(activity_id=f'closed-{index}', title=f'有营业证据景点{index}',
                        start_at='18:00', end_at='19:00', fixed_time=False, kind='attraction', meal_type=None)
        activity['place']['opening_hours'] = '09:00-17:00'
        day['activities'].append(activity)
    assert repair_initial_schedule(doc)
    assert len(doc['candidate_pool']) == 15
    assert {f'closed-{index}' for index in range(3)} <= {item['activity_id'] for item in doc['candidate_pool']}
    assert len(day['activities']) == 0


def test_closed_museum_and_real_route_time_block_apply():
    doc = document()
    day = doc['itinerary']['days'][0]
    museum = day['activities'][0]
    museum.update(start_at='20:15', end_at='21:15')
    museum['place']['opening_hours'] = '09:00-17:00'
    refresh_schedule_validation(doc)
    assert not doc['validation']['valid']
    assert 'OPENING_HOURS_CONFLICT' in {issue['code'] for issue in doc['validation']['issues']}
    assert not validate_trip_draft(doc)['can_apply']
    museum.update(start_at='10:00', end_at='11:00')
    other = deepcopy(museum)
    other.update(activity_id='other', title='另一景点', start_at='11:15', end_at='12:00', order=2)
    day['activities'] = [museum, other]
    doc['map_guidance']['day_routes'] = [{'day': 1, 'legs': [{'from_id': museum['activity_id'], 'to_id': 'other', 'status': 'ready', 'duration_minutes': 63}]}]
    assert any(issue['code'] == 'ROUTE_TIME_CONFLICT' for issue in schedule_issues(doc))


def test_notes_and_time_are_drafts_and_place_selection_is_required():
    doc = document()
    before = deepcopy(doc)
    result = edit(doc, 'update_trip_note', content='带好证件')
    assert doc == before
    assert any(note['content'] == '带好证件' for note in result['document']['notes'])
    result = edit(doc, 'update_day_note', day=1, content='提前预约')
    TravelPlanDocumentV3.model_validate(result['document'])
    assert result['document']['itinerary']['days'][0]['note_id']
    activity = doc['itinerary']['days'][0]['activities'][0]
    with pytest.raises(TripEditError):
        edit(doc, 'update_activity_time', activity_id=activity['activity_id'], start_at='10:07', end_at='11:00')
    with pytest.raises(TripEditError, match='地点选择'):
        edit(doc, 'replace_activity', activity_id=activity['activity_id'], selection_id='invented')
    place = deepcopy(activity['place'])
    place['name'] = '已检索的新地点'
    token = remember_place(doc['intent']['destination'], place)
    result = edit(doc, 'replace_activity', activity_id=activity['activity_id'], selection_id=token)
    updated = result['document']['itinerary']['days'][0]['activities'][0]
    assert updated['activity_id'] == activity['activity_id']
    assert updated['title'] == '已检索的新地点'
    assert updated['images'] == [] and updated['estimated_cost'] is None
    assert result['diff']['routes_revalidation_required']


def test_route_rebuild_includes_anchor_endpoints():
    doc = document()
    day = doc['itinerary']['days'][0]
    place = deepcopy(day['activities'][0]['place'])
    day['anchors'] = [{'anchor_id': 'arrival', 'kind': 'arrival_hub', 'day': 1, 'place': place, 'display_label': '抵达'},
                      {'anchor_id': 'hotel', 'kind': 'lodging_return', 'day': 1, 'place': place, 'display_label': '回酒店'}]
    ids = [item['activity_id'] for item in daily_route_nodes(day)]
    assert ids[0] == 'arrival' and ids[-1] == 'hotel'
    calls = []
    async def route(**kwargs):
        nodes = kwargs['activities']
        calls.append(nodes)
        return {'status': 'ready', 'legs': [{'from_activity_id': left['activity_id'], 'to_activity_id': right['activity_id'], 'mode': 'walking', 'status': 'ready', 'duration_minutes': 10} for left, right in zip(nodes, nodes[1:])]}
    with patch('services.route_geometry_service.build_day_route', side_effect=route):
        asyncio.run(rebuild_formal_routes(doc))
    legs = doc['map_guidance']['day_routes'][0]['legs']
    assert legs[0]['from_id'] == 'arrival' and legs[-1]['to_id'] == 'hotel'


def test_add_after_route_rebuild_keeps_untouched_day_anchor_ids():
    doc = document()
    for day in doc['itinerary']['days']:
        nodes = daily_route_nodes(day)
        doc['map_guidance']['day_routes'].append({'day': day['day'], 'status': 'unavailable', 'legs': [
            {'from_id': left['activity_id'], 'to_id': right['activity_id'], 'mode': 'transit', 'status': 'unavailable'}
            for left, right in zip(nodes, nodes[1:])
        ]})
    untouched = deepcopy(doc['itinerary']['days'][1]['anchors'])
    place = deepcopy(doc['itinerary']['days'][0]['activities'][0]['place'])
    token = remember_place(doc['intent']['destination'], place)
    result = edit(doc, 'add_activity', selection_id=token, day=1, start_at='14:00', end_at='15:00')
    TravelPlanDocumentV3.model_validate(result['document'])
    assert [item['anchor_id'] for item in result['document']['itinerary']['days'][1]['anchors']] == [item['anchor_id'] for item in untouched]


def test_one_day_meals_use_both_transport_bounds_and_candidate_budget_counts_people():
    from services.meal_schedule_service import meal_targets_by_day
    from services.candidate_schedule_service import _budget_allows, _opening_windows
    assert meal_targets_by_day(days=1, date_mode='fixed', outbound_arrival='16:00', return_departure='18:00') == {1: []}
    doc = document()
    doc['budget']['total_budget'] = {'amount': 100, 'currency': 'CNY'}
    doc['budget']['estimated_total'] = {'amount': 0, 'currency': 'CNY'}
    candidate = {'estimated_cost': {'amount': 60, 'currency': 'CNY'}, 'estimated_cost_unit': 'per_traveler'}
    assert not _budget_allows(doc, candidate)[0]
    assert _opening_windows('24/7') == [(0, 1440)]


def test_recorded_museum_hours_respect_date_weekday_and_last_admission():
    from services.candidate_schedule_service import _opening_windows, _last_admission, _closed_day, refresh_candidate_schedule_options
    hours = ('05/19 09:00-19:00开放 最晚进入18:30；01/01-05/18 周一 全天不开放；'
             '05/20-12/31 周一 全天不开放；01/01-05/18 周二-周日 09:00-17:00开放 最晚进入16:30；'
             '05/20-12/31 周二-周日 09:00-17:00开放 最晚进入16:30；'
             '清明节,端午节,中秋节,国庆节 09:00-17:00开放 最晚进入16:30；周一闭馆，遇法定节假日正常开放')
    assert _opening_windows(hours, '2026-09-05') == [(540, 1020)]
    assert _last_admission(hours, '2026-09-05') == 990
    assert _opening_windows(hours, '2026-05-19') == [(540, 1140)]
    assert _closed_day(hours, '2026-09-07')
    assert _opening_windows('周一 全天不开放') == []
    assert not _closed_day('Monday closed', '2026-09-08')
    assert _opening_windows('Mo-Fr 09:00-17:00; Sa-Su closed', '2026-09-09') == [(540, 1020)]
    assert _closed_day('Mo-Fr 09:00-17:00; Sa-Su closed', '2026-09-12')
    assert _closed_day('09:00-17:00；周一闭馆', '2026-09-07')
    doc = document()
    day = doc['itinerary']['days'][0]
    day['date'] = '2026-09-05'
    museum = day['activities'][0]
    museum.update(start_at='20:15', end_at='21:15')
    museum['place']['opening_hours'] = hours
    assert {'OPENING_HOURS_CONFLICT', 'LAST_ADMISSION_CONFLICT'} <= {item['code'] for item in schedule_issues(doc)}
    day['date'] = '2026-09-07'
    candidate = deepcopy(museum)
    candidate.update(activity_id='closed-candidate', estimated_cost=None, day=None, start_at=None, end_at=None)
    doc['candidate_pool'] = [candidate]
    refresh_candidate_schedule_options(doc)
    assert not any(option['day'] == 1 for option in candidate['insertion_options'])


def test_removed_closed_place_rechecks_the_new_adjacent_real_route():
    from services.formal_consistency_service import finalize_initial_schedule
    doc = document()
    day = doc['itinerary']['days'][0]
    doc['itinerary']['days'] = [day]
    day['anchors'] = []
    prototype = deepcopy(day['activities'][0])
    day['activities'] = []
    for identity, start, end, hours in [
        ('temple', '14:00', '15:00', '09:00-23:00'),
        ('closed-museum', '15:15', '16:15', '09:00-12:00'),
        ('dinner', '16:30', '17:30', '09:00-23:00'),
    ]:
        activity = deepcopy(prototype)
        activity.update(activity_id=identity, title=identity, start_at=start, end_at=end,
                        fixed_time=False, duration_minutes=60, order=len(day['activities']) + 1)
        activity['place'].update(poi_id='poi-' + identity, opening_hours=hours)
        day['activities'].append(activity)
    seen_edges = []
    async def route(**kwargs):
        nodes = kwargs['activities']
        legs = []
        for left, right in zip(nodes, nodes[1:]):
            edge = (left['activity_id'], right['activity_id'])
            seen_edges.append(edge)
            legs.append({'from_activity_id': edge[0], 'to_activity_id': edge[1],
                         'mode': 'walking', 'status': 'ready',
                         'duration_minutes': 122 if edge == ('temple', 'dinner') else 15})
        return {'status': 'ready', 'legs': legs}
    with patch('services.route_geometry_service.build_day_route', side_effect=route):
        asyncio.run(finalize_initial_schedule(doc))
    assert ('temple', 'dinner') in seen_edges
    assert [item['activity_id'] for item in day['activities']] == ['temple', 'dinner']
    assert day['activities'][1]['start_at'] == '17:15'
    assert not schedule_issues(doc)
    assert any(item['activity_id'] == 'closed-museum' for item in doc['candidate_pool'])


def test_long_commutes_move_optional_sights_instead_of_losing_required_meals():
    """成都实测的长通勤会把晚餐推过午夜；删景点后须重新核验直达路线。"""
    from services.formal_consistency_service import finalize_initial_schedule
    doc = document()
    day = doc['itinerary']['days'][0]
    doc['itinerary']['days'] = [day]
    day['anchors'] = []
    prototype = deepcopy(day['activities'][0])
    day['activities'] = []
    for identity, start, end, meal in [
        ('morning', '09:00', '10:15', None),
        ('remote-morning', '10:30', '11:30', None),
        ('lunch', '11:30', '13:00', 'lunch'),
        ('afternoon', '13:15', '14:45', None),
        ('remote-evening', '15:15', '16:45', None),
        ('dinner', '17:30', '19:00', 'dinner'),
    ]:
        activity = deepcopy(prototype)
        activity.update(activity_id=identity, title=identity, start_at=start, end_at=end,
                        fixed_time=False, kind='food' if meal else 'attraction', meal_type=meal,
                        order=len(day['activities']) + 1)
        activity['place'].update(poi_id='poi-' + identity, opening_hours=None)
        day['activities'].append(activity)
    # 四条长边取自真实失败样本；删点后新边由这个测试的路线提供器单独返回。
    durations = {('morning', 'remote-morning'): 133, ('remote-morning', 'lunch'): 125,
                 ('lunch', 'afternoon'): 58, ('afternoon', 'remote-evening'): 137,
                 ('remote-evening', 'dinner'): 122,
                 ('morning', 'lunch'): 50, ('afternoon', 'dinner'): 60}
    seen_edges = set()
    async def route(**kwargs):
        legs = []
        for left, right in zip(kwargs['activities'], kwargs['activities'][1:]):
            edge = (left['activity_id'], right['activity_id'])
            seen_edges.add(edge)
            legs.append({'from_activity_id': edge[0], 'to_activity_id': edge[1],
                         'mode': 'transit', 'status': 'ready', 'duration_minutes': durations.get(edge, 15)})
        return {'status': 'ready', 'legs': legs}
    with patch('services.route_geometry_service.build_day_route', side_effect=route):
        asyncio.run(finalize_initial_schedule(doc))
    assert [item['activity_id'] for item in day['activities']] == ['morning', 'lunch', 'afternoon', 'dinner']
    assert day['activities'][1]['start_at'] == '11:30'
    assert day['activities'][-1]['start_at'] == '17:30'
    assert {('morning', 'lunch'), ('afternoon', 'dinner')} <= seen_edges
    assert {'remote-morning', 'remote-evening'} <= {item['activity_id'] for item in doc['candidate_pool']}
    assert not schedule_issues(doc)
    assert doc['validation']['valid']


@pytest.mark.parametrize('closed', [False, True])
def test_impossible_meal_preserves_fixed_activity_and_fails_validation(closed):
    from services.formal_consistency_service import finalize_initial_schedule
    doc = document()
    day = doc['itinerary']['days'][0]
    doc['itinerary']['days'] = [day]
    day['anchors'] = []
    fixed = deepcopy(day['activities'][0])
    fixed.update(activity_id='fixed', title='预约景点', start_at='09:00', end_at='14:00', fixed_time=True)
    fixed['place']['opening_hours'] = None
    lunch = deepcopy(fixed)
    lunch.update(activity_id='lunch', title='午餐', start_at='11:30', end_at='13:00',
                 kind='food', meal_type='lunch', fixed_time=False)
    lunch['place'].update(poi_id='lunch-poi', opening_hours='全天不开放' if closed else None)
    day['activities'] = [fixed, lunch]
    async def route(**kwargs):
        nodes = kwargs['activities']
        return {'status': 'ready', 'legs': [
            {'from_activity_id': left['activity_id'], 'to_activity_id': right['activity_id'],
             'mode': 'transit', 'status': 'ready', 'duration_minutes': 90}
            for left, right in zip(nodes, nodes[1:])]}
    with patch('services.route_geometry_service.build_day_route', side_effect=route):
        asyncio.run(finalize_initial_schedule(doc))
    assert [item['activity_id'] for item in day['activities']] == ['fixed', 'lunch']
    assert (fixed['start_at'], fixed['end_at']) == ('09:00', '14:00')
    assert not doc['validation']['valid']
    assert 'INITIAL_MEAL_WINDOW_CONFLICT' in {issue['code'] for issue in doc['validation']['issues']}
    assert not any(item['activity_id'] == 'lunch' for item in doc['candidate_pool'])


@pytest.mark.parametrize('meal,arrival,start,end,expected', [
    ('lunch', '13:00', '11:30', '13:00', '13:30'),
    ('dinner', '19:00', '17:30', '19:00', '19:30'),
])
def test_arrival_meal_uses_actual_arrival_and_direct_transfer(meal, arrival, start, end, expected):
    from services.formal_consistency_service import finalize_initial_schedule
    doc = document()
    day = doc['itinerary']['days'][0]
    doc['itinerary']['days'] = [day]
    food = deepcopy(day['activities'][0])
    food.update(activity_id=meal, title=meal, kind='food', meal_type=meal,
                start_at=start, end_at=end, fixed_time=False)
    food['place']['opening_hours'] = '11:00-15:00; 17:00-23:00'
    day['activities'] = [food]
    arrival_anchor = next(item for item in day['anchors'] if item['kind'] == 'arrival_hub')
    day['anchors'] = [arrival_anchor]
    doc['outbound_transport']['options'][0]['arrival_time']['local_iso'] = '2026-08-01T' + arrival + ':00+08:00'
    seen_edges = set()
    async def route(**kwargs):
        legs = []
        for left, right in zip(kwargs['activities'], kwargs['activities'][1:]):
            edge = (left['activity_id'], right['activity_id'])
            seen_edges.add(edge)
            legs.append({'from_activity_id': edge[0], 'to_activity_id': edge[1],
                         'mode': 'transit', 'status': 'ready', 'duration_minutes': 30})
        return {'status': 'ready', 'legs': legs}
    with patch('services.route_geometry_service.build_day_route', side_effect=route):
        asyncio.run(finalize_initial_schedule(doc))
    assert (arrival_anchor['anchor_id'], meal) in seen_edges
    assert [item['activity_id'] for item in day['activities']] == [meal]
    assert food['start_at'] == expected
    assert not schedule_issues(doc)
    assert doc['validation']['valid']
