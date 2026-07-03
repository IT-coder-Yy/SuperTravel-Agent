import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set


@dataclass(frozen=True)
class SkillProfile:
    id: str
    name: str
    description: str
    system_prompt: str
    workflow_steps: List[str]
    allowed_mcp_servers: List[str]
    allowed_local_tools: List[str]
    answer_style: str
    fallback: str
    required_env: List[str] = field(default_factory=list)
    required_any_tools: List[str] = field(default_factory=list)


BUILTIN_SKILL_PROFILES: List[SkillProfile] = [
    SkillProfile(
        id="travel_planner",
        name="综合旅行规划",
        description="整合交通、地图、攻略和预算，生成可执行的多日旅行计划。",
        system_prompt=(
            "你是综合旅行规划助手。先确认目的地、日期、人数、预算和偏好；"
            "再组合交通、地图、攻略和预算信息，输出可执行行程。"
        ),
        workflow_steps=[
            "识别城市、日期、天数、人数、预算和兴趣偏好",
            "按需调用交通、地图、搜索和小红书工具补齐证据",
            "按区域和时间顺序组织每日行程",
            "给出交通、预算、风险和备选方案",
        ],
        allowed_mcp_servers=["baidu-map", "12306-mcp", "xhs-mcp", "serper_web_search", "fetch"],
        allowed_local_tools=["calculate", "query_12306_realtime_tickets", "query_12306_tickets_by_query"],
        answer_style="结论优先；按天输出行程；附交通、预算和注意事项。",
        fallback="缺少实时数据时明确说明无法确认，不编造价格、余票、营业时间或链接。",
        required_any_tools=["calculate"],
    ),
    SkillProfile(
        id="rail_transport",
        name="交通票务查询",
        description="查询火车、高铁、直达、中转、余票和票价，并给出出行建议。",
        system_prompt=(
            "你是严谨的交通票务助手。必须优先使用实时票务工具结果；"
            "不能编造车次、票价、余票或经停信息。"
        ),
        workflow_steps=[
            "抽取出发地、目的地、日期和用户偏好",
            "优先查询直达车次",
            "再查询中转方案",
            "按省时、省钱和少换乘给出2-3个推荐",
        ],
        allowed_mcp_servers=["12306-mcp"],
        allowed_local_tools=["query_12306_realtime_tickets", "query_12306_tickets_by_query"],
        answer_style="先给推荐结论，再给车次表格和购票注意事项。",
        fallback="实时票务不可用时，要求用户补充站点/日期或提示稍后重试。",
        required_any_tools=[
            "get-tickets",
            "get-interline-tickets",
            "query_12306_realtime_tickets",
            "query_12306_tickets_by_query",
        ],
    ),
    SkillProfile(
        id="map_route",
        name="地图路线规划",
        description="围绕地点检索、地理编码、路线规划和顺路排序组织行程。",
        system_prompt=(
            "你是地图路线规划助手。优先按地理邻近性和交通便利性安排路线，"
            "减少折返，并明确路线假设。"
        ),
        workflow_steps=[
            "识别起点、终点和候选地点",
            "调用地图工具做地点检索、地理编码和路线规划",
            "按距离和通勤时间聚类地点",
            "输出顺路路线和备选路线",
        ],
        allowed_mcp_servers=["baidu-map"],
        allowed_local_tools=[],
        answer_style="路线顺序 + 时间/距离理由 + 地图友好的地点清单。",
        fallback="地图工具不可用时，只能给经验性路线，并明确未经过实时地图验证。",
    ),
    SkillProfile(
        id="xhs_insight",
        name="小红书种草避雷",
        description="检索小红书资源，提炼真实反馈、推荐点和避雷点。",
        system_prompt=(
            "你是小红书旅行洞察助手。必须基于检索到的笔记资源总结，"
            "不得编造不存在的作者、链接、点赞数据或笔记内容。"
        ),
        workflow_steps=[
            "提取用户关注点，如拍照、美食、住宿、省钱或避雷",
            "调用小红书检索和摘要工具",
            "去重并提炼共性推荐和风险",
            "将UGC观点与其他来源区分开",
        ],
        allowed_mcp_servers=["xhs-mcp", "serper_web_search"],
        allowed_local_tools=[],
        answer_style="3-5条建议 + 避雷点 + 可用资源表。",
        fallback="未检索到资源时说明数据不足，改用网页搜索或请用户换关键词。",
        required_any_tools=["xhs_search_and_summarize", "xhs_search_resources"],
    ),
    SkillProfile(
        id="destination_research",
        name="目的地攻略检索",
        description="结合网页、官方信息和UGC做目的地攻略研究。",
        system_prompt=(
            "你是目的地研究助手。需要区分官方信息、网页资料和用户经验，"
            "对时效性强的信息必须提示核验。"
        ),
        workflow_steps=[
            "拆分景点、美食、住宿、交通和季节性问题",
            "调用网页搜索和页面抓取工具",
            "按需结合小红书资源做交叉验证",
            "输出研究简报和决策建议",
        ],
        allowed_mcp_servers=["serper_web_search", "fetch", "xhs-mcp"],
        allowed_local_tools=[],
        answer_style="简明研究结论 + 分主题证据 + 风险提示。",
        fallback="搜索工具不可用时，只给通用建议，并提示需要外部核验。",
        required_any_tools=["search_web_page", "xhs_search_and_summarize"],
    ),
    SkillProfile(
        id="budget_optimizer",
        name="预算优化",
        description="拆分交通、住宿、餐饮、门票和市内交通预算，给省钱方案。",
        system_prompt=(
            "你是旅行预算优化助手。必须显式拆分预算项，使用计算工具核算总额，"
            "并给出省钱但可执行的取舍。"
        ),
        workflow_steps=[
            "识别总预算、人数、天数和舒适度要求",
            "估算交通、住宿、餐饮、门票和本地交通",
            "调用计算工具核算总额和人均费用",
            "给出基础、省钱和舒适三个档位",
        ],
        allowed_mcp_servers=["12306-mcp", "baidu-map", "serper_web_search"],
        allowed_local_tools=["calculate"],
        answer_style="预算表 + 总额/人均 + 省钱杠杆 + 推荐档位。",
        fallback="缺少实时价格时使用区间估算，并明确估算来源和不确定性。",
        required_any_tools=["calculate"],
    ),
    SkillProfile(
        id="local_discovery",
        name="住宿美食景点发现",
        description="围绕住宿区域、美食、景点和打卡点生成本地发现清单。",
        system_prompt=(
            "你是本地发现助手。需要按区域、交通便利性和用户偏好筛选住宿、"
            "美食、景点和打卡点。"
        ),
        workflow_steps=[
            "识别城市、住宿区域、口味、预算和游玩偏好",
            "调用地图、小红书和网页搜索工具寻找候选点",
            "按区域和路线便利性筛选",
            "输出分类短名单和适合人群",
        ],
        allowed_mcp_servers=["baidu-map", "xhs-mcp", "serper_web_search"],
        allowed_local_tools=[],
        answer_style="住宿/美食/景点分类清单 + 推荐理由 + 路线匹配。",
        fallback="缺少实时地点数据时给区域级建议，并提醒用户二次核验。",
        required_any_tools=["xhs_search_and_summarize", "search_web_page"],
    ),
]


def get_builtin_skill_profiles() -> List[SkillProfile]:
    return list(BUILTIN_SKILL_PROFILES)


def resolve_skill_profiles(selected_skill_ids: Optional[Sequence[str]]) -> List[SkillProfile]:
    if not selected_skill_ids:
        return []

    profiles_by_id = {profile.id: profile for profile in BUILTIN_SKILL_PROFILES}
    resolved: List[SkillProfile] = []
    seen: Set[str] = set()

    for skill_id in selected_skill_ids:
        normalized_id = str(skill_id or "").strip()
        if not normalized_id or normalized_id in seen:
            continue
        profile = profiles_by_id.get(normalized_id)
        if profile:
            resolved.append(profile)
            seen.add(normalized_id)

    return resolved


def build_skill_system_message(selected_skill_ids: Optional[Sequence[str]]) -> Optional[Dict[str, str]]:
    profiles = resolve_skill_profiles(selected_skill_ids)
    if not profiles:
        return None

    lines = [
        "【用户选择的旅行技能包】",
        "请严格按下列技能改变回答策略、工具使用优先级和输出结构。",
    ]
    for profile in profiles:
        lines.extend(
            [
                "",
                f"## {profile.name} ({profile.id})",
                f"能力说明: {profile.description}",
                f"系统要求: {profile.system_prompt}",
                "工作流:",
            ]
        )
        lines.extend([f"- {step}" for step in profile.workflow_steps])
        if profile.allowed_mcp_servers:
            lines.append(f"优先MCP服务器: {', '.join(profile.allowed_mcp_servers)}")
        if profile.allowed_local_tools:
            lines.append(f"优先本地工具: {', '.join(profile.allowed_local_tools)}")
        lines.append(f"输出风格: {profile.answer_style}")
        lines.append(f"失败兜底: {profile.fallback}")

    return {
        "role": "system",
        "content": "\n".join(lines),
        "message_id": f"skill_{uuid.uuid4()}",
        "type": "system_skill_profile",
    }


def merge_skill_mcp_servers(
    selected_mcp_servers: Optional[Sequence[str]],
    selected_skill_ids: Optional[Sequence[str]],
) -> Optional[List[str]]:
    if selected_mcp_servers is None and not selected_skill_ids:
        return None

    merged: List[str] = []
    for server_name in selected_mcp_servers or []:
        normalized = str(server_name or "").strip()
        if normalized and normalized not in merged:
            merged.append(normalized)

    for profile in resolve_skill_profiles(selected_skill_ids):
        for server_name in profile.allowed_mcp_servers:
            if server_name not in merged:
                merged.append(server_name)

    return merged


def collect_skill_local_tools(selected_skill_ids: Optional[Sequence[str]]) -> List[str]:
    tools: List[str] = []
    for profile in resolve_skill_profiles(selected_skill_ids):
        for tool_name in profile.allowed_local_tools:
            if tool_name not in tools:
                tools.append(tool_name)
    return tools


def _list_runtime_tool_names(tool_manager: Any) -> Set[str]:
    if not tool_manager:
        return set()

    try:
        tools = tool_manager.list_tools_simplified()
    except Exception:
        try:
            tools = tool_manager.list_tools()
        except Exception:
            return set()

    names: Set[str] = set()
    for tool in tools or []:
        if isinstance(tool, dict):
            name = str(tool.get("name") or "").strip()
        else:
            name = str(getattr(tool, "name", "") or "").strip()
        if name:
            names.add(name)
    return names


def _configured_mcp_servers(app_config: Any) -> Dict[str, Any]:
    servers = getattr(getattr(app_config, "mcp", None), "servers", None)
    if isinstance(servers, dict):
        return servers
    return {}


def _missing_mcp_servers(profile: SkillProfile, app_config: Any) -> List[str]:
    servers = _configured_mcp_servers(app_config)
    missing: List[str] = []
    for server_name in profile.allowed_mcp_servers:
        server_config = servers.get(server_name)
        if server_config is None or bool(getattr(server_config, "disabled", False)):
            missing.append(server_name)
    return missing


def _missing_env(profile: SkillProfile) -> List[str]:
    return [env_name for env_name in profile.required_env if not os.getenv(env_name)]


def _missing_local_tools(profile: SkillProfile, runtime_tools: Set[str]) -> List[str]:
    return [tool_name for tool_name in profile.allowed_local_tools if tool_name not in runtime_tools]


def _is_available(
    profile: SkillProfile,
    runtime_tools: Set[str],
    missing_mcp_servers: List[str],
    missing_env: List[str],
) -> bool:
    if missing_env:
        return False
    if missing_mcp_servers and not profile.allowed_local_tools:
        return False
    if profile.required_any_tools:
        return any(tool_name in runtime_tools for tool_name in profile.required_any_tools)
    return True


def list_skill_infos(tool_manager: Any = None, app_config: Any = None) -> List[Dict[str, Any]]:
    runtime_tools = _list_runtime_tool_names(tool_manager)
    infos: List[Dict[str, Any]] = []

    for profile in BUILTIN_SKILL_PROFILES:
        missing_mcp = _missing_mcp_servers(profile, app_config)
        missing_env = _missing_env(profile)
        missing_local = _missing_local_tools(profile, runtime_tools)
        infos.append(
            {
                "id": profile.id,
                "name": profile.name,
                "description": profile.description,
                "workflow_steps": list(profile.workflow_steps),
                "allowed_mcp_servers": list(profile.allowed_mcp_servers),
                "allowed_local_tools": list(profile.allowed_local_tools),
                "answer_style": profile.answer_style,
                "fallback": profile.fallback,
                "required_env": list(profile.required_env),
                "available": _is_available(profile, runtime_tools, missing_mcp, missing_env),
                "missing_mcp_servers": missing_mcp,
                "missing_local_tools": missing_local,
                "missing_env": missing_env,
            }
        )

    return infos


def build_skill_catalog_http_response(response: Any, tool_manager: Any, app_config: Any) -> List[Dict[str, Any]]:
    from services.http_response_service import add_cors_headers

    add_cors_headers(response)
    return list_skill_infos(tool_manager=tool_manager, app_config=app_config)


def build_skill_catalog_model_http_response(response: Any, tool_manager: Any, app_config: Any) -> List[Any]:
    from schemas.api_models import SkillInfo

    payload = build_skill_catalog_http_response(
        response=response,
        tool_manager=tool_manager,
        app_config=app_config,
    )
    return [SkillInfo(**item) for item in payload]


def build_skill_catalog_runtime_model_http_response(response: Any, runtime_state: Any) -> List[Any]:
    from config_loader import get_app_config

    return build_skill_catalog_model_http_response(
        response=response,
        tool_manager=runtime_state.tool_manager,
        app_config=get_app_config(),
    )
