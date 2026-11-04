#!/usr/bin/env bash
# Run by windows_setup.ps1 as the normal Ubuntu user, never the Windows username.
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }
root_docker() {
  # sudo may clear DOCKER_HOST or select root's saved context. Pin every call explicitly.
  sudo env -u DOCKER_CONTEXT docker --host unix:///var/run/docker.sock "$@"
}
action=setup
directory=sewer-agent
archive=
port=8000
project=sewer-agent-wsl
helper="$(dirname -- "$0")/wsl_source.py"
while (($#)); do
  (($# >= 2)) || fail "Missing argument value."
  case "$1" in
    --action) action=$2 ;;
    --directory) directory=$2 ;;
    --archive) archive=$2 ;;
    --port) port=$2 ;;
    --project) project=$2 ;;
    *) fail "Unknown argument: $1" ;;
  esac
  shift 2
done
case "$action" in setup|start|stop|status|keepalive) ;; *) fail "Unknown action." ;; esac
[[ $(id -u) != 0 ]] || fail "Finish Ubuntu's first-run user creation, then use its normal default user."
command -v python3 >/dev/null || fail "Ubuntu Python 3 is missing; install python3, then retry."
root=$(python3 "$helper" path --directory "$directory")
# All actions target the Ubuntu-local Docker socket, never a remote Docker context.
export DOCKER_HOST=unix:///var/run/docker.sock
unset DOCKER_CONTEXT
control="$root/.wsl-control"

if [[ $action == keepalive ]]; then
  [[ -d $control ]] || exit 0
  exec 9>"$control/keepalive.lock"
  flock -n 9 || exit 0
  while [[ -f $control/running ]]; do sleep 5; done
  exit 0
fi
if [[ $action != status ]]; then
  mkdir -p -- "$HOME/.cache/sewer-agent"
  exec 8>"$HOME/.cache/sewer-agent/setup.lock"
  flock -n 8 || fail "Another installation/start/stop is running. Retry after it finishes."
fi

if [[ $action == setup ]]; then
  [[ -f $archive ]] || fail "Source archive is missing."
  [[ $(uname -m) == x86_64 ]] || fail "This deployment currently requires Windows/WSL x86-64."
  grep -qi microsoft /proc/sys/kernel/osrelease || fail "Run this installer inside WSL 2."
  source /etc/os-release
  [[ $ID == ubuntu ]] || fail "This installer supports Ubuntu only."
  case "$VERSION_ID" in 24.04|26.04) ;; *) fail "Use Ubuntu-24.04 (or supported Ubuntu 26.04)." ;; esac
  sudo -v
  if [[ $(ps -p 1 -o comm=) != systemd ]]; then
    sudo python3 "$helper" systemd
    printf '%s\n' "Systemd enabled. Close this Ubuntu session, terminate only this distribution, then repeat setup."
    exit 20
  fi
  [[ -e /dev/dxg ]] || fail "WSL GPU is unavailable. Update the Windows NVIDIA driver and WSL; do not install a Linux display driver."
  gpu_check=/usr/lib/wsl/lib/nvidia-smi
  [[ -x $gpu_check ]] || gpu_check=$(command -v nvidia-smi || true)
  [[ -n $gpu_check ]] && "$gpu_check" -L || fail "The Windows NVIDIA driver is not available inside WSL."

  if ! command -v docker >/dev/null; then
    # Do not silently uninstall another container runtime or distribution Docker package.
    for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
      if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
        fail "Existing $package detected. Follow Docker's migration instructions before setup."
      fi
    done
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
      "${UBUNTU_CODENAME:-$VERSION_CODENAME}" | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  sudo systemctl start docker
  root_docker compose version >/dev/null || fail "Docker Compose v2 is missing. Install docker-compose-plugin from Docker's official repository."
  operating_system=$(root_docker info --format '{{.OperatingSystem}}')
  [[ $operating_system != *"Docker Desktop"* ]] || fail "Docker Desktop integration is active. Use a separate Ubuntu distribution for this Engine installer."
  if ! root_docker info --format '{{json .Runtimes}}' | python3 -c 'import json,sys; sys.exit(0 if "nvidia" in json.load(sys.stdin) else 1)'; then
    [[ -z $(root_docker ps -q) ]] || fail "Other containers are running. Stop them before enabling the NVIDIA runtime; this installer will not restart their Docker daemon."
    if ! command -v nvidia-ctk >/dev/null; then
      sudo apt-get update
      sudo apt-get install -y ca-certificates curl gnupg
      temporary=$(mktemp -d)
      trap 'rm -rf -- "$temporary"' EXIT
      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey -o "$temporary/key"
      gpg --batch --yes --dearmor -o "$temporary/key.gpg" "$temporary/key"
      sudo install -m 0644 "$temporary/key.gpg" /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
      curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
      sudo apt-get update
      sudo apt-get install -y nvidia-container-toolkit
    fi
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
  fi
  root_docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L
  # New docker group membership is used only for these child processes, not a new login session.
  sudo usermod -aG docker "$(id -un)"
  user=$(id -un)
  as_user=(sudo -u "$user" -g docker -- env "HOME=$HOME" "DOCKER_HOST=$DOCKER_HOST")
  "${as_user[@]}" python3 "$helper" install --directory "$directory" --archive "$archive"
  "${as_user[@]}" python3 "$helper" configure --directory "$directory" --port "$port" --project "$project"
else
  python3 "$helper" check --directory "$directory"
  command -v docker >/dev/null || fail "Docker is missing; run setup first."
  as_user=(sudo -u "$(id -un)" -g docker -- env "HOME=$HOME" "DOCKER_HOST=$DOCKER_HOST")
  if [[ $action == start ]]; then sudo systemctl start docker; fi
fi

cd -- "$root"
dc=("${as_user[@]}" docker compose --env-file .env.docker)
# Refuse to attach a new installation to another directory's identically named project.
configuration=$("${dc[@]}" config --format json)
project=$(printf '%s' "$configuration" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
existing=$("${as_user[@]}" docker ps -a --filter "label=com.docker.compose.project=$project" --format '{{.Label "com.docker.compose.project.working_dir"}}')
while IFS= read -r origin; do
  [[ -z $origin || $origin == "$root" ]] || fail "Compose project $project belongs to another directory. Choose a unique -ProjectName for a new installation."
done <<< "$existing"

case "$action" in
  status)
    "${dc[@]}" ps -a
    ;;
  stop)
    "${dc[@]}" stop
    rm -f -- "$control/running"
    printf '%s\n' "Stopped this project. Containers, volumes, models and settings are retained."
    ;;
  start)
    "${dc[@]}" up -d --no-build --pull never --wait --wait-timeout 360
    ;;
  setup)
    available=$(df -Pk "$root" | awk 'NR==2 {print $4}')
    ((available >= 25 * 1024 * 1024)) || fail "Less than 25 GiB free in Ubuntu. Free build space before repeating setup."
    "${as_user[@]}" python3 scripts/docker_setup.py up
    ;;
esac

if [[ $action == setup || $action == start ]]; then
  mkdir -p -- "$control"
  touch -- "$control/running"
fi
if [[ $action != stop ]]; then
  port=$(printf '%s' "$configuration" | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["api"]["ports"][0]["published"])')
  printf 'Open http://127.0.0.1:%s and use Settings to configure your API.\n' "$port"
fi
