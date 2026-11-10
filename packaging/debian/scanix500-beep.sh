#!/bin/sh
# scanix500-beep [-D <alsa device>] <color|bw|custom|finish>
# Plays a short sound through ALSA. Feedback must never fail loudly: always exit 0.
SOUNDS=${SCANIX500_SOUNDS:-/usr/share/scanix500/sounds}
dev=${SCANIX500_BEEP_DEVICE:-}
if [ "${1:-}" = "-D" ]; then
    dev=${2:-}
    shift 2 2>/dev/null || shift $#
fi
button=${1:-}

command -v aplay >/dev/null 2>&1 || exit 0

if [ -z "$dev" ] && aplay -l 2>/dev/null | grep -q 'Headphones'; then
    dev=plughw:CARD=Headphones
fi

case "$button" in
    finish) file=$SOUNDS/finish.wav ;;
    *) file=$SOUNDS/press.wav ;;
esac

aplay -q ${dev:+-D "$dev"} "$file" >/dev/null 2>&1 || true
exit 0
