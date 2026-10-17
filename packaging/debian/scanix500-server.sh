#!/bin/sh
PYTHONPATH=/usr/lib/scanix500${PYTHONPATH:+:$PYTHONPATH}
export PYTHONPATH
exec /usr/bin/python3 -m scanix500.server.cli "$@"
