# DonutSMP RTP Mapper

A client-only Fabric mod for **Minecraft Java 1.21.11** that passively records the
landing coordinates of regional DonutSMP random teleports. It provides a live HUD,
a full scatter-map screen, requested-region counts, quadrant statistics, radial
buckets, and CSV/TXT exports.

## What this mod does — and does not do

The mod observes RTP commands that **you type yourself** and labels the next
detected landing with the requested region. It never sends a command, clicks a
menu, changes movement input, performs anti-AFK actions, or resumes mapping after
a disconnect.

DonutSMP's server chooses the destination coordinate. A client mod cannot expand
the server's RTP range, bypass its zone selection, or guarantee that two players
will receive different areas. The map helps measure the distribution; it does not
control it.

## Requirements

- Minecraft Java **1.21.11**
- Fabric Loader **0.19.5** (latest tested)
- Fabric API **0.141.6+1.21.11**
- Java **21**

## Installation

1. Install Fabric Loader for Minecraft 1.21.11.
2. Put Fabric API for 1.21.11 in your Minecraft **mods** folder.
3. Build this project with Gradle 9.2.1 using **gradle build**.
4. Put **build/libs/donut-smp-rtp-mapper-1.0.0.jar** in the same **mods** folder.

The source bundle intentionally does not include a Gradle wrapper binary. You can
regenerate it from a trusted Gradle 9.2.1 installation if you prefer **./gradlew**.

## Safe workflow

1. Join **donutsmp.net**.
2. Press **M** and click **Start Mapping**, or press **K**.
3. Manually type one of these commands:

   - **/rtp east**
   - **/rtp west**
   - **/rtp eu central**
   - **/rtp eu west**
   - **/rtp asia**
   - **/rtp oceania**

4. The outgoing-command event arms exactly one observation. After the client
   moves at least the configured threshold and the landing settles briefly, one
   sample is recorded with its **requestedRegion**.
5. Type the next command yourself when you are ready. The UI rotates a
   **Suggested next** region only as a sampling aid; it never executes it.

Only one command can be armed at a time. Sending any other command before the
landing finishes conservatively cancels that observation so an unrelated teleport
cannot be mislabeled. A bare **/rtp** that opens a server GUI is ignored because
the client event cannot reliably know which region you click.

## Keybinds

| Key | Action |
| --- | --- |
| **M** | Open or close the mapper screen |
| **N** | Toggle the HUD |
| **K** | Start or stop passive recording |

Starting recording is always a manual action. Rejoining a server does not restart
it.

## Detection model

For a recognized manual RTP command, the mapper stores the current X/Z position
and waits for a horizontal jump of at least 50 blocks by default. It then requires
10 consecutive stable client ticks before storing the final X/Y/Z position. The
whole attempt times out after 30 seconds by default.

Server messages containing an RTP/teleport/warmup failure or cancellation cancel
the armed observation. Detection is heuristic, so server behavior changes can
still cause a missed sample. The mod never infers a requested region from the
coordinate itself.

## Mapper screen

The main screen follows the dark, cyan-accented mapper style:

- live session and total sample counts
- current and last RTP coordinates
- armed requested region and suggested next manual command
- X/Z scatter plot with axes and distance rings
- mouse-wheel zoom, drag-to-pan, and reset view
- NE/NW/SE/SW percentages
- counts for NA East, NA West, EU Central, EU West, Asia, and Oceania
- radial buckets for 0–50k, 50–100k, 100–200k, and 200k+
- session/all views, reset, export, and settings controls

Map colors represent the **requested region**, not a claim about which physical
backend handled the landing.

## Configuration and saved data

Files are stored in:

~~~text
.minecraft/config/donut-smp-rtp-mapper/
├── config.json
├── samples.csv
├── samples.txt
├── export-session.csv
├── export-all.csv
└── sessions/
~~~

CSV columns are:

~~~text
timestamp,session_id,x,y,z,requested_region,distance_from_origin
~~~

Legacy seven-column files that used a **dimension** column are migrated before
new rows are appended; their old region value becomes **unknown**.

Settings include the teleport threshold, timeout, stabilization ticks, exact
allowed server host, suggestion-region list, auto-save, HUD visibility, mini-map,
and HUD corner. There is intentionally no automatic-command setting.

## Server rules

Even though this build is passive, you are responsible for following the current
DonutSMP rules and any limits on repeated RTP use.

Saved files contain exact coordinates in plain text. Keep exports private if the
locations matter to you.

## License

MIT
