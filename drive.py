#!/usr/bin/env python3
"""Reusable WDA UI driver for verification. Usage:
  python drive.py status
  python drive.py shot <save_path>
  python drive.py tap_name <name>
  python drive.py tap_xy <x> <y>
  python drive.py ui
  python drive.py tunnel

App build/install/launch and syslog use tools/ios_app_tool.py and
tools/ios_log_tool.py instead of this MCP driver.
"""
import asyncio, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_server.env_sanitizer import sanitized_env

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "mcp_server", "server.py")

def _text(result):
    return "\n".join(getattr(c, "text", "") for c in result.content)

async def main(argv):
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=sanitized_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            cmd = argv[0] if argv else "status"
            if cmd == "status":
                print(_text(await session.call_tool("device_status", {})))
            elif cmd == "tunnel":
                print(_text(await session.call_tool("tunnel_status", {})))
            elif cmd == "shot":
                print(_text(await session.call_tool("screenshot", {"save_path": argv[1]})))
            elif cmd == "tap_name":
                print(_text(await session.call_tool("tap", {"name": argv[1]})))
            elif cmd == "tap_xy":
                print(_text(await session.call_tool("tap", {"x": int(argv[1]), "y": int(argv[2])})))
            elif cmd == "ui":
                print(_text(await session.call_tool("get_ui_hierarchy", {})))
            else:
                print("unknown cmd", cmd)

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
