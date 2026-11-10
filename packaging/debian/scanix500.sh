#!/bin/sh
PYTHONPATH=/usr/lib/scanix500${PYTHONPATH:+:$PYTHONPATH}
export PYTHONPATH
# Only the scanner backends the server needs (see /etc/scanix500/sane.d/dll.conf).
: "${SANE_CONFIG_DIR:=/etc/scanix500/sane.d:}"
export SANE_CONFIG_DIR
exec /usr/bin/python3 -c 'import sys; from scanix500.cli import main; sys.exit(main())' "$@"
