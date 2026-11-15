# Local BlueStacks catalogue pipeline

The game runs on Wayne Home. GitHub schedules the work; Vercel still hosts the
website. No Docker host, paid service, public ADB port, or game-account upload is
needed. The runner must be online and Windows signed in. Sleep/offline time can
delay a scheduled job. The schedule is daily at 18:23 UTC (10:23 Vancouver in
winter, 11:23 in summer).

## Operator workflow

1. The **Check local Love Nikki catalogue** workflow first boots Android if needed
   and reads the installed resource version, before launching Love Nikki. It asks
   the game's version service whether patches are pending. If current, it opens
   the game and captures the installed catalogue, rebuilding only when data differs.
2. A changed catalogue marks the check as requiring attention. Open the game,
   finish any update/download or login, then run the workflow manually with
   **update_ready** checked. This generates a candidate artifact and change report.
3. Review the report and candidate. Run the workflow again with the snapshot ID
   in **publish_snapshot**. This creates a data-update pull request. Merge that PR
   to publish. The website reads the data repo; no frontend rebuild is required
   for later catalogue updates.

Pending patches stop capture and create a GitHub issue assigned to `wdoucette`,
with a mention, installed/target versions and the run link. Repeated checks of the
same target do not create another issue (the latest 100 bot-created issues are
checked). The run also fails for attention. Open the game and confirm any resource
download prompt; small patches can start automatically, larger ones may not.
After downloads complete, rerun with `update_ready` checked. Close the alert issue
after resolving it. Every completed server check supplies a summary and a 14-day
`server-version-report` artifact. Unknown/error results stop capture rather than
claiming the game is current. Existing reviewed-candidate publication is separate.

GitHub issue/mention and Actions email delivery depends on the owner's notification
settings. This pipeline does not provision a separate email service. The server
result describes resource patches for Global channel 12/resource type 2; it does
not certify login availability or absence of maintenance. A new patch can appear
after the check, so the operator still confirms completion before building.

The standard-library checker is `scripts/check_pending_update.py`. Wayne Home has
its game-derived protocol constants in `Pikki-Runner/version-check-config.json`
outside the repository, with `app_version`, `http_key`, and `local_key`. These were
derived from the validated installed game, not player credentials. Do not upload
game preferences or account files. The checker reads only `game_version` from the
preferences in memory and sends no player token, account or device identifier.
It pins the app version to 11.1.2; a changed APK requires revalidation. The currently
observed service selects HTTP with application-level encoding, not authenticated
TLS. Its response is only an update signal; the checker never fetches or executes
patches, and rejects unexpected routing. Setup on a different runner requires
revalidating and provisioning the local protocol configuration.

Only schedule and manual-dispatch events target the local runner, only on main.
Do not add pull_request or pull_request_target jobs to this runner. GitHub
recommends private repositories for self-hosted runners; this public data repo
must keep runner workflows restricted to trusted main-branch code. The runner
runs as the signed-in user and can access that user's machine.

## Local commands

Python 3.12 and Git are required. From this repository:

```powershell
python -m pip install -r scripts/emulator/requirements.txt
python -m unittest discover -s tests -v
python scripts/emulator_update.py check --launch
# Exit 10 means changed/needs attention; no tracked data was modified.
python scripts/emulator_update.py build --snapshot .emulator/snapshots/<ID> --ready
git switch -c catalogue/local-update
python scripts/emulator_update.py publish --candidate .emulator/snapshots/<ID>/candidate --reviewed
git diff --stat
# Commit the four intended data files, push the branch, and open a PR.
```

Snapshots, APK-derived tables, decoded Lua, and reports stay in ignored
`.emulator/`. Only the normalized bundle, version, stable-ID map, and overrides
snapshot are publishable. No `usrdat`, wardrobe files, tokens, player saves, or
whole APKs are published. Captures use APK tables plus the active patch overlay.
Unexpected patch layouts, changed files during capture, unsupported native
libraries, malformed tables, missing IDs, or disappearing published items stop
the update. A crashed process can leave `.emulator/update.lock`; verify no job is
running before removing that one file.

## Decoder and evidence

The tested library is ARM64 `libnnchanneleu.so`, SHA-256
`22af7e85f09f27e428642fb8d001cc8b2218dcfa728841f7221b1e4a7f6a70bf`.
The observed game screen reports 11.4.1(11112). The decoder emulates only the
native decoding functions using Unicorn with a small allowlist of memory/string
functions. Lua tables are parsed as literals, never executed. The game binary is
not bundled here. A Play Store APK update can change function locations, so a
new library hash requires revalidation; an asset patch using the same library
can be decoded automatically.

Observed snapshot: 38,844 table entries, of which 34,331 have `no_display=0` and
4,513 are hidden. All 34,331 visible entries map one-to-one to existing site IDs
and names. Recipe `no_display` gates are respected. Dependencies on hidden or
unavailable targets set `fut`. Decomposition eligibility follows the game's
`gdClothesResolveData[id].resolve == 1`; absence also means unavailable.

Paige's Overrides sheet remains supported, with the last local snapshot as a
fallback. Overrides cannot turn a game-forbidden item into a decomposable one.
The legacy requirement-count convention remains in place, including its
limitations around recolour cycles and alternative routes. These fields are
planning counts, not proof that a particular owned duplicate is safe to destroy.

## Stable IDs and source cutover

`data/game-id-map.json` is a one-time mapping from native game IDs to existing
Pikki IDs. It includes the existing `-1` ID for Nikki's Pinky, caused by the old
scraper treating offset zero as missing. Keeping it avoids breaking wardrobes.
New game items receive locally allocated IDs. Never regenerate the map from
Nikki Calc's ordering. Future third-party wardrobe imports must map by native
game ID, not assume their offsets equal Pikki IDs.

## Wardrobe and planning fields

The compact bundle includes descriptions and native `game_id` values, current
recipe quantities and gold fees, reverse crafting/evolution links, recolour dye
names/quantities and Material costs, unavailable recipes separately from active
recipes, and configured shop prices/currencies/unlock IDs. The top-level `suits`
table contains base completion parts, separately identified reward pieces, and
known recolour variants. Item `suit_ids` link to those records. Only suits whose
base pieces are all visible are included. Shop listings are not an assertion
that a time-limited item is on sale now. Stage drops and event acquisition
details are not yet decoded into player-facing sources.

The website's item page can show these details and calculate suit progress from
owned items. Existing status logic is retained; the wording clarifies that
“Extra clothes” does not certify safety for recolours or future dependencies.
Changes to the Overrides sheet also trigger a candidate even when game files
are unchanged.

The original Nikki Calc schedule is removed. Its manual workflow is disabled
unless `ENABLE_LEGACY_NIKKICALC=true` is deliberately set as a repository variable.
The old CLI updater refuses to overwrite a local-emulator bundle. Reverting to
the former source requires a deliberate data rollback and ID-compatibility review.

Runner installation on this machine: `C:\Users\wayne\Documents\Codex\Pikki-Runner`.
Windows task: **Pikki GitHub Runner**, at user sign-in. To stop it, stop that task
and its runner process; disable the task to prevent the next sign-in start.
The GitHub runner label is `pikki-bluestacks`.

The workflow uses the runner's per-user Python 3.12 at 
C:\Users\wayne\Documents\Codex\Pikki-Runner\Python312. Install official Python
with InstallAllUsers=0 and Include_launcher=0 at that location; the workflow
does not request a system-wide Python installation or administrator access.
