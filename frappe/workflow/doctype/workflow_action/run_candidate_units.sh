#!/usr/bin/env bash
# Source5967 unit-only runner. No build, pull, install, sites or production access.
set -euo pipefail

source_root=/tmp/frappe-workflow-candidate-5967
source_head=${1:?supply the frozen full Git SHA approved for this run}
[[ $source_head =~ ^[0-9a-f]{40}$ ]]
[[ $(git -C "$source_root" rev-parse HEAD) == "$source_head" ]]
[[ -z $(git -C "$source_root" status --porcelain) ]]
run_dir=$(mktemp -d /tmp/frappe-workflow5967-units.XXXXXX)
container_name="frappe-workflow-5967-units-${source_head:0:12}-$$"
cid_file="$run_dir/container.cid"
printf 'SOURCE_HEAD=%s RUN_DIR=%s NAME=%s\n' "$source_head" "$run_dir" "$container_name"

cleanup() {
    local run_status=$?
    trap - EXIT INT TERM
    if [[ -s $cid_file ]]; then
        local container_id identity
        read -r container_id < "$cid_file"
        if [[ ! $container_id =~ ^[0-9a-f]{64}$ ]]; then
            printf 'CLEANUP_REFUSED invalid CID\n'
            exit 90
        fi
        if identity=$(timeout 10s docker container inspect --format '{{.Id}} {{.Name}} {{index .Config.Labels "source5967"}}' "$container_id" 2>"$run_dir/inspect.log"); then
            if [[ $identity != "$container_id /$container_name $source_head" ]]; then
                printf 'CLEANUP_REFUSED ownership mismatch\n'
                exit 91
            fi
            timeout --kill-after=2s 10s docker container rm --force "$container_id"
        elif ! rg -q 'No such (object|container)' "$run_dir/inspect.log"; then
            printf 'CLEANUP_UNKNOWN inspect failed\n'
            exit 92
        fi
        if timeout 10s docker container inspect "$container_id" >"$run_dir/readback.log" 2>&1; then
            printf 'CLEANUP_FAILED container still present\n'
            exit 93
        elif ! rg -q 'No such (object|container)' "$run_dir/readback.log"; then
            printf 'CLEANUP_UNKNOWN readback failed\n'
            exit 94
        fi
    fi
    printf 'CONTAINER_ABSENT UNIT_EXIT=%s RUN_DIR=%s\n' "$run_status" "$run_dir"
    exit "$run_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

declarations=()
for path in \
    frappe/__init__.py \
    frappe/utils/caching.py \
    frappe/utils/user.py \
    frappe/permissions.py \
    frappe/model/workflow.py \
    frappe/workflow/doctype/workflow_action/workflow_action.py \
    frappe/workflow/doctype/workflow_action/test_workflow_action.py \
    frappe/workflow/doctype/workflow_action/candidate_unit_runner.py; do
    digest=$(sha256sum "$source_root/$path")
    declarations+=("${digest%% *}:$path")
done

set +e
timeout --signal=TERM --kill-after=5s 120s docker run --rm \
    --name "$container_name" --cidfile "$cid_file" --label "source5967=$source_head" \
    --pull=never --network=none --read-only --cap-drop=ALL \
    --security-opt=no-new-privileges --pids-limit=128 --memory=1g --memory-swap=1g --cpus=1 \
    --user=frappe \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
    --tmpfs /home/frappe/frappe-bench/sites:rw,nosuid,nodev,noexec,size=1m \
    --tmpfs /home/frappe/frappe-bench/logs:rw,nosuid,nodev,noexec,size=1m \
    --mount "type=bind,src=$source_root,dst=/candidate,readonly" \
    --workdir=/tmp --entrypoint=/usr/bin/env \
    sha256:f9de222710a9ec83223a6dd183eb03fc317df86fb662dd1f095308e70970e2a4 \
    -i PATH=/home/frappe/frappe-bench/env/bin:/usr/bin:/bin \
    PYTHONPATH=/candidate PYTHONDONTWRITEBYTECODE=1 \
    python /candidate/frappe/workflow/doctype/workflow_action/candidate_unit_runner.py \
    "$source_head" "${declarations[@]}" 2>&1 | tee "$run_dir/units.log"
unit_status=${PIPESTATUS[0]}
set -e
exit "$unit_status"
