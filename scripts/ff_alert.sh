#!/usr/bin/env bash
# Open / close the "ff-blocked" GitHub issue based on the ForexFactory page status.
#
#   scripts/ff_alert.sh blocked   -> open an issue (only if none is open), assigned to the repo owner
#   scripts/ff_alert.sh ok        -> comment "✅ আবার ঠিক হয়েছে" and close any open ff-blocked issue
#
# Env (set by the workflow): GH_TOKEN, GH_REPO, OWNER, FF_HTTP, FF_REASON, FF_FALLBACK, RUN_URL
set -euo pipefail

LABEL="ff-blocked"
TITLE="⚠️ ForexFactory ব্লক করেছে — Actual ডেটা আসছে না"
mode="${1:-}"
now_dhaka="$(TZ=Asia/Dhaka date +'%d %b %Y, %I:%M %p') (ঢাকা)"

open_issues() {
  gh issue list --label "$LABEL" --state open --limit 20 --json number --jq '.[].number'
}

case "$mode" in
  blocked)
    gh label create "$LABEL" --color D93F0B \
      --description "ForexFactory calendar page blocked for GitHub Actions" --force >/dev/null
    existing="$(open_issues | head -n1)"
    if [[ -n "$existing" ]]; then
      echo "ff-blocked issue #$existing already open — not creating another."
      exit 0
    fi
    # keep the Markdown table intact if a value contains a pipe
    FF_HTTP="${FF_HTTP//|/／}"; FF_REASON="${FF_REASON//|/／}"; FF_FALLBACK="${FF_FALLBACK//|/／}"
    body="$(mktemp)"
    cat >"$body" <<MD
ForexFactory-এর ক্যালেন্ডার পেজ GitHub-এর সার্ভার থেকে খোলা যাচ্ছে না, তাই **Actual** ডেটা আপডেট হচ্ছে না।

| | |
|---|---|
| সময় | ${now_dhaka} |
| HTTP স্ট্যাটাস | ${FF_HTTP:-জানা যায়নি} |
| কারণ | ${FF_REASON:-জানা যায়নি} |
| এখন যা চলছে | ${FF_FALLBACK:-ব্যাকআপ নেই} |
| রান লগ | ${RUN_URL:-—} |

**এর মানে:** ক্যালেন্ডার (সময়, Forecast, Previous) আর সম্ভাবনা চলতে থাকবে, কিন্তু নতুন Actual নাও আসতে পারে। আগামী সপ্তাহের ইভেন্টও বাদ যেতে পারে।

**আপনার কিছু করতে হবে না।** প্রতি ২ ঘণ্টায় আবার চেষ্টা হবে। ঠিক হলে এই ইস্যুতে "✅ আবার ঠিক হয়েছে" লিখে নিজে থেকেই বন্ধ হয়ে যাবে।
MD
    if ! gh issue create --title "$TITLE" --label "$LABEL" --assignee "${OWNER}" --body-file "$body"; then
      echo "Assigning failed (owner may be an org) — creating without assignee."
      gh issue create --title "$TITLE" --label "$LABEL" --body-file "$body"
    fi
    ;;
  ok)
    issues="$(open_issues || true)"
    if [[ -z "$issues" ]]; then
      echo "No open ff-blocked issue."
      exit 0
    fi
    for n in $issues; do
      gh issue comment "$n" --body "✅ আবার ঠিক হয়েছে — ${now_dhaka} থেকে ForexFactory পেজ আবার খুলছে, Actual ডেটা আসছে। ${RUN_URL:-}"
      gh issue close "$n" --reason completed
    done
    ;;
  *)
    echo "usage: $0 blocked|ok" >&2
    exit 2
    ;;
esac
