#!/usr/bin/env python3
"""
verify.py — 通过 MCP 实际驱动真机做端到端验证。
用法: python verify.py            # tunnel/status → screenshot → ui_hierarchy
      python verify.py --tools   # 仅列出 tools(不碰真机)
"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

try:
    from .env_sanitizer import sanitized_env
    from .runtime_paths import ensure_runtime_paths
except ImportError:
    from env_sanitizer import sanitized_env
    from runtime_paths import ensure_runtime_paths

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
OUT = str(ensure_runtime_paths().screenshots / "mcp_verify_target.png")
EXPECTED_TOOLS = {
    "device_status", "tunnel_status", "start_tunnel", "stop_tunnel",
    "screenshot", "get_ui_hierarchy", "tap",
}


def _text(result):
    return "\n".join(getattr(c, "text", "") for c in result.content)


async def _call(session, name, arguments):
    """调用 MCP tool，并把协议 isError/ERROR 文本提升为验证失败。"""
    result = await session.call_tool(name, arguments)
    text = _text(result)
    if getattr(result, "isError", False) or text.startswith("ERROR:") or "Error executing tool" in text:
        raise RuntimeError(f"{name} 失败: {text[:600]}")
    return text


async def main(tools_only: bool):
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=sanitized_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("=== tools ===")
            for t in tools.tools:
                print(f"  - {t.name}")
            actual = {t.name for t in tools.tools}
            missing = sorted(EXPECTED_TOOLS - actual)
            unexpected = sorted(actual - EXPECTED_TOOLS)
            if missing:
                raise RuntimeError(f"MCP 缺少预期 tools: {', '.join(missing)}")
            if unexpected:
                raise RuntimeError(f"MCP 仍暴露已抽离或未知 tools: {', '.join(unexpected)}")
            if tools_only:
                print(f"\n共 {len(tools.tools)} 个 tool ✅")
                return

            started_tunnel = False
            try:
                print("\n=== tunnel_status ===")
                initial_tunnel = await _call(session, "tunnel_status", {})
                print(initial_tunnel)
                if "隧道运行中" not in initial_tunnel:
                    print("\n=== start_tunnel ===")
                    print(await _call(session, "start_tunnel", {}))
                    started_tunnel = True

                print("\n=== device_status ===")
                print(await _call(session, "device_status", {}))

                print("\n=== screenshot ===")
                print(await _call(session, "screenshot", {"save_path": OUT}))
                print("\n=== get_ui_hierarchy (前 200 字符) ===")
                print((await _call(session, "get_ui_hierarchy", {}))[:200])
            finally:
                if started_tunnel:
                    print("\n=== restore tunnel state ===")
                    print(await _call(session, "stop_tunnel", {}))
            print("\nMCP 端到端真机验证完成 ✅")


if __name__ == "__main__":
    asyncio.run(main("--tools" in sys.argv))
