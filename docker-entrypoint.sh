#!/bin/sh
set -e

# If the config directory is empty (e.g., mounted from an empty host dir via Portainer),
# copy the default configurations into it.
if [ ! -f "/app/config/gateway.json" ]; then
    echo "Initializing default config files in /app/config..."
    cp -r /app/default_config/* /app/config/
fi

# Execute the main command (e.g., python src/main.py)
exec "$@"
