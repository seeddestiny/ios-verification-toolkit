#!/usr/bin/env bash
#
# 07_install_skill.sh — 安装 Codex / TRAE CLI 共用的 iOS 验证 Skill
#
# 默认把仓库内 Skill 以软链接安装到 ~/.agents/skills，避免复制后版本漂移。
# 不注册全局 MCP；UI MCP 仍由 Skill 在实际需要时按需启动。
#
# 用法:
#   bash scripts/07_install_skill.sh             # 安装或确认已安装
#   bash scripts/07_install_skill.sh --check     # 只检查链接是否指向当前仓库
#   bash scripts/07_install_skill.sh --uninstall # 仅删除指向当前仓库的安装链接

set -euo pipefail
umask 077

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$DIR/.." && pwd)"
SKILL_NAME="ios-change-verification"
SOURCE_DIR="$PROJECT_DIR/skills/$SKILL_NAME"
INSTALL_ROOT="$HOME/.agents/skills"
TARGET="$INSTALL_ROOT/$SKILL_NAME"

c_info(){ printf "\033[36m[INFO]\033[0m  %s\n" "$*"; }
c_ok(){   printf "\033[32m[ OK ]\033[0m  %s\n" "$*"; }
c_warn(){ printf "\033[33m[WARN]\033[0m  %s\n" "$*"; }
c_err(){  printf "\033[31m[FAIL]\033[0m  %s\n" "$*" >&2; }

usage(){
  sed -n '2,12s/^# \{0,1\}//p' "$0"
}

canonical_path(){
  python3 - "$1" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).expanduser().resolve(strict=False))
PY
}

validate_source(){
  [ -f "$SOURCE_DIR/SKILL.md" ] \
    || { c_err "Skill 源不存在: $SOURCE_DIR/SKILL.md"; exit 1; }
  grep -q '^name: ios-change-verification$' "$SOURCE_DIR/SKILL.md" \
    || { c_err "Skill 名称校验失败: $SOURCE_DIR/SKILL.md"; exit 1; }
}

link_points_to_source(){
  [ -L "$TARGET" ] || return 1
  [ "$(canonical_path "$TARGET")" = "$(canonical_path "$SOURCE_DIR")" ]
}

check_install(){
  validate_source
  if link_points_to_source; then
    c_ok "共享 Skill 已安装: $TARGET"
    return 0
  fi
  if [ -L "$TARGET" ]; then
    c_warn "目标是指向其它位置的软链接，未修改: $TARGET"
  elif [ -e "$TARGET" ]; then
    c_warn "目标已存在且由用户管理，未修改: $TARGET"
  else
    c_warn "共享 Skill 尚未安装: $TARGET"
  fi
  return 1
}

install_skill(){
  validate_source
  if link_points_to_source; then
    c_ok "共享 Skill 已安装且指向当前仓库，无需更新。"
    return 0
  fi
  if [ -L "$TARGET" ] || [ -e "$TARGET" ]; then
    c_err "安装目标已存在，为避免覆盖用户内容已停止: $TARGET"
    c_info "请先检查并自行备份或移走该目标，然后重新运行本脚本。"
    exit 2
  fi

  mkdir -p "$INSTALL_ROOT"
  ln -s "$SOURCE_DIR" "$TARGET"
  link_points_to_source \
    || { c_err "软链接创建后校验失败: $TARGET"; exit 1; }
  c_ok "共享 Skill 已安装: $TARGET -> $SOURCE_DIR"
  c_info "Codex 与 TRAE CLI 新会话均可使用 \$ios-change-verification。"
  c_info "未创建全局 MCP 注册；UI MCP 仍由 Skill 按需启动。"
}

uninstall_skill(){
  validate_source
  if [ ! -L "$TARGET" ] && [ ! -e "$TARGET" ]; then
    c_ok "共享 Skill 安装链接不存在，无需清理。"
    return 0
  fi
  if ! link_points_to_source; then
    c_warn "目标不属于当前仓库，拒绝删除: $TARGET"
    return 2
  fi
  rm "$TARGET"
  c_ok "已删除共享 Skill 安装链接；仓库内 Skill 源码保持不变。"
}

case "${1:-}" in
  "")          install_skill ;;
  --check)     check_install ;;
  --uninstall) uninstall_skill ;;
  --help|-h)   usage ;;
  *)           c_err "未知参数: $1"; usage; exit 2 ;;
esac
