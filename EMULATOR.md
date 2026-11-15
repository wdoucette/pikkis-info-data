# Local BlueStacks catalogue pipeline

The game runs on Wayne Home. GitHub schedules the work; Vercel still hosts the
website. No Docker host, paid service, public ADB port, or game-account upload is
needed. The runner must be online and Windows signed in. Sleep/offline time can
delay a scheduled job. The schedule is daily at 18:23 UTC (10:23 Vancouver in
winter, 11:23 in summer).

## Operator workflow

1. The **Check local Love Nikki catalogue** workflow opens BlueStacks and captures
   the installed catalogue. It hashes selected APK/static-table contents and
   rebuilds only when the catalogue differs from published data.
2. A changed catalogue marks the check as requiring attention. Open the game,
   finish any update/download or login, then run the workflow manually with
   **update_ready** checked. This generates a candidate artifact and change report.
3. Review the report and candidate. Run the workflow again with the snapshot ID
   in **publish_snapshot**. This creates a data-update pull request. Merge that PR
   to publish. The website reads the data repo; no frontend rebuild is required
   for later catalogue updates.

GitHub Actions failure email depends on the owner's GitHub notification settings.
This pipeline does not send email or provision a mail service. An unchanged
installed catalogue is not proof that the server has no new update: the game
may still be waiting for a download confirmation. No public server-update feed
has been established. This is deliberately a semi-automated first version.

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

The original Nikki Calc schedule is removed. Its manual workflow is disabled
unless `ENABLE_LEGACY_NIKKICALC=true` is deliberately set as a repository variable.
The old CLI updater refuses to overwrite a local-emulator bundle. Reverting to
the former source requires a deliberate data rollback and ID-compatibility review.

Runner installation on this machine: `C:\Users\wayne\Documents\Codex\Pikki-Runner`.
Windows task: **Pikki GitHub Runner**, at user sign-in. To stop it, stop that task
and its runner process; disable the task to prevent the next sign-in start.
The GitHub runner label is `pikki-bluestacks`.
