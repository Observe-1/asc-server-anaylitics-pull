# App Store weekly pull: setup

What it does: every Saturday at 05:00, Oblivion pulls your App Store Connect data (analytics, sales, reviews, version state, TestFlight builds, store rating), writes short summaries, and uploads them to Google Drive. The Saturday project digest reads them at 06:53 and writes the App Store numbers into each iOS project card.

```
Apple  -->  Oblivion (run.sh, Docker)  -->  Google Drive "App Store Analytics/latest"  -->  Saturday digest
            keys + raw data stay here        Docs + one Sheet, summaries only
```

Your Apple keys and the raw downloads never leave Oblivion. Only the summary Docs and the history Sheet go to Drive.

## A. Apple side (phone or PC, about 10 minutes)

➤ 1. App Store Connect > Users and Access > Integrations > App Store Connect API. If you see "Request Access", click it (Account Holder only).
➤ 2. Copy the **Issuer ID** at the top of that page.
➤ 3. Under Team Keys, press + three times. For each, download the .p8 file straight away (Apple allows one download) and note the **Key ID**:
- `asc-setup`: access **Admin**. Used once, then deleted.
- `asc-weekly-reports`: access **Sales and Reports**.
- `asc-weekly-apps`: access **App Manager**.
➤ 4. Copy your **vendor number** (Sales and Trends, or Payments and Financial Reports).

## B. Start Apple's clock (do this first; Apple needs 1 to 2 days to produce the first reports)

Apple only starts producing reports after a request exists, so the sooner this runs, the sooner data appears. It needs the Admin key and does not need Oblivion.

➤ 5. Put this folder somewhere, copy `config.env.example` to `config.env`, fill in the three key blocks and the vendor number. Put the .p8 files in `keys/`.
➤ 6. Run setup. Either:
- On Oblivion: `cd /mnt/user/appdata/asc-pull && ./run.sh setup`
- On your PC (if Oblivion is off): `pip install requests "pyjwt[crypto]" tzdata`, then `python asc_pull.py --env-file config.env --setup`. In `config.env` use Windows paths for the key files, for example `ASC_SETUP_KEY_FILE=C:\asc-pull\keys\AuthKey_ABC123.p8`.

You should see "created ONGOING request" and "created ONE_TIME_SNAPSHOT request" for each app. The snapshot backfills as much history as Apple holds. Rerunning is safe: it only creates what is missing.
➤ 7. Afterwards, revoke the `asc-setup` key in App Store Connect and delete the three `ASC_SETUP_*` lines from `config.env`.

## C. Oblivion

➤ 8. Create `/mnt/user/appdata/asc-pull/` (over SMB: `\\Oblivion\appdata\asc-pull`). Copy in everything from this folder. Put the .p8 files in `keys/`. Then in the Unraid terminal:
```
cd /mnt/user/appdata/asc-pull
chmod +x run.sh && chmod 600 config.env keys/*
./run.sh check
```
`check` makes one harmless read call per capability and prints OK or FAIL per key role. Fix any FAIL before moving on (see the table below).

➤ 9. Link Google Drive (once). Run `./run.sh rclone` and answer:
- `n` (new remote), name `gdrive`, storage `drive`
- client_id and client_secret: press Enter
- scope: choose **3** (drive.file, access to files rclone creates only)
- service_account_file: Enter; advanced config: `n`
- auto config: **`n`** (Oblivion has no browser). It prints a command starting `rclone authorize "drive" ...`.
- On your PC: install rclone (`winget install Rclone.Rclone`), paste that command in PowerShell, sign in with the Google account that owns your Drive, copy the token it prints, and paste it into the Oblivion prompt.
- shared drive: `n`, keep remote: `y`, quit: `q`.

➤ 10. First real run, once Apple's reports exist (1 to 2 days after step 6): `./run.sh`. Then open Drive: folder **App Store Analytics > latest** should hold Docs `STATUS`, `headline`, `reviews`, `detail_<app>` and a Sheet `history_weekly`. Open `STATUS` first: it says OK, PARTIAL or FAILED and lists every data gap.

➤ 11. Schedule it. Settings > User Scripts > Add New Script, name `asc-pull-weekly`, content: `/mnt/user/appdata/asc-pull/run.sh`. Set schedule to Custom: `0 5 * * 6` (Saturday 05:00). Check Settings > Date and Time is set to Europe/London. **Oblivion must be powered on at that time.** Failures raise an Unraid notification.

The Saturday digest is already wired to read the folder. If the folder does not exist yet, the digest says "App Store data not connected yet" and carries on.

## Simplified path (no Google Drive)

Set `ASC_SKIP_UPLOAD=1` in `config.env` and skip step 9. Files stay in `out/latest` on Oblivion, readable over SMB at `\Oblivionppdatasc-pull\out\latest`. One Admin key can do everything: set `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_FILE` instead of the three key blocks. The User Script can be created by writing `scripts/asc-pull-weekly/script` and a `schedule.json` entry on the flash share; press Apply on the User Scripts page afterwards.

## Apple rate limits

Apple lists about 150 report definitions per app. By default the pull checks only the ones the headline numbers need (about 6) and only the ONGOING request, which keeps a full run for 11 apps to about 80 seconds. `ASC_ALL_REPORTS=1` fetches every report for the detail files, but is slow and can hit HTTP 429. `ASC_WORKERS` (default 3) sets the number of parallel requests.

## What you get

`headline.md` per app: store rating, latest version and review state, latest TestFlight build, impressions, product page views, first-time downloads, redownloads, installs, sessions, crashes, purchases, Sales and Trends units and proceeds, new reviews. Each number comes with the previous week and the change. `detail_<app>.md` adds every Apple analytics report with top breakdowns (source, territory, device and so on). `history_weekly.csv` keeps one row per app, week and metric so trends build up over time.

Windows: analytics use the 7 days ending 2 days ago (Apple's data lags) against the 7 days before that. Reviews and store status are live. Unique counts are summed per day, so they are approximate.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `check` says 401 | Key ID, issuer ID or key path wrong, or the key was revoked. |
| `check` says 403 on reviews, versions or builds | Give `ASC_APPS_*` an Admin key, or leave it out and use one Admin key via `ASC_KEY_*`. |
| STATUS: "no report request exists" | Run `./run.sh setup` with the Admin key. |
| STATUS: "stopped due to inactivity" | Apple stops idle requests. Create a new Admin key and run setup again. |
| STATUS: "no reports yet" | Normal for 1 to 2 days after setup. |
| Zeros or gaps for a tiny app | Apple may withhold very small counts for privacy. Expected before launch. |
| Nothing in Drive | Read `out/run.log`. Run `./run.sh upload` to retry the upload alone. |
| Digest says data is stale | STATUS is FAILED or older than 8 days. Check `out/run.log` and Oblivion was on. |

## Changing things

- Only some apps: set `ASC_APPS_INCLUDE=openprayer,shove` in `config.env`.
- Manual run any time: `./run.sh`. Pull without upload: `./run.sh pull`.
- Tests (mock Apple API, 49 checks): `python tests/run_tests.py`.
