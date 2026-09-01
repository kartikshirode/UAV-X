#!/usr/bin/env python3
"""One definition of "the source that produced this result".

Every run record carries `source_tree_sha256`, and the final package has to prove the runs it
submits as evidence came off the source it is submitting. That only works if the
runner and the checker compute the same number the same way, so both call this.

What it covers is deliberately narrower than the repository. Editing a sentence
in stage-1/plan.md must not invalidate a week of runs, so only the paths that
can change what a simulation does are in scope:

    uavx_ws/  scenarios/  scripts/  stage-1/setup/

The hash is over "path<tab>blob" lines, sorted, where blob is git's own object
id for the file content. That makes a hash taken from a commit and a hash taken
from a clean checkout of that commit identical, which is the whole point.

    python3 scripts/source_tree_hash.py              # the working tree
    python3 scripts/source_tree_hash.py --ref <sha>  # a commit

Prints one 64-character hex digest.
"""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE_PREFIXES = ("uavx_ws/", "scenarios/", "scripts/", "stage-1/setup/")


def blob_id(data: bytes) -> str:
    """Git's object id for a file's content."""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def in_scope(path: str) -> bool:
    return path.startswith(SOURCE_PREFIXES)


def from_ref(ref: str) -> dict:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-tree", "-r", "-z", ref],
        capture_output=True, text=True, check=True).stdout
    files = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[1] != "blob":
            continue
        if in_scope(path):
            files[path] = parts[2]
    return files


def from_worktree() -> dict:
    """Hash the files on disk, letting git apply its own filters.

    Not by reading the bytes and hashing them. This repo forces LF in the
    object database and the working copy on Windows holds CRLF, so hashing raw
    bytes made a clean checkout disagree with the commit it came from. Every
    submitted run record would then have failed to match the frozen source, and the
    submission would have been blocked by a checker that was wrong.

    `git hash-object` runs the same clean filters git uses on the way in, so a
    clean tree and its commit produce identical ids. One process for the whole
    list rather than one per file.
    """
    # Chunk 1.7. This listed tracked files only, so a run made before its
    # own runner was committed recorded a source_tree_sha256 that did not
    # cover the code that produced it. The docstring calls this hash wider
    # than commit_sha, which ignores uncommitted code; that was true of
    # modified tracked files and false of new ones. --others with
    # --exclude-standard adds the untracked files git would show you and
    # respects .gitignore, so build output and the ignored spec receipt
    # stay out of it.
    listing = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z",
         "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True).stdout
    paths = sorted({p for p in listing.split("\0")
                    if p and in_scope(p) and (REPO / p).is_file()})
    if not paths:
        return {}
    hashed = subprocess.run(
        ["git", "-C", str(REPO), "hash-object", "--stdin-paths"],
        input="\n".join(paths) + "\n", capture_output=True, text=True,
        check=True).stdout.split()
    if len(hashed) != len(paths):
        raise RuntimeError(
            f"git hash-object returned {len(hashed)} ids for {len(paths)} "
            f"paths; refusing to guess which is which")
    return dict(zip(paths, hashed))


def digest(files: dict) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(f"{path}\t{files[path]}\n".encode("utf-8"))
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref")
    ap.add_argument("--list", action="store_true",
                    help="print the files that went into the hash")
    a = ap.parse_args()
    files = from_ref(a.ref) if a.ref else from_worktree()
    if not files:
        print("no source files in scope; refusing to hash nothing",
              file=sys.stderr)
        return 1
    if a.list:
        for path in sorted(files):
            print(f"{files[path]}  {path}")
    print(digest(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
