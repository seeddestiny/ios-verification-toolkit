#!/usr/bin/env bash
#
# 05_install_mcp.sh — 安装 MCP Server 的 Python 依赖(mcp SDK + requests)
# ─────────────────────────────────────────────────────────────────────────────
# 供应链安全:优先使用 PIP_INDEX_URL 或本机 pip 配置，其次使用项目级候选，
#            都未配置时使用官方 PyPI；始终只使用单一源且不修改用户配置。
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

PUBLIC_PYPI="https://pypi.org/simple"
TRUSTED_PYPI=""
PYPI_SOURCE=""

c_info(){ printf "\033[36m[INFO]\033[0m  %s\n" "$*"; }
c_ok(){   printf "\033[32m[ OK ]\033[0m  %s\n" "$*"; }
c_warn(){ printf "\033[33m[WARN]\033[0m  %s\n" "$*"; }
c_err(){  printf "\033[31m[FAIL]\033[0m  %s\n" "$*" >&2; }
c_step(){ printf "\n\033[1;34m===== %s =====\033[0m\n" "$*"; }

PY=python3

check_only(){
  c_step "检查 MCP 依赖"
  $PY -c "import mcp; from mcp.server.fastmcp import FastMCP; print('mcp OK')" 2>&1 || { c_warn "mcp 未安装"; return 1; }
  $PY -c "import requests; print('requests', requests.__version__)" 2>&1 || { c_warn "requests 未安装"; return 1; }
  c_ok "MCP 依赖就绪。"
}

configured_pypi_index(){
  local configured=""
  configured="$($PY -m pip config get global.index-url 2>/dev/null || true)"
  if [ -z "$configured" ]; then
    configured="$($PY -m pip config get install.index-url 2>/dev/null || true)"
  fi
  printf '%s' "$configured"
}

configured_pypi_extra_index(){
  local configured=""
  configured="$($PY -m pip config get global.extra-index-url 2>/dev/null || true)"
  if [ -z "$configured" ]; then
    configured="$($PY -m pip config get install.extra-index-url 2>/dev/null || true)"
  fi
  printf '%s' "$configured"
}

resolve_pypi(){
  local configured=""
  if [ -n "${PIP_INDEX_URL:-}" ]; then
    TRUSTED_PYPI="$PIP_INDEX_URL"
    PYPI_SOURCE="用户环境 PIP_INDEX_URL"
  else
    configured="$(configured_pypi_index)"
    if [ -n "$configured" ]; then
      TRUSTED_PYPI="$configured"
      PYPI_SOURCE="本机 pip 配置"
    fi
  fi

  if [ -z "$TRUSTED_PYPI" ] && [ -n "${IOS_MCP_PYPI:-}" ]; then
    TRUSTED_PYPI="$IOS_MCP_PYPI"
    PYPI_SOURCE="IOS_MCP_PYPI"
  elif [ -z "$TRUSTED_PYPI" ]; then
    TRUSTED_PYPI="$PUBLIC_PYPI"
    PYPI_SOURCE="PyPI 官方默认源"
  fi
}

detect_pypi(){
  c_step "探测可信 PyPI 镜像(供应链安全)"
  local metadata scheme host has_credentials code extra_index
  resolve_pypi

  extra_index="${PIP_EXTRA_INDEX_URL:-$(configured_pypi_extra_index)}"
  if [ -n "$extra_index" ]; then
    c_err "检测到 extra-index-url；为避免依赖混淆，本工具只允许一个 PyPI 源。"
    c_err "请在本轮环境中清除 PIP_EXTRA_INDEX_URL 和对应 pip extra-index-url 配置。"
    exit 1
  fi

  metadata="$(printf '%s' "$TRUSTED_PYPI" | $PY -c '
import sys, urllib.parse
p = urllib.parse.urlsplit(sys.stdin.read().strip())
print("\t".join((p.scheme, p.hostname or "", "1" if p.username or p.password else "0")))
')"
  IFS=$'\t' read -r scheme host has_credentials <<< "$metadata"
  [ -n "$host" ] && { [ "$scheme" = "https" ] || [ "$scheme" = "http" ]; } \
    || { c_err "解析到的 PyPI 配置不是合法 HTTP(S) URL"; exit 1; }
  [ "$has_credentials" = "0" ] \
    || { c_err "PyPI URL 不得内嵌账号或令牌；请使用本机 pip 认证/keyring。"; exit 1; }
  if [ "$scheme" != "https" ] && [ "${ALLOW_INSECURE_PYPI:-0}" != "1" ]; then
    c_err "PyPI 源不是 HTTPS；确认受信网络后才可显式设置 ALLOW_INSECURE_PYPI=1。"
    exit 1
  fi

  c_info "PyPI 源选择: $PYPI_SOURCE(不回显地址)"
  # 2xx/3xx/401/403/429 都说明镜像服务存在(429=限流)。
  code="$(PYPI_PROBE_URL="$TRUSTED_PYPI" $PY -c '
import os, urllib.error, urllib.request
try:
    with urllib.request.urlopen(os.environ["PYPI_PROBE_URL"], timeout=8) as response:
        print(response.status)
except urllib.error.HTTPError as exc:
    print(exc.code)
except Exception:
    print("000")
' 2>/dev/null)"
  if printf '%s' "$code" | grep -qE '^(2..|3..|401|403|429)$'; then
    c_ok "可信 PyPI 可达 (HTTP $code)"
  else
    c_warn "可信 PyPI 不可达 (HTTP ${code:-000})。"
    c_err "不会切换到其它源；请修复本机配置后重试。"
    exit 1
  fi
  export PIP_INDEX_URL="$TRUSTED_PYPI"
  export PIP_DISABLE_PIP_VERSION_CHECK=1
  export PIP_NO_INPUT=1
}

main(){
  case "${1:-}" in
    --check) check_only; exit $? ;;
  esac

  c_step "安装 ios_ui_automation MCP Server 依赖"
  command -v $PY >/dev/null 2>&1 || { c_err "未找到 python3"; exit 1; }

  detect_pypi

  if [ "${1:-}" = "--venv" ]; then
    c_info "在 $MCP_DIR/.venv 创建不继承系统 site-packages 的隔离虚拟环境"
    rm -rf "$MCP_DIR/.venv"
    $PY -m venv "$MCP_DIR/.venv"
    # shellcheck disable=SC1091
    source "$MCP_DIR/.venv/bin/activate"
    PY=python
    c_ok "已激活虚拟环境: $MCP_DIR/.venv"
  fi

  c_info "pip 安装: $REQ(只接受 wheel，不升级 pip、不执行源码包构建)"
  $PY -m pip install --only-binary=:all: -r "$REQ" || { c_err "依赖安装失败"; exit 1; }

  check_only && c_ok "MCP 依赖安装完成。" || { c_err "安装后校验未通过"; exit 1; }

  c_step "下一步"
  c_info "推荐由 ios-change-verification Skill 按需启动，不要注册到 Agent 全局 MCP 配置:"
  c_info "  $MCP_DIR/.venv/bin/python ~/.agents/skills/ios-change-verification/scripts/ios_ui_session.py start --session <runId>"
  c_info "只有明确需要所有会话都携带原生 tools 时，才参考 mcp_server/mcp_config.example.json。"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
