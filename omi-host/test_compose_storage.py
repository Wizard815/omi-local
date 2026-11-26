"""The compose file names every bucket an upload on the self-hosted stack writes to.

backend/utils/other/storage.py reads most bucket names with no default; on local storage an
unset one is a 500 on the upload that needs it (creating an app with a logo, a chat attachment).
Each bucket storage.py reads with no default is either named in docker-compose.yml or listed
below as a hosted-only path that the self-hosted stack does not reach.

Run:  python3 test_compose_storage.py     (no pytest required)
"""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPOSE = (HERE / "docker-compose.yml").read_text()
STORAGE = (HERE.parent / "backend" / "utils" / "other" / "storage.py").read_text()

# Hosted-only: GCS audio pipelines, desktop release hosting, and screen frames (which raise a
# clear "not configured" error of their own instead of crashing).
HOSTED_ONLY = {
    "BUCKET_POSTPROCESSING",
    "BUCKET_DESKTOP_UPDATES",
    "BUCKET_SCREEN_FRAMES",
    "BUCKET_CANONICAL_AUDIO",
    "BUCKET_AUDIO_DERIVED",
    "BUCKET_BATCH_PIPELINE",
    "BUCKET_OBJECT_LAKE",
}


def buckets_without_default():
    # os.getenv('BUCKET_X') with no second argument (a default of '' counts as none).
    names = set(re.findall(r"os\.getenv\('(BUCKET_[A-Z_]+)'\)", STORAGE))
    names |= set(re.findall(r"os\.getenv\('(BUCKET_[A-Z_]+)', ''\)", STORAGE))
    return names


def test_upload_buckets_are_named_in_compose():
    missing = sorted(
        name
        for name in buckets_without_default() - HOSTED_ONLY
        if not re.search(rf"^\s+{name}: \$\{{{name}:-[a-z0-9-]+\}}$", COMPOSE, re.M)
    )
    assert not missing, f"name these buckets in docker-compose.yml (with a default): {missing}"


def test_the_ones_that_broke_are_covered():
    for name in ("BUCKET_PLUGINS_LOGOS", "BUCKET_APP_THUMBNAILS", "BUCKET_CHAT_FILES"):
        assert name in buckets_without_default(), name
        assert re.search(rf"^\s+{name}: \$\{{{name}:-", COMPOSE, re.M), name


def main():
    tests = [test_upload_buckets_are_named_in_compose, test_the_ones_that_broke_are_covered]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
