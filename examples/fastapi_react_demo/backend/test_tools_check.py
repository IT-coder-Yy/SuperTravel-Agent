import asyncio
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'd:/vscode/project/SuperTravelAgent')
sys.path.insert(0, 'd:/vscode/project/SuperTravelAgent/examples/fastapi_react_demo/backend')
os.chdir('d:/vscode/project/SuperTravelAgent/examples/fastapi_react_demo/backend')

from config_loader import get_app_config
from services.tool_runtime_service import initialize_tool_manager

async def main():
    config = get_app_config()
    manager = await initialize_tool_manager(config)
    for name in sorted(manager.tools.keys()):
        print(name)
    print(f'Total: {len(manager.tools)}')


if __name__ == '__main__':
    asyncio.run(main())
