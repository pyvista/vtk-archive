#!/usr/bin/env bash
# Move the corpus between this checkout and R2. See docs/HOSTING.md "Where the
# corpus lives" for why it is in object storage and why R2.
#
# Credentials come from the environment and are never written to disk:
#   R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
#
# Usage: scripts/corpus.sh pull|push [extra rclone flags...]
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
action=${1:?usage: corpus.sh pull|push [rclone flags...]}
shift || true
extra=("$@")

for required in R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY; do
    if [[ -z ${!required:-} ]]; then
        echo "corpus.sh: $required is not set" >&2
        exit 2
    fi
done

bucket=${R2_BUCKET:-vtk-archive}

# rclone reads RCLONE_CONFIG_<REMOTE>_<KEY> when it has no config file for the
# remote. `no_check_bucket` is required by an object-scoped token, which cannot
# read bucket metadata. https://developers.cloudflare.com/r2/examples/rclone/
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_REGION=auto
export RCLONE_CONFIG_R2_ACL=private
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
export RCLONE_CONFIG_R2_ACCESS_KEY_ID=$R2_ACCESS_KEY_ID
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=$R2_SECRET_ACCESS_KEY
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

# `copy`, never `sync`. sync deletes on the far side to match the near side, so
# a pull that failed would empty the bucket on the next push. Nothing in the
# corpus is ever meant to be deleted.
data_flags=(--transfers 8 --checkers 16 --s3-chunk-size 64M)
file_flags=(--transfers 16 --checkers 32)
common=(--stats 30s --stats-one-line)
# A lock belongs to the machine holding it. Restoring one a dead run took would
# refuse every pass on the next run.
skip=(--exclude '.lock-*' --exclude '*.tmp')

case $action in
pull)
    rclone copy "r2:${bucket}/data" "$here/data" \
        "${skip[@]}" "${data_flags[@]}" "${common[@]}" "${extra[@]}"
    rclone copy "r2:${bucket}/attachments" "$here/attachments" \
        "${file_flags[@]}" "${common[@]}" "${extra[@]}"
    ;;
push)
    rclone copy "$here/data" "r2:${bucket}/data" \
        "${skip[@]}" "${data_flags[@]}" "${common[@]}" "${extra[@]}"
    rclone copy "$here/attachments" "r2:${bucket}/attachments" \
        "${file_flags[@]}" "${common[@]}" "${extra[@]}"
    ;;
*)
    echo "corpus.sh: unknown action '$action' (expected pull or push)" >&2
    exit 2
    ;;
esac
