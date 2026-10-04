# AALC Advance Team Setting

This document is under development.
## Retry limits

Open **Settings → Game settings** to change:

- **Recognition retry count**: 0 keeps each process's existing default. A positive value overrides general recognition retry loops in daily tasks, Mirror Dungeons, shops, battle entry, and returning to the main menu. Successful actions and loading waits keep their existing behavior.
- **Stall timeout (seconds)**: defaults to 90, with a minimum of 1. Applies to shared stall detection (including network connections and pathfinding) and loading while returning to the main menu. Existing recovery or failure handling still runs when a limit is exceeded. Explicit battle timeouts keep their existing values.
