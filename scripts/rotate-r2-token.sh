#!/usr/bin/env bash
# Rotate the R2 credential end to end, printing no secret.
#
# Mints a replacement bucket-scoped token, proves it against the live bucket,
# installs it into the GitHub secrets and the local env file, revokes the old
# token, and finally revokes the bootstrap token that minted it. Every value
# lives in a shell variable or a pipe; the only output is status.
#
# See docs/MAINTENANCE.md "Rotating the R2 credential" for where the bootstrap
# token comes from and why this goes through a script at all.
set -euo pipefail

: "${CF_BOOTSTRAP_TOKEN:?set CF_BOOTSTRAP_TOKEN to a token with Account API Tokens Write}"
: "${BOOTSTRAP_TOKEN_ID:?set BOOTSTRAP_TOKEN_ID so this script can revoke it}"
: "${CF_ACCOUNT_ID:?set CF_ACCOUNT_ID to the Cloudflare account holding the bucket}"
acct=$CF_ACCOUNT_ID
bucket=${R2_BUCKET:-vtk-archive}
repo=${GH_REPO:-pyvista/vtk-archive}
old_id=${OLD_R2_TOKEN_ID:-}
envfile=$HOME/.config/vtk-archive/env
api=https://api.cloudflare.com/client/v4

# Object Read & Write on one bucket, which is what the corpus push and pull need.
ITEM_WRITE=2efd5506f9c8494dacb1fa10a3e7d5b6
ITEM_READ=6a018a9f2fc74eb6b293b0c548f38b39

cf() {
    local method=$1 path=$2 body=${3:-}
    if [[ -n $body ]]; then
        curl -sS -X "$method" "$api$path" \
            -H "Authorization: Bearer $CF_BOOTSTRAP_TOKEN" \
            -H "Content-Type: application/json" --data "$body"
    else
        curl -sS -X "$method" "$api$path" -H "Authorization: Bearer $CF_BOOTSTRAP_TOKEN"
    fi
}

# Reads a Cloudflare envelope and prints only its errors, never its result.
errors_of() {
    python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as exc:
    print(f"unparseable response: {exc}")
    sys.exit(0)
if d.get("success"):
    print("")
else:
    print("; ".join(f'"'"'{e.get("code")}: {e.get("message")}'"'"' for e in d.get("errors") or []) or "failed with no error given")
'
}

field_of() {
    python3 -c "
import json, sys
d = json.load(sys.stdin)
print((d.get('result') or {}).get('$1') or '')
"
}

echo "== 1. minting a replacement token scoped to r2:${bucket}"
body=$(python3 -c "
import json, datetime, sys
acct, bucket, w, r = sys.argv[1:5]
print(json.dumps({
    'name': f'vtk-archive corpus (Object Read & Write) {datetime.date.today().isoformat()}',
    'policies': [{
        'effect': 'allow',
        'resources': {f'com.cloudflare.edge.r2.bucket.{acct}_default_{bucket}': '*'},
        'permission_groups': [{'id': w}, {'id': r}],
    }],
}))
" "$acct" "$bucket" "$ITEM_WRITE" "$ITEM_READ")

resp=$(cf POST "/accounts/$acct/tokens" "$body")
err=$(printf '%s' "$resp" | errors_of)
if [[ -n $err ]]; then
    echo "FAIL: could not create the token: $err"
    exit 1
fi
new_id=$(printf '%s' "$resp" | field_of id)
new_value=$(printf '%s' "$resp" | field_of value)
unset resp
if [[ ${#new_id} -ne 32 || -z $new_value ]]; then
    echo "FAIL: the response carried no usable token"
    exit 1
fi
# The S3 secret access key is the sha256 of the token value, never the value.
new_secret=$(printf '%s' "$new_value" | shasum -a 256 | cut -d' ' -f1)
unset new_value
echo "   minted ${new_id:0:8}... and derived a ${#new_secret}-character secret"

echo "== 2. proving it against the live bucket before replacing anything"
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_REGION=auto
export RCLONE_CONFIG_R2_ACL=private
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
export RCLONE_CONFIG_R2_ACCESS_KEY_ID=$new_id
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=$new_secret
export RCLONE_CONFIG_R2_ENDPOINT="https://${acct}.r2.cloudflarestorage.com"

probe=".probe-rotate-$$"
# A new token is not usable the instant it is created, so give it a few tries.
wrote=no
for _ in 1 2 3 4 5 6; do
    if printf ok | rclone rcat "r2:${bucket}/${probe}" >/dev/null 2>&1; then
        wrote=yes
        break
    fi
    sleep 5
done
if [[ $wrote != yes ]]; then
    echo "FAIL: the new credential cannot write to r2:${bucket}. Nothing changed."
    echo "      the unused token is ${new_id}; revoke it."
    exit 1
fi
got=$(rclone cat "r2:${bucket}/${probe}" 2>/dev/null || true)
rclone delete "r2:${bucket}/${probe}" >/dev/null 2>&1 || true
if [[ $got != ok ]]; then
    echo "FAIL: wrote but could not read back. Nothing changed."
    exit 1
fi
objects=$(rclone size "r2:${bucket}/data" --json 2>/dev/null |
    python3 -c 'import json,sys;print(json.load(sys.stdin)["count"])')
if [[ ${objects:-0} -lt 1 ]]; then
    echo "FAIL: the new credential sees no objects under data/. Nothing changed."
    exit 1
fi
echo "   wrote, read back, deleted, and listed ${objects} objects under data/"

echo "== 3. installing it"
printf '%s' "$new_id" | gh secret set R2_ACCESS_KEY_ID -R "$repo" >/dev/null
printf '%s' "$new_secret" | gh secret set R2_SECRET_ACCESS_KEY -R "$repo" >/dev/null
echo "   GitHub secrets updated on ${repo}"

umask 077
mkdir -p "$(dirname "$envfile")"
tmp=$(mktemp)
if [[ -f $envfile ]]; then
    grep -v -E '^R2_(ACCOUNT_ID|ACCESS_KEY_ID|SECRET_ACCESS_KEY|BUCKET)=' "$envfile" >"$tmp" || true
fi
{
    echo "R2_ACCOUNT_ID=${acct}"
    echo "R2_BUCKET=${bucket}"
    echo "R2_ACCESS_KEY_ID=${new_id}"
    echo "R2_SECRET_ACCESS_KEY=${new_secret}"
} >>"$tmp"
mv "$tmp" "$envfile"
chmod 600 "$envfile"
echo "   ${envfile} updated"

if [[ -n $old_id ]]; then
    echo "== 4. revoking the old token ${old_id:0:8}..."
    err=$(cf DELETE "/accounts/$acct/tokens/$old_id" | errors_of)
    if [[ -n $err ]]; then
        echo "   WARNING: could not revoke it: $err"
        echo "   revoke ${old_id} by hand."
    else
        echo "   revoked"
    fi
fi

echo "== 5. revoking the bootstrap token ${BOOTSTRAP_TOKEN_ID:0:8}..."
err=$(cf DELETE "/accounts/$acct/tokens/$BOOTSTRAP_TOKEN_ID" | errors_of)
if [[ -n $err ]]; then
    echo "   WARNING: could not revoke it: $err"
    echo "   revoke ${BOOTSTRAP_TOKEN_ID} by hand; it can mint tokens."
else
    echo "   revoked"
fi

echo
echo "DONE. The live R2 credential was never printed and is in no transcript."
echo "New access key id: ${new_id}  (an identifier, not a secret)"
