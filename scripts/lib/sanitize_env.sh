#!/usr/bin/env bash

# Run a diagnostic child process without credential-like variables. The iOS MCP
# settings, PATH, HOME, Xcode selection and device selectors remain available.
ios_mcp_sanitized_env() {
  local name
  local upper_name
  local -a env_args=()

  while IFS='=' read -r name _; do
    upper_name="$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')"
    case "$upper_name" in
      *API_KEY*|*ACCESS_KEY*|*PRIVATE_KEY*|*TOKEN*|*SECRET*|*PASSWORD*|*PASSWD*|*CREDENTIAL*|*AUTHORIZATION*|*COOKIE*)
        env_args+=("-u" "$name")
        ;;
    esac
  done < <(env)

  /usr/bin/env "${env_args[@]}" "$@"
}
