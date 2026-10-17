#!/bin/sh
PYTHONPATH=/usr/lib/scanix500${PYTHONPATH:+:$PYTHONPATH}
export PYTHONPATH
exec /usr/bin/python3 -c 'import sys; from scanix500.cli import main; sys.exit(main())' "$@"
