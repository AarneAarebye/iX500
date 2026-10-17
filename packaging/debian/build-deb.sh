#!/bin/sh
# Builds dist/scanix500-server_<version>_all.deb from this checkout.
# Needs dpkg-deb (Debian/Ubuntu, or `brew install dpkg` on a Mac).
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)
[ -n "$VERSION" ] || { echo "build-deb: no version in pyproject.toml" >&2; exit 1; }
HERE="$ROOT/packaging/debian"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

install -d "$STAGE/DEBIAN" "$STAGE/usr/lib/scanix500" "$STAGE/usr/bin" \
    "$STAGE/lib/systemd/system" "$STAGE/etc/scanix500" \
    "$STAGE/usr/share/doc/scanix500-server"
cp -R "$ROOT/src/scanix500" "$STAGE/usr/lib/scanix500/"
find "$STAGE/usr/lib/scanix500" -name __pycache__ -prune -exec rm -rf {} +
install -m 0755 "$HERE/scanix500-server.sh" "$STAGE/usr/bin/scanix500-server"
install -m 0755 "$HERE/scanix500.sh" "$STAGE/usr/bin/scanix500"
install -m 0644 "$HERE/scanix500-server.service" "$STAGE/lib/systemd/system/"
install -m 0644 "$HERE/server.conf" "$STAGE/etc/scanix500/server.conf"
install -m 0644 "$HERE/copyright" "$STAGE/usr/share/doc/scanix500-server/copyright"
printf 'scanix500-server (%s) unstable; urgency=medium\n\n  * Release notes: https://github.com/AarneAarebye/iX500/releases\n\n -- Aarne Aarebye <25765996+AarneAarebye@users.noreply.github.com>  %s\n' \
    "$VERSION" "$(LC_ALL=C date -R)" | gzip -9n > "$STAGE/usr/share/doc/scanix500-server/changelog.Debian.gz"
for script in postinst prerm postrm; do install -m 0755 "$HERE/$script" "$STAGE/DEBIAN/$script"; done
install -m 0644 "$HERE/conffiles" "$STAGE/DEBIAN/conffiles"
SIZE=$(du -sk "$STAGE" | cut -f1)
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" "$HERE/control.in" > "$STAGE/DEBIAN/control"
(cd "$STAGE" && find . -type f ! -path './DEBIAN/*' -exec md5sum {} + | sed 's|  \./|  |' > DEBIAN/md5sums)
find "$STAGE" -type d -exec chmod 0755 {} +
mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/scanix500-server_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "$OUT"
