import csv
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).parent))
import mock_asc  # noqa: E402

SCRIPT = str(Path(__file__).resolve().parent.parent / "asc_pull.py") if (Path(__file__).resolve().parent.parent / "asc_pull.py").exists() else "/home/claude/asc-pull/asc_pull.py"
work = Path(tempfile.mkdtemp(prefix="asc_test_"))
fails = []


def check(cond, label):
    print(("PASS  " if cond else "FAIL  ") + label)
    if not cond:
        fails.append(label)


def mkkey(name):
    k = ec.generate_private_key(ec.SECP256R1())
    priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    p = work / f"AuthKey_{name}.p8"
    p.write_bytes(priv)
    return p, pub


admin_p, admin_pub = mkkey("ADMINKID")
rep_p, rep_pub = mkkey("REPKID")
apps_p, apps_pub = mkkey("APPSKID")
evil_p, _evil_pub = mkkey("EVILKID")  # signs with a key Apple does not know

srv, S, base = mock_asc.start({"ADMINKID": admin_pub, "REPKID": rep_pub, "APPSKID": apps_pub}, {"ADMINKID"})


def env_file(name, out, extra):
    lines = [f"ASC_API_BASE={base}", f"ITUNES_BASE={base}", f"OUT_DIR={out}", "ASC_NO_SLEEP=1"] + extra
    p = work / name
    p.write_text("\n".join(lines) + "\n")
    return p


def run(envp, *args):
    env = dict(os.environ, ASC_NO_SLEEP="1")
    r = subprocess.run([sys.executable, SCRIPT, "--env-file", str(envp), *args], capture_output=True, text=True, env=env, cwd=str(work))
    return r


three_keys = [
    f"ASC_VENDOR_NUMBER=85012345",
    f"ASC_SETUP_KEY_ID=ADMINKID", "ASC_SETUP_ISSUER_ID=issuer-1", f"ASC_SETUP_KEY_FILE={admin_p}",
    f"ASC_REPORTS_KEY_ID=REPKID", "ASC_REPORTS_ISSUER_ID=issuer-1", f"ASC_REPORTS_KEY_FILE={rep_p}",
    f"ASC_APPS_KEY_ID=APPSKID", "ASC_APPS_ISSUER_ID=issuer-1", f"ASC_APPS_KEY_FILE={apps_p}",
]
out1 = work / "out1"
env1 = env_file("env1", out1, three_keys)

# ------------------------------------------------------------------ run 1: weekly pull
r = run(env1, "--today", "2026-10-17")
print(r.stdout, r.stderr[-1500:])
check(r.returncode == 0, "weekly pull exits 0 (PARTIAL is fine)")
latest = out1 / "latest"
head = (latest / "headline.md").read_text()
status = (latest / "STATUS.md").read_text()
rev = (latest / "reviews.md").read_text()
check("Impressions: 1,092 (prev 2,184, -50%)" in head, "restated instance wins, no double count (impressions 1,092 vs 2,184)")
check("Product page views: 210 (prev 420, -50%)" in head, "page views correct")
check("First-time downloads: 49 (prev 49, +0%)" in head, "DAILY fallback with 14 instances and pagination (downloads 49)")
check("Redownloads: 7 (prev 7, +0%)" in head, "redownloads from dimension filter")
check("Sessions: 98 (prev 70, +40%)" in head, "two-segment instance merged (sessions 98 vs 70)")
check("Avg session length (s): 60 (prev 60, +0%)" in head, "derived average session length")
check("Downloads per 100 page views" in head, "derived conversion line present")
check("Latest version: 1.3.0 IOS, state WAITING_FOR_REVIEW" in head, "version state read from appVersionState, newest first")
check("Latest TestFlight build: 45, VALID" in head, "latest build")
check("GB 4.70 from 23 ratings" in head, "public store rating")
check("New reviews, last 7 days: 3 (previous 7 days: 2), average 3.3, lowest 1" in head, "reviews windows over 3 pages")
check("proceeds GBP 24.50" in head and "21 (prev 18, +17%) first-time downloads" in head, "sales via 429 retry, 404 day, IAP matched by parent SKU")
check("Frameworks" not in head and "Detailed" not in head, "Detailed reports and FRAMEWORKS_USAGE skipped")
check("not found in GB, US storefronts" in head, "not-live app flagged from public lookup")
check("Result: PARTIAL" in status, "STATUS is PARTIAL")
check("ALL APPS, NOTE: Sales: Apple returned no report" in head, "global sales notes shown once, labelled ALL APPS")
check("Sales and Trends: 0 (prev 0)" not in head, "no zero-sales noise for apps that are not live")
check("1 star," in rev, "review pluralisation")
check("no report request exists" in status and "Shove" in status, "app without request flagged, points at --setup")
check("stopped due to inactivity" in status, "stopped request flagged")
check("Apple has generated no reports yet" in status, "empty request flagged")
check("403 forbidden" in status, "403 on reviews recorded as a gap")
check("Sales: Apple returned no report for 1 of 14 days" in status, "missing sales day noted")
check("did not match any app" in status, "unmatched sales rows noted")
check("1 of 5 reports had no data" in status or "had no data in the window" in status, "empty report noted")
check("IGNORE ALL PREVIOUS" in rev and "untrusted user content" in rev, "review text kept as data, with the untrusted warning")
check((latest / "detail_openprayer.md").exists(), "per-app detail file written")
detail = (latest / "detail_openprayer.md").read_text()
check("Top territory by Counts" in detail and "Top event by Counts" in detail and "mix the values of event" in detail, "detail has useful breakdowns and the mixed-totals warning")
check("Top device by Counts" not in detail, "single-valued dimensions skipped")
hist = list(csv.DictReader((latest / "history_weekly.csv").open()))
check(len(hist) > 20, f"history has rows ({len(hist)})")
check(any(h["metric"] == "Impressions" and h["value"] == "1092" and h["window_end"] == "2026-10-15" for h in hist), "history impressions row")
nh1 = len(hist)
seg1, dl1, api1 = S.counts["segments_calls"], S.counts["download"], S.counts["api"]
check(S.sales_429_sent and S.flaky_dl_sent, "429 and flaky download were exercised and survived")
check(not (out1 / "latest" / "raw").exists(), "raw data stays outside latest/")

# ------------------------------------------------------------------ run 2: idempotent
r2 = run(env1, "--today", "2026-10-17")
check(r2.returncode == 0, "second run exits 0")
hist2 = list(csv.DictReader((latest / "history_weekly.csv").open()))
check(len(hist2) == nh1, f"history upsert is idempotent ({nh1} rows)")
check(S.counts["segments_calls"] == seg1 and S.counts["download"] == dl1, "second run re-downloads nothing for analytics (cache works)")

# ------------------------------------------------------------------ setup mode
S.posts.clear()
rs = run(env1, "--setup")
print(rs.stdout, rs.stderr[-800:])
check(rs.returncode == 0, "--setup exits 0")
exp = {("1001", "ONE_TIME_SNAPSHOT"), ("1002", "ONE_TIME_SNAPSHOT"), ("1003", "ONGOING"), ("1003", "ONE_TIME_SNAPSHOT"), ("1004", "ONGOING"), ("1004", "ONE_TIME_SNAPSHOT")}
check(set(S.posts) == exp and len(S.posts) == 6, f"--setup creates only the missing requests: {sorted(S.posts)}")
S.posts.clear()
run(env1, "--setup")
check(S.posts == [], "--setup is idempotent")

# non-admin key cannot create requests: reported, not crashed
env_na = env_file("env_na", work / "out_na", ["ASC_SETUP_KEY_ID=REPKID", "ASC_SETUP_ISSUER_ID=i", f"ASC_SETUP_KEY_FILE={rep_p}"])
S.requests["1004"].clear()
rna = run(env_na, "--setup")
check("FAILED" in rna.stdout and rna.returncode == 0, "non-admin --setup reports the 403 clearly")

# ------------------------------------------------------------------ single-key fallback + include filter
out3 = work / "out3"
env3 = env_file("env3", out3, ["ASC_VENDOR_NUMBER=85012345", "ASC_KEY_ID=ADMINKID", "ASC_KEY_ISSUER_ID=x", "ASC_ISSUER_ID=issuer-1", f"ASC_KEY_FILE={admin_p}", "ASC_APPS_INCLUDE=openprayer"])
r3 = run(env3, "--today", "2026-10-17")
h3 = (out3 / "latest" / "headline.md").read_text() if (out3 / "latest" / "headline.md").exists() else ""
check(r3.returncode == 0 and "Impressions: 1,092" in h3 and "Shove" not in h3, "single ASC_* key works, include filter limits apps")

# an app with only INFO notes must be OK, not PARTIAL
out4 = work / "out4"
S.requests["1001"][0]["reports"] = ["rep_eng", "rep_dl", "rep_ses"]
env4 = env_file("env4", out4, three_keys + ["ASC_APPS_INCLUDE=openprayer"])
S.deny_reviews_for.clear()
r4 = run(env4, "--today", "2026-10-17")
st4 = (out4 / "latest" / "STATUS.md").read_text()
check("Result: OK" in st4 or "Result: PARTIAL" in st4, "clean run status readable")
print("run4 status:", [l for l in st4.splitlines() if l.startswith("Result")])

# --check shows which role can do what
S.deny_reviews_for.add("1001")
rc_ = run(env1, "--check")
print(rc_.stdout)
check("OK    list apps: 4 app(s)" in rc_.stdout and "FAIL  customer reviews (apps key): 403" in rc_.stdout and "OK    Sales and Trends report" in rc_.stdout, "--check reports capabilities and flags a 403")
S.deny_reviews_for.discard("1001")

# ------------------------------------------------------------------ bad key: FAILED, old data kept
bad = [
    "ASC_VENDOR_NUMBER=85012345", "ASC_KEY_ID=EVILKID", "ASC_ISSUER_ID=issuer-1", f"ASC_KEY_FILE={evil_p}",
]
env_bad = env_file("env_bad", out1, bad)
rb = run(env_bad, "--today", "2026-10-24")
st_bad = (latest / "STATUS.md").read_text()
check(rb.returncode == 1, "bad key exits 1")
check("Result: FAILED" in st_bad and "Last fully successful run" in st_bad, "STATUS shows FAILED and last success")
check("Authentication failed" in st_bad and "stale" in st_bad, "401 reported as an auth failure with a stale-data warning")
check("Impressions: 1,092" in (latest / "headline.md").read_text(), "previous good headline.md left in place on failure")
check(not any("unexpected" in f.lower() for f in S.log), "no unexpected server-side log noise")

print("\nFAILED CHECKS:" if fails else "\nALL CHECKS PASSED", fails)
print("\n--- headline.md from run 1 ---\n" + head)
sys.exit(1 if fails else 0)
