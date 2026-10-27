#!/usr/bin/env bash
# Notarizes and staples a signed .dmg with an App Store Connect API key:
#   APPLE_API_KEY_P8 (contents of the .p8), APPLE_API_KEY_ID, APPLE_API_ISSUER_ID
#
#   installer/notarize.sh dist/iPhoneManager-<version>-macOS-<arch>.dmg
#
# Apple's check is automatic and usually answers in a few minutes. If it fails or takes longer
# than NOTARY_TIMEOUT, the script says so and exits 0: the release still ships the signed .dmg
# (macOS then asks the user to allow it in System Settings), and the next release tries again.
set -uo pipefail

DMG="$1"
TIMEOUT="${NOTARY_TIMEOUT:-30m}"
KEY="$(mktemp -t notary).p8"
trap 'rm -f "$KEY"' EXIT
printf '%s' "$APPLE_API_KEY_P8" > "$KEY"

echo "Submitting $DMG for notarization (timeout $TIMEOUT)"
if xcrun notarytool submit "$DMG" --key "$KEY" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER_ID" \
     --wait --timeout "$TIMEOUT" --output-format json | tee "$DMG.notary.json" | grep -q '"status" *: *"Accepted"'; then
  xcrun stapler staple "$DMG" && xcrun stapler validate "$DMG"
  spctl --assess --type open --context context:primary-signature -v "$DMG" || true
  echo "Notarized: $DMG"
else
  echo "::warning::Notarization of $(basename "$DMG") didn't finish; the release ships it signed but not notarized."
  cat "$DMG.notary.json" || true
  ID="$(sed -n 's/.*"id" *: *"\([^"]*\)".*/\1/p' "$DMG.notary.json" | head -1)"
  if [ -n "$ID" ]; then
    xcrun notarytool log "$ID" --key "$KEY" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER_ID" || true
  fi
fi
rm -f "$DMG.notary.json"
exit 0
