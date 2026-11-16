#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

SERVICE_NAME="rice-reborn.service"
APP_USER="rice"
APP_GROUP="rice"
APP_DIR="/opt/rice-reborn"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"
CONTROL_PATH="/usr/local/sbin/rice-rebornctl"
SOURCE_RECORD="${APP_DIR}/.source-path"
SELF_PATH="${BASH_SOURCE[0]}"

usage() {
  cat <<'EOF'
Rice Reborn Linux launcher
  sudo bash deploy/rice-reborn.sh             Menu
  sudo bash deploy/rice-reborn.sh install     Install from this source
  sudo rice-rebornctl start|stop|restart|status|logs
  sudo rice-rebornctl update                  Git fast-forward + staged environment
  sudo rice-rebornctl check                   Read-only environment check
  sudo rice-rebornctl repair                  Rebuild a verified environment
  sudo rice-rebornctl state-check             Read-only local database check
  sudo rice-rebornctl preset-va               VA thresholds only, preserve capital/data
  sudo rice-rebornctl backup                  Protected backup, briefly stops backend
  sudo rice-rebornctl password                Change password, revoke old sessions
  sudo rice-rebornctl access                  Show current network/access settings
  sudo rice-rebornctl access-set MODE [IPS]    direct / ssh / https; no firewall changes
EOF
}

require_linux() { [[ "$(uname -s)" == Linux ]] || { echo 'Linux required.' >&2; exit 1; }; }
require_root() { [[ "${EUID}" -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }; }

is_source() {
  [[ -f "$1/pyproject.toml" && -f "$1/rice_reborn/main.py" && -f "$1/rice_reborn/maintenance.py" &&
     -f "$1/requirements.lock" && -f "$1/deploy/rice-reborn.sh" && -f "$1/.env.example" ]]
}

resolve_source() {
  local candidate script_dir
  if [[ -n "${RICE_SOURCE_DIR:-}" ]]; then
    is_source "${RICE_SOURCE_DIR}" || { echo 'Invalid RICE_SOURCE_DIR.' >&2; return 1; }
    (cd -- "${RICE_SOURCE_DIR}" && pwd); return
  fi
  script_dir="$(cd -- "$(dirname -- "${SELF_PATH}")" && pwd)"
  candidate="$(cd -- "${script_dir}/.." && pwd)"
  if is_source "${candidate}"; then printf '%s\n' "${candidate}"; return; fi
  if [[ -f "${SOURCE_RECORD}" ]]; then
    IFS= read -r candidate <"${SOURCE_RECORD}"
    if is_source "${candidate}"; then (cd -- "${candidate}" && pwd); return; fi
  fi
  echo 'Source not found. Run Install from a complete source checkout.' >&2; return 1
}

ensure_git() {
  if command -v git >/dev/null && command -v ssh >/dev/null; then return; fi
  . /etc/os-release
  case "${ID}" in
    ubuntu) apt-get update; apt-get install -y git openssh-client ca-certificates ;;
    almalinux) dnf install -y git openssh-clients ca-certificates ;;
    *) echo "Unsupported distribution: ${ID}" >&2; return 1 ;;
  esac
}

read_repo_url() {
  local url authority
  read -r -p 'Git URL (HTTPS or SSH, no embedded token): ' url
  case "${url}" in https://*|ssh://*|git@*:*) ;; *) echo 'Invalid Git URL.' >&2; return 1 ;; esac
  [[ "${url}" != *'?'* && "${url}" != *'#'* && "${url}" != *[$' \t\r\n']* ]] || { echo 'Use a plain repository URL without query/fragment/whitespace.' >&2; return 1; }
  authority="${url#*://}"; authority="${authority%%/*}"
  if [[ "${url}" == https://* && "${authority}" == *@* ]] ||
     [[ "${url}" == ssh://* && "${authority}" == *:*@* ]]; then
    echo 'Use SSH or a credential manager; do not embed credentials in URLs.' >&2; return 1
  fi
  printf '%s\n' "${url}"
}

install_or_clone() {
  require_root
  local source_dir repo_url target
  if source_dir="$(resolve_source 2>/dev/null)"; then
    RICE_SOURCE_DIR="${source_dir}" bash "${source_dir}/deploy/rice-reborn.sh" install; return
  fi
  repo_url="$(read_repo_url)"
  ensure_git
  target="${APP_DIR}/source"
  [[ ! -e "${target}" ]] || { echo 'Source target exists; refusing to overwrite.' >&2; return 1; }
  install -d -m 0755 "${APP_DIR}"
  git clone -- "${repo_url}" "${target}"
  is_source "${target}" || { echo 'Incomplete Rice Reborn checkout.' >&2; return 1; }
  RICE_SOURCE_DIR="${target}" bash "${target}/deploy/rice-reborn.sh" install
}

update_app() {
  require_root
  local source_dir repo_root dirty repo_url target
  source_dir="$(resolve_source)"
  ensure_git
  if ! repo_root="$(git -c safe.directory="${source_dir}" -C "${source_dir}" rev-parse --show-toplevel 2>/dev/null)"; then
    echo '[GIT] ZIP source: clone to a separate folder; keep current source and data.'
    repo_url="$(read_repo_url)"
    target="$(mktemp -d "${APP_DIR}/source-git-XXXXXXXX")"
    git clone -- "${repo_url}" "${target}"
    is_source "${target}" || { echo 'Incomplete Git checkout; old service unchanged.' >&2; return 1; }
    RICE_SOURCE_DIR="${target}" bash "${target}/deploy/rice-reborn.sh" install
    return
  fi
  [[ "$(cd -- "${repo_root}" && pwd)" == "${source_dir}" ]] || { echo 'Source must be repository root.' >&2; return 1; }
  dirty="$(git -c safe.directory="${source_dir}" -C "${source_dir}" status --porcelain)"
  [[ -z "${dirty}" ]] || { echo 'Local changes found. Nothing reset or stashed.' >&2; return 1; }
  git -c safe.directory="${source_dir}" -C "${source_dir}" pull --ff-only
  bash -n "${source_dir}/deploy/rice-reborn.sh"
  RICE_SOURCE_DIR="${source_dir}" bash "${source_dir}/deploy/rice-reborn.sh" install
}

show_menu() {
  cat <<'EOF'

========== RICE REBORN ==========
  1. Start backend / show web access
  2. Update code + verified environment
  3. Load preset VA (preserve capital / API / trades)
  4. Install / check / repair environment
  5. Diagnostics / logs / local data (read-only)
  6. Stop backend
  7. Restart backend (BOT starts OFF)
  8. Status
  9. Network access / Cloud Firewall info
  B. Protected backup
  P. Change administrator password
  0. Exit
================================
EOF
}

menu_action() {
  case "$1" in
    1) echo start ;; 2) echo update ;; 3) echo preset-va ;; 4) echo environment ;;
    5) echo diagnostics ;; 6) echo stop ;; 7) echo restart ;; 8) echo status ;;
    9) echo network ;; b|B) echo backup ;; p|P) echo password ;; *) return 1 ;;
  esac
}

run_menu_action() { bash "${SELF_PATH}" "$1"; }
menu() {
  require_root
  local choice action
  while true; do
    show_menu
    if ! read -r -p 'Choose [0-9/B/P]: ' choice; then return 0; fi
    [[ "${choice}" != 0 ]] || return 0
    if action="$(menu_action "${choice}")"; then
      if run_menu_action "${action}"; then echo 'Done.'
      else echo 'Failed. No automatic reset of account data.'; fi
    else echo 'Invalid choice.'; fi
  done
}

runtime() {
  [[ -x "${APP_DIR}/.venv/bin/python" ]] || { echo 'Environment missing. Choose 4 > install / repair.' >&2; return 1; }
  runuser -u "${APP_USER}" -- env RICE_HOME="${APP_DIR}" RICE_ENV_FILE="${APP_DIR}/data/.env" PYTHONDONTWRITEBYTECODE=1 \
    "${APP_DIR}/.venv/bin/python" -I -B "$@"
}
service_active() { systemctl is-active --quiet "${SERVICE_NAME}"; }

with_stopped() {
  require_root
  local was_active=0 result=0
  if service_active; then was_active=1; systemctl stop "${SERVICE_NAME}"; fi
  if [[ "$1" == backup ]]; then
    if env RICE_HOME="${APP_DIR}" RICE_ENV_FILE="${APP_DIR}/data/.env" "${APP_DIR}/.venv/bin/python" -I -B \
      -m rice_reborn.maintenance backup --directory "${APP_DIR}/backups"; then :; else result=$?; fi
  elif [[ "$1" == password ]]; then
    if runtime -m rice_reborn.main --set-password; then :; else result=$?; fi
  else
    if runtime -m rice_reborn.maintenance "$@"; then :; else result=$?; fi
  fi
  if [[ "${was_active}" == 1 ]]; then systemctl start "${SERVICE_NAME}" || result=1; fi
  return "${result}"
}

service_command() {
  require_root
  systemctl "$1" "${SERVICE_NAME}"
  if [[ "$1" == start || "$1" == restart ]]; then echo 'Backend started. BOT starts OFF.'; show_access; fi
}

check_environment() {
  require_root
  echo '[CHECK] Read-only: no package installs, timezone changes or backend start.'
  if [[ -f /etc/os-release ]]; then . /etc/os-release; echo "OS: ${PRETTY_NAME}"; fi
  command -v git >/dev/null && git --version || echo '[WARN] Git missing (needed for Git updates only).'
  if command -v timedatectl >/dev/null; then timedatectl show -p NTPSynchronized --value || true; fi
  local lock="${APP_DIR}/.venv/requirements.lock"
  if [[ -f "${lock}" ]]; then runtime -m rice_reborn.maintenance check --lock "${lock}"
  else runtime -m rice_reborn.maintenance check; fi
  runtime -m pip check
  echo '[CHECK] Review firewall/provider security group separately.'
}

environment_menu() {
  local choice
  echo '1. Read-only check'; echo '2. Install / repair packages'; echo '0. Back'
  read -r -p 'Choose [0-2]: ' choice
  case "${choice}" in 1) bash "${SELF_PATH}" check ;; 2) bash "${SELF_PATH}" bootstrap ;; 0|'') return 0 ;; *) return 1 ;; esac
}
diagnostics_menu() {
  local choice
  echo '1. Environment (read-only)'; echo '2. Logs'; echo '3. Local state (read-only)'; echo '4. Service sandbox assessment'; echo '0. Back'
  read -r -p 'Choose [0-4]: ' choice
  case "${choice}" in
    1) bash "${SELF_PATH}" check ;; 2) bash "${SELF_PATH}" logs ;; 3) bash "${SELF_PATH}" state-check ;;
    4) systemd-analyze security --no-pager "${SERVICE_NAME}" ;; 0|'') return 0 ;; *) return 1 ;;
  esac
}

show_access() {
  runtime -m rice_reborn.maintenance access-show
  echo 'Binance API trusted IP is the VPS outbound IP, not the home browser IP.'
}
network_menu() {
  local choice mode allowed
  show_access
  echo '1. Direct IP / HTTP (you manage firewall)'; echo '2. SSH tunnel / loopback'; echo '3. Existing HTTPS proxy / loopback'; echo '0. Back'
  read -r -p 'Choose [0-3]: ' choice
  case "${choice}" in 1) mode=direct ;; 2) mode=ssh ;; 3) mode=https ;; 0|'') return 0 ;; *) return 1 ;; esac
  allowed=''
  with_stopped access-set --mode "${mode}" --allow-ips "${allowed}"
  show_access
}

render_unit() {
  cat <<EOF
[Unit]
Description=Rice Reborn Binance Spot VA terminal
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
Environment=RICE_HOME=${APP_DIR}
Environment=RICE_ENV_FILE=${APP_DIR}/data/.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${APP_DIR}/.venv/bin/python -I -B -m rice_reborn.main
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${APP_DIR}/data
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
Nice=5
MemoryHigh=768M
MemoryMax=1G
TasksMax=48

[Install]
WantedBy=multi-user.target
EOF
}

wait_healthy() {
  local attempt success=0
  for attempt in {1..12}; do
    if service_active && runtime -c 'import json,urllib.request; from rice_reborn.config import Environment; e=Environment(); r=json.load(urllib.request.urlopen("http://127.0.0.1:"+str(e.port)+"/healthz",timeout=2)); assert r.get("ok") is True' >/dev/null 2>&1; then
      success=$((success+1)); [[ "${success}" -lt 2 ]] || return 0
    else success=0; fi
    sleep 1
  done
  return 1
}

activate_environment() {
  local candidate="$1" previous='' legacy='' old_unit='' was_active=0
  [[ "${candidate}" == "${APP_DIR}/venvs/"* && -x "${candidate}/bin/python" ]] || { echo 'Invalid candidate environment.' >&2; return 1; }
  if [[ -e "${APP_DIR}/.venv" ]]; then
    previous="$(readlink -f -- "${APP_DIR}/.venv")"
    [[ "${previous}" == "${APP_DIR}/"* ]] || { echo 'Current environment points outside app root.' >&2; return 1; }
  fi
  if service_active; then was_active=1; systemctl stop "${SERVICE_NAME}"; fi
  if [[ -d "${APP_DIR}/data/v1" ]]; then
    if ! env RICE_HOME="${APP_DIR}" RICE_ENV_FILE="${APP_DIR}/data/.env" "${candidate}/bin/python" -I -B \
      -m rice_reborn.maintenance backup --directory "${APP_DIR}/backups"; then
      [[ "${was_active}" != 1 ]] || systemctl start "${SERVICE_NAME}"
      return 1
    fi
  fi
  if [[ -f "${UNIT_PATH}" ]]; then old_unit="${APP_DIR}/.previous-unit"; cp -- "${UNIT_PATH}" "${old_unit}"; fi
  if [[ -d "${APP_DIR}/.venv" && ! -L "${APP_DIR}/.venv" ]]; then
    legacy="${APP_DIR}/venvs/legacy-$(date -u +%Y%m%d-%H%M%S)-$$"
    mv -- "${APP_DIR}/.venv" "${legacy}"; previous="${legacy}"
  fi
  local link="${APP_DIR}/.venv-next-$$-${RANDOM}"
  ln -s -- "${candidate}" "${link}"
  mv -Tf -- "${link}" "${APP_DIR}/.venv"
  render_unit >"${UNIT_PATH}"; chmod 0644 "${UNIT_PATH}"
  systemctl daemon-reload; systemctl enable "${SERVICE_NAME}"
  systemctl reset-failed "${SERVICE_NAME}" || true
  if systemctl restart "${SERVICE_NAME}" && wait_healthy; then
    echo '[OK] Service and local health endpoint verified.'; return 0
  fi
  echo '[FAIL] New service failed health check; candidate/data kept for diagnosis.' >&2
  systemctl stop "${SERVICE_NAME}" || true
  if [[ -n "${previous}" ]]; then
    link="${APP_DIR}/.venv-rollback-$$-${RANDOM}"
    ln -s -- "${previous}" "${link}"
    mv -Tf -- "${link}" "${APP_DIR}/.venv"
    [[ -z "${old_unit}" ]] || cp -- "${old_unit}" "${UNIT_PATH}"
    systemctl daemon-reload
    [[ "${was_active}" != 1 ]] || systemctl start "${SERVICE_NAME}"
    echo '[ROLLBACK] Previous environment restored. Financial data was not rolled back.' >&2
  fi
  return 1
}

install_app() {
  require_root
  local source_dir python_bin candidate mode allowed port
  source_dir="$(resolve_source)"
  . /etc/os-release
  case "${ID}:${VERSION_ID}" in
    ubuntu:24.04|ubuntu:26.04) export DEBIAN_FRONTEND=noninteractive; apt-get update; apt-get install -y python3 python3-venv ca-certificates; python_bin=python3 ;;
    almalinux:9|almalinux:9.*) dnf install -y python3.11 python3.11-pip ca-certificates; python_bin=python3.11 ;;
    almalinux:10|almalinux:10.*) dnf install -y python3 python3-pip ca-certificates; python_bin=python3 ;;
    *) echo "Supported: Ubuntu 24.04/26.04, AlmaLinux 9/10. Detected ${ID} ${VERSION_ID}" >&2; return 1 ;;
  esac
  "${python_bin}" -c 'import sys; assert sys.version_info >= (3,11)'
  if ! getent group "${APP_GROUP}" >/dev/null; then groupadd --system "${APP_GROUP}"; fi
  if ! id "${APP_USER}" >/dev/null 2>&1; then useradd --system --gid "${APP_GROUP}" --home-dir "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"; fi
  [[ ! -L "${APP_DIR}" && ! -L "${APP_DIR}/data" && ! -L "${APP_DIR}/venvs" ]] || { echo 'App/data/environment roots must not be symlinks.' >&2; return 1; }
  install -d -m 0750 -o root -g "${APP_GROUP}" "${APP_DIR}" "${APP_DIR}/venvs"
  install -d -m 0750 -o "${APP_USER}" -g "${APP_GROUP}" "${APP_DIR}/data"
  if [[ ! -f "${APP_DIR}/data/.env" ]]; then
    if [[ -f "${APP_DIR}/.env" ]]; then install -m 0600 -o "${APP_USER}" -g "${APP_GROUP}" "${APP_DIR}/.env" "${APP_DIR}/data/.env"
    else install -m 0600 -o "${APP_USER}" -g "${APP_GROUP}" "${source_dir}/.env.example" "${APP_DIR}/data/.env"; fi
  fi
  [[ ! -L "${APP_DIR}/data/.env" ]] || { echo 'Refusing symlink .env.' >&2; return 1; }
  chown "${APP_USER}:${APP_GROUP}" "${APP_DIR}/data/.env"; chmod 0600 "${APP_DIR}/data/.env"
  candidate="$(mktemp -d "${APP_DIR}/venvs/release-XXXXXXXX")"
  chgrp "${APP_GROUP}" "${candidate}"; chmod 0750 "${candidate}"
  # Stage pip changes separately; never change a package currently running.
  (umask 022; "${python_bin}" -m venv "${candidate}"; "${candidate}/bin/python" -I -B -m pip install --disable-pip-version-check -r "${source_dir}/requirements.lock"; "${candidate}/bin/python" -I -B -m pip install --disable-pip-version-check --no-deps "${source_dir}")
  install -m 0644 "${source_dir}/requirements.lock" "${candidate}/requirements.lock"
  "${candidate}/bin/python" -I -B -m pip check
  runuser -u "${APP_USER}" -- env RICE_HOME="${APP_DIR}" RICE_ENV_FILE="${APP_DIR}/data/.env" PYTHONDONTWRITEBYTECODE=1 \
    "${candidate}/bin/python" -I -B -m rice_reborn.maintenance check --lock "${candidate}/requirements.lock"
  if [[ ! -f "${UNIT_PATH}" ]]; then
    if service_active; then echo 'Stop service before creating the initial password.' >&2; return 1; fi
    mode="$(RICE_ENV_FILE="${APP_DIR}/data/.env" "${candidate}/bin/python" -I -B -c 'import os; from dotenv import dotenv_values; print(dotenv_values(os.environ["RICE_ENV_FILE"]).get("RICE_ACCESS_MODE") or "direct")')"
    allowed="$(RICE_ENV_FILE="${APP_DIR}/data/.env" "${candidate}/bin/python" -I -B -c 'import os; from dotenv import dotenv_values; print(dotenv_values(os.environ["RICE_ENV_FILE"]).get("RICE_ALLOWED_IPS") or "")')"
    port="$(RICE_ENV_FILE="${APP_DIR}/data/.env" "${candidate}/bin/python" -I -B -c 'import os; from dotenv import dotenv_values; print(dotenv_values(os.environ["RICE_ENV_FILE"]).get("RICE_PORT") or "8080")')"
    runuser -u "${APP_USER}" -- env RICE_HOME="${APP_DIR}" RICE_ENV_FILE="${APP_DIR}/data/.env" "${candidate}/bin/python" -I -B -m rice_reborn.maintenance access-set --mode "${mode}" --allow-ips "${allowed}" --port "${port}"
  fi
  if ! env RICE_ENV_FILE="${APP_DIR}/data/.env" "${candidate}/bin/python" -I -B -c 'import os,sys; from dotenv import dotenv_values; sys.exit(0 if dotenv_values(os.environ["RICE_ENV_FILE"]).get("RICE_PASSWORD_HASH") else 1)'; then
    if service_active; then echo 'Stop service before creating the initial password.' >&2; return 1; fi
    runuser -u "${APP_USER}" -- env RICE_HOME="${APP_DIR}" RICE_ENV_FILE="${APP_DIR}/data/.env" "${candidate}/bin/python" -I -B -m rice_reborn.main --set-password
  fi
  activate_environment "${candidate}"
  install -m 0755 "${source_dir}/deploy/rice-reborn.sh" "${CONTROL_PATH}"
  printf '%s\n' "${source_dir}" >"${SOURCE_RECORD}"; chmod 0600 "${SOURCE_RECORD}"
  echo '[OK] Installed. API/settings/data kept. Firewall, SSH, TLS and timezone unchanged.'
  show_access
}

main() {
  require_linux
  local action="${1:-menu}"
  case "${action}" in
    menu) menu ;; bootstrap) install_or_clone ;; install|repair) install_app ;; update) update_app ;;
    start|stop|restart) service_command "${action}" ;;
    status) require_root; systemctl status "${SERVICE_NAME}" --no-pager ;;
    logs) require_root; journalctl -u "${SERVICE_NAME}" -f ;;
    check) check_environment ;; state-check) require_root; runtime -m rice_reborn.maintenance state-check ;;
    preset-va|backup|password) with_stopped "${action}" ;;
    environment) environment_menu ;; diagnostics) diagnostics_menu ;; network) require_root; network_menu ;;
    access) require_root; show_access ;;
    access-set) require_root; [[ $# -ge 2 ]] || { echo 'Usage: access-set direct|ssh|https [IP/CIDR,...]' >&2; return 1; }; with_stopped access-set --mode "$2" --allow-ips "${3:-}" ;;
    help|-h|--help) usage ;; *) usage; return 2 ;;
  esac
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
