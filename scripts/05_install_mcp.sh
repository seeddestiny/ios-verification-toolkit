#!/usr/bin/env bash
#
# 05_install_mcp.sh — 安装 MCP Server 的 Python 依赖(mcp SDK + requests)
# ─────────────────────────────────────────────────────────────────────────────
# 供应链安全:优先使用 IOS_MCP_PYPI 或本机 PIP_INDEX_URL 指定的可信镜像，
#            仅本次 --index-url 生效，不把源地址写入项目或全局 pip 配置。
#
# 用法:
#   bash 05_install_mcp.sh            # 安装依赖(推荐建虚拟环境)
#   bash 05_install_mcp.sh --venv     # 在 mcp_server/.venv 建虚拟环境后安装(隔离,最安全)
#   bash 05_install_mcp.sh --check    # 只检查 mcp/requests 是否可用
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="$(cd "$DIR/../mcp_server" && pwd)"
REQ="$MCP_DIR/requirements.txt"

TRUSTED_PYPI="${IOS_MCP_PYPI:-${PIP_INDEX_URL:-}}"

c_info(){ printf "\033[36m[INFO]\033[0m  %s\n" "$*"; }
c_ok(){   printf "\033[32m[ OK ]\033[0m  %s\n" "$*"; }
c_warn(){ printf "\033[33m[WARN]\033[0m  %s\n" "$*"; }
c_err(){  printf "\033[31m[FAIL]\033[0m  %s\n" "$*" >&2; }
c_step(){ printf "\n\033[1;34m===== %s =====\033[0m\n" "$*"; }

PY=python3
PIP_ARGS=()

check_only(){
  c_step "检查 MCP 依赖"
  $PY -c "import mcp; from mcp.server.fastmcp import FastMCP; print('mcp OK')" 2>&1 || { c_warn "mcp 未安装"; return 1; }
  $PY -c "import requests; print('requests', requests.__version__)" 2>&1 || { c_warn "requests 未安装"; return 1; }
  c_ok "MCP 依赖就绪。"
}

detect_pypi(){
  c_step "探测可信 PyPI 镜像(供应链安全)"
  local host code
  if [ -z "$TRUSTED_PYPI" ]; then
    if [ "${ALLOW_PUBLIC_PYPI:-0}" = "1" ]; then
      c_warn "未配置可信镜像；已显式允许 pip 使用其默认公网源。"
      return 0
    fi
    c_err "未配置可信 PyPI。请设置 IOS_MCP_PYPI，或显式设置 ALLOW_PUBLIC_PYPI=1。"
    exit 1
  fi
  host="$($PY -c 'import sys, urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).hostname or "")' "$TRUSTED_PYPI")"
  [ -n "$host" ] || { c_err "IOS_MCP_PYPI/PIP_INDEX_URL 不是合法 URL"; exit 1; }
  # 2xx/3xx/401/403/429 都说明镜像服务存在(429=限流)。
  code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 8 "$TRUSTED_PYPI" 2>/dev/null)"
  if printf '%s' "$code" | grep -qE '^(2..|3..|401|403|429)$'; then
    c_ok "可信 PyPI 可达 (HTTP $code)"
    PIP_ARGS+=(--index-url "$TRUSTED_PYPI")
  else
    c_warn "可信 PyPI 不可达 (HTTP ${code:-000})。"
    c_warn "为遵守供应链安全,默认不在公网 pypi 静默安装。"
    c_warn "如确认要用公网源,显式设置: ALLOW_PUBLIC_PYPI=1 bash $0"
    if [ "${ALLOW_PUBLIC_PYPI:-0}" != "1" ]; then exit 1; fi
    c_warn "已允许公网 pypi。"
  fi
}

main(){
  case "${1:-}" in
    --check) check_only; exit $? ;;
  esac

  c_step "安装 ios_ui_automation MCP Server 依赖"
  command -v $PY >/dev/null 2>&1 || { c_err "未找到 python3"; exit 1; }

  detect_pypi

  if [ "${1:-}" = "--venv" ]; then
    c_info "在 $MCP_DIR/.venv 创建虚拟环境(--system-site-packages:复用系统已装的重包,避免重复编译)"
    rm -rf "$MCP_DIR/.venv"
    $PY -m venv --system-site-packages "$MCP_DIR/.venv"
    # shellcheck disable=SC1091
    source "$MCP_DIR/.venv/bin/activate"
    PY=python
    c_ok "已激活虚拟环境: $MCP_DIR/.venv"
  fi

  c_info "pip 安装: $REQ(优先用预编译 wheel,减少源码编译失败)"
  $PY -m pip install --upgrade pip "${PIP_ARGS[@]}" >/dev/null 2>&1 || true
  # --prefer-binary:能用 wheel 就不用源码,规避 cryptography 等源码编译失败
  $PY -m pip install --prefer-binary "${PIP_ARGS[@]}" -r "$REQ" || { c_err "依赖安装失败"; exit 1; }

  check_only && c_ok "MCP 依赖安装完成。" || { c_err "安装后校验未通过"; exit 1; }

  c_step "下一步"
  c_info "推荐由 ios-change-verification Skill 按需启动，不要注册到 Agent 全局 MCP 配置:"
  c_info "  $MCP_DIR/.venv/bin/python ~/.agents/skills/ios-change-verification/scripts/ios_ui_session.py start --session <runId>"
  c_info "只有明确需要所有会话都携带原生 tools 时，才参考 mcp_server/mcp_config.example.json。"
}

main "$@"
