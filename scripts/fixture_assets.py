#!/usr/bin/env python3
"""Build the smallest real PDF and video that a valid submission needs.

Round 5 finding 2: the submission fixture suite had never once seen a package
the checker accepts. Every negative case was mutated from a baseline that was
itself broken, so a permanently failing W5 checker would have passed the suite.

Making the baseline valid means the suite needs a PDF that pdftotext can read
and a video that ffmpeg can decode. Both are built here rather than committed,
so nothing binary lives in the repository and the fixtures cannot drift from
what the checker asks for.

The PDF is hand-assembled. reportlab is not a dependency this project should
grow for a test asset, and the format needed here is small: pages, a font, text
streams and an xref table.
"""

import subprocess
import zlib
from pathlib import Path


def _escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def write_pdf(path: Path, pages: list) -> None:
    """pages is a list of lists of lines."""
    objs: dict[int, bytes] = {}
    n = len(pages)
    kids = [4 + 2 * i for i in range(n)]

    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = ("<< /Type /Pages /Kids [%s] /Count %d >>"
               % (" ".join(f"{k} 0 R" for k in kids), n)).encode()
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for i, lines in enumerate(pages):
        page_id, content_id = 4 + 2 * i, 5 + 2 * i
        objs[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            "/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
            % content_id).encode()
        body = ["BT", "/F1 10 Tf", "50 780 Td", "12 TL"]
        for line in lines:
            body.append(f"({_escape(line)}) Tj T*")
        body.append("ET")
        stream = "\n".join(body).encode()
        objs[content_id] = (b"<< /Length %d >>\nstream\n%s\nendstream"
                            % (len(stream), stream))

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"

    xref_at = len(out)
    top = max(objs) + 1
    out += b"xref\n0 %d\n" % top
    out += b"0000000000 65535 f \n"
    for num in range(1, top):
        out += b"%010d 00000 n \n" % offsets.get(num, 0)
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (top, xref_at))
    path.write_bytes(bytes(out))
    _ = zlib


def proposal_pages(run_ids: list, sections: list, words_per_page: int = 340) -> list:
    """Six pages that satisfy every content rule check_submission applies.

    Not filler for its own sake. The checker asks for a page count, a word
    count, the required section words and a citation for every run id it will
    later validate, so the fixture has to carry all four or it is testing a
    package the real one could never be.
    """
    pages = []
    body = ("The swarm holds a multi hop link to the ground station through a "
            "modelled radio, surveys the frozen box, loses its relay and "
            "rebuilds the chain without anyone touching it. Every number in "
            "this document comes from a recorded run. ").split()

    first = [f"UAV-X Stage 1 technical proposal, fixture build.",
             "Sections covered below: " + ", ".join(sections) + ".",
             "Regulatory position: simulation only, no physical flight.",
             "Evidence, by run id:"]
    first += [f"  {r}" for r in run_ids]
    pages.append(first)

    for page in range(5):
        lines, cur = [], []
        for i in range(words_per_page):
            cur.append(body[(page * words_per_page + i) % len(body)])
            if len(cur) == 12:
                lines.append(" ".join(cur))
                cur = []
        if cur:
            lines.append(" ".join(cur))
        lines.insert(0, f"Section {page + 2}. {sections[page % len(sections)]}.")
        pages.append(lines)
    return pages


def write_video(path: Path, seconds: int = 60, bitrate: str = "") -> bool:
    """A clip that decodes end to end. Returns False if ffmpeg is unavailable."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             f"testsrc=duration={seconds}:size=320x240:rate=10",
             "-pix_fmt", "yuv420p"]
            + (["-b:v", bitrate] if bitrate else []) + [str(path)],
            capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and path.is_file()
