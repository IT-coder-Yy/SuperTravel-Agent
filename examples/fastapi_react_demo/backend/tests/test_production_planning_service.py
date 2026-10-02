import asyncio
import pytest
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[4]))

from schemas.trip_models import TripIntent
from schemas.trip_v3_models import AgentStagePayload, TravelPlanDocumentV3
from services.production_planning_service import stream_production_plan
from services.planning_orchestrator import PlanningTaskGraph, PlanningTask
from backend.tests.test_trip_v3_contract import build_legacy_v2_document


@pytest.mark.parametrize('retry_verification', [False, True])
def test_production_graph_executes_specialists_and_retries_only_failed_research(retry_verification):
    legacy = build_legacy_v2_document()
    legacy['itinerary']['days'][0]['activities'][0]['estimated_cost'] = 40
    template = legacy['itinerary']['days'][0]['activities'][0]
    for meal, start, end in [('lunch', '12:00', '13:00'), ('dinner', '18:00', '19:00')]:
        activity = deepcopy(template)
        activity.update(activity_id='meal-' + meal, title=meal, activity_type='food', meal_type=meal, start_time=start, end_time=end, estimated_cost=0)
        activity['place'].update(poi_id='poi-' + meal, name=meal, category='food')
        legacy['itinerary']['days'][0]['activities'].append(activity)
        legacy['map_guidance']['location_ids'].append('poi-' + meal)
    calls = Counter()
    def search(**kwargs):
        role = kwargs['research_role']
        calls[role] += 1
        if role == 'research' and calls[role] == 1:
            raise RuntimeError('临时失败')
        place = {'name': '西湖', 'poi_id': 'test-west-lake', 'lat': 30.25, 'lng': 120.16, 'category': '景点'}
        return [place], '测试地图', '', []
    def build(*_args):
        calls['build_document'] += 1
        return deepcopy(legacy)
    async def route(**kwargs):
        return {'status': 'ready', 'legs': []}
    async def run():
        return [event async for event in stream_production_plan(intent=TripIntent.model_validate(legacy['intent']), query='上海到杭州', tool_manager=object(), message_history=[], session_id='local-test', allow_web_search=False)]
    from services.production_planning_service import finalize_initial_schedule
    verification_calls = 0
    async def verify(document, **kwargs):
        nonlocal verification_calls
        verification_calls += 1
        if retry_verification and verification_calls == 1:
            document['itinerary']['days'][0]['activities'].clear()
            raise RuntimeError('路线供应商暂时失败')
        assert document['itinerary']['days'][0]['activities'], '重试必须取得未被失败尝试污染的文档'
        return await finalize_initial_schedule(document, **kwargs)
    with ExitStack() as stack:
        stack.enter_context(patch('services.production_planning_service.finalize_initial_schedule', side_effect=verify))
        for name, kwargs in {
            '_run_map_search_for_travel': {'side_effect': search},
            'maybe_prepare_train_ticket_bundle': {'return_value': None},
            'maybe_prepare_travel_rag_context': {'return_value': ''},
            'build_travel_document': {'side_effect': build},
            'maybe_prepare_destination_cover': {'return_value': None},
            'maybe_prepare_activity_images': {'return_value': 0},
        }.items():
            stack.enter_context(patch('services.chat_service.' + name, **kwargs))
        stack.enter_context(patch('services.route_geometry_service.build_day_route', side_effect=route))
        events = asyncio.run(run())
    tasks = [AgentStagePayload.model_validate(event['payload']) for event in events if event['type'] == 'planning_task']
    assert {task.task_id for task in tasks} == {'requirements', 'research', 'transport', 'lodging', 'dining', 'route', 'verification', 'budget', 'quality'}
    assert calls == {'research': 2, 'lodging': 1, 'dining': 1, 'build_document': 1}
    assert any(task.task_id == 'research' and task.status == 'retrying' for task in tasks)
    assert any(task.task_id == 'verification' and task.status == 'retrying' for task in tasks) == retry_verification
    assert all(task.result is not None and task.duration_ms is not None for task in tasks if task.status == 'completed')
    starts = [task.task_id for task in tasks if task.status == 'running']
    assert starts.index('route') > max(starts.index(role) for role in ('research', 'transport', 'lodging', 'dining'))
    result = TravelPlanDocumentV3.model_validate(events[-1]['document'])
    assert result.title == '杭州1日旅行方案' and result.budget.estimated_total.amount == 40


def test_graph_failure_cancels_sibling_and_never_starts_dependent_task():
    cancelled = []
    async def fail():
        await asyncio.sleep(0)
        raise RuntimeError('failed')
    async def sibling():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    async def run():
        graph = PlanningTaskGraph([
            PlanningTask('fail', '测试', 'research', '失败', max_attempts=1),
            PlanningTask('sibling', '测试', 'research', '同时工作'),
            PlanningTask('dependent', '测试', 'route_planning', '不应启动', dependencies=('fail',)),
        ])
        try:
            await graph.execute({'fail': fail, 'sibling': sibling, 'dependent': lambda: (_ for _ in ()).throw(AssertionError())})
        except RuntimeError:
            pass
        else:
            raise AssertionError('失败必须向调用者传播')
    asyncio.run(run())
    assert cancelled == [True]


def test_multiday_lodging_without_verified_place_retries_before_route_or_formal_publication():
    legacy = build_legacy_v2_document()
    intent = TripIntent.model_validate({**legacy['intent'], 'days': 2, 'date_range': '日期暂未确定'})
    calls = Counter()
    events = []
    def search(**kwargs):
        calls[kwargs['research_role']] += 1
        return [{'name': '真实地点'}], 'provider', '', []
    async def run():
        try:
            async for event in stream_production_plan(intent=intent, query='上海到杭州', tool_manager=object(),
                    message_history=[], session_id='lodging-retry', allow_web_search=False):
                events.append(event)
        except ValueError as error:
            assert '住宿地点尚未核验成功' in str(error)
        else:
            raise AssertionError('多日方案没有真实住宿，不能继续正式发布')
    with patch('services.chat_service._run_map_search_for_travel', side_effect=search), \
         patch('services.chat_service.maybe_prepare_travel_rag_context', return_value=''), \
         patch('services.chat_service.build_travel_document') as build:
        asyncio.run(run())
    build.assert_not_called()
    assert calls == {'research': 1, 'dining': 1, 'lodging': 2}
    tasks = [event['payload'] for event in events if event['type'] == 'planning_task']
    assert any(task['task_id'] == 'lodging' and task['status'] == 'retrying' for task in tasks)
    assert not any(event['type'] == 'trip_plan' for event in events)


def test_ready_task_does_not_wait_for_unrelated_research():
    async def run():
        dependent_started = asyncio.Event()
        async def fast():
            return 'ready'
        async def unrelated():
            # 旧的分批 gather 会形成死等：无依赖关系的任务不应互相阻塞。
            await asyncio.wait_for(dependent_started.wait(), timeout=1)
            return 'finished'
        async def dependent():
            dependent_started.set()
            return 'scheduled'
        graph = PlanningTaskGraph([
            PlanningTask('fast', '测试', 'research', '资料'),
            PlanningTask('unrelated', '测试', 'research', '独立检索', max_attempts=1),
            PlanningTask('dependent', '测试', 'route_planning', '路线', dependencies=('fast',)),
        ])
        result = await graph.execute({'fast': fast, 'unrelated': unrelated, 'dependent': dependent})
        assert result['dependent'].value == 'scheduled'
    asyncio.run(run())


def test_graph_cancellation_waits_for_running_task_cleanup():
    async def run():
        started = asyncio.Event()
        cleaned = asyncio.Event()
        async def lookup():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        graph = PlanningTaskGraph([PlanningTask('lookup', '测试', 'research', '检索')])
        worker = asyncio.create_task(graph.execute({'lookup': lookup}))
        await started.wait()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('取消应向上传播')
        assert cleaned.is_set()
    asyncio.run(run())


def test_graph_rejects_duplicate_ids_and_missing_executors_before_work():
    import pytest
    task = PlanningTask('lookup', '测试', 'research', '检索')
    with pytest.raises(ValueError, match='ID'):
        PlanningTaskGraph([task, task])
    async def run():
        with pytest.raises(ValueError, match='执行器'):
            await PlanningTaskGraph([task]).execute({})
    asyncio.run(run())
