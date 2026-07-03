# xhs-mcp

Small MCP server for Xiaohongshu search and summary.

## Tools

- `xhs_search_resources`: Search Xiaohongshu resources by query.
- `xhs_search_and_summarize`: Search and return concise summary with resource list.

## Env

- `XHS_COOKIE`: Optional. When valid, server tries official Xiaohongshu web search API first.
- `SERPER_API_KEY`: Optional fallback for site search when official API is unavailable.

## Run

```bash
python D:/vscode/project/SuperTravelAgent/mcp_servers/xhs-mcp/main.py
```
