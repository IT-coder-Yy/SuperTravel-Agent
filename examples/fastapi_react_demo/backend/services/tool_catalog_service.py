from typing import Any, Dict, List

from agents.utils.logger import logger


def _simple_chinese_description(name: str, description: Any) -> str:
    normalized = str(name or "").lower()
    rules = [
        (("calculate", "factorial", "math"), "执行基础数值计算。"),
        (("unsplash",), "搜索并提供带版权署名的旅行图片。"),
        (("12306", "train"), "查询中国铁路车次、余票和中转方案。"),
        (("flight",), "查询航班和机票参考信息。"),
        (("bus", "coach"), "查询城际大巴班次和票务参考信息。"),
        (("map", "route", "geocode"), "查询地点、坐标、距离和路线建议。"),
        (("weather",), "查询目的地天气和出行提示。"),
        (("xhs", "xiaohongshu"), "检索与目的地旅行主题相关的小红书公开内容。"),
        (("search", "serper", "tavily", "fetch"), "检索公开网页中的旅行参考信息。"),
        (("file",), "在旅行输出目录中读取和保存文件。"),
    ]
    for keywords, text in rules:
        if any(keyword in normalized for keyword in keywords):
            return text
    first_line = str(description or "").strip().split("\n", 1)[0].strip()
    if not first_line or not any("\u4e00" <= char <= "\u9fff" for char in first_line):
        return "为旅行规划提供结构化辅助能力。"
    sentence = first_line.split("。", 1)[0].strip()
    return f"{sentence}。"


def build_tool_catalog(tool_manager: Any) -> List[Dict[str, Any]]:
    """Build simplified tool catalog payload for API response."""
    if not tool_manager:
        return []

    tools = tool_manager.list_tools_simplified()
    return [
        {
            "name": tool["name"],
            "description": _simple_chinese_description(tool["name"], tool.get("description")),
            "parameters": tool.get("parameters", {}),
        }
        for tool in tools
    ]


def build_tool_catalog_runtime_model_http_response(response: Any, runtime_state: Any) -> List[Any]:
    """将工具目录转换为 HTTP 响应。"""
    from fastapi import HTTPException
    from schemas.api_models import ToolInfo
    from services.http_response_service import add_cors_headers

    add_cors_headers(response)
    try:
        payload = build_tool_catalog(runtime_state.tool_manager)
    except Exception as exc:
        logger.error(f"获取工具列表失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [ToolInfo(**tool) for tool in payload]
