"""Chunk 3.6: the capture path, tested where a simulator is not needed.

Round 5 finding 6 is the reason this file is careful. The recording half of
the W3 gate used to accept any decodable local clip of at least 55 seconds:
the receipt did not hash the clip, and the clip did not have to show Gazebo,
four vehicles, this scenario or this source. A holiday video passed.

The burn is what closes that, so the overlay is checked here rather than
escaped, and the rate is checked here because a clip encoded at the nominal
camera rate rather than the measured one plays about 1.4 times too fast on
this stack. Both of those are wrong in a way that still produces a file that
decodes and has the right duration.

    python3 -m pytest -q uavx_ws/src/uavx_sim/test/test_video.py

Runs on a clean checkout with nothing built and needs no ffmpeg.
"""

import pytest

from uavx_sim.video import (ENCODING, MIN_CAPTURE_HZ, OUTPUT_FPS, RAW_NAME,
                            RecorderError, as_rgb, capture_rate,
                            clip_problems, encode_command, frame_bytes,
                            overlay_filter, raw_path, receipt_fields, sweep)

RUN_ID = "rehearsal_relay_required_20260905T120000Z"


def solid(width, height, colour=(10, 20, 30)):
    return bytes(bytearray(colour) * (width * height))


# ------------------------------------------------------------------- a frame
def test_a_frame_is_three_bytes_a_pixel():
    assert frame_bytes(960, 540) == 960 * 540 * 3


@pytest.mark.parametrize("size", [(0, 4), (4, 0), (-1, 4)])
def test_a_frame_with_no_area_is_not_an_image(size):
    with pytest.raises(RecorderError, match="not an image"):
        frame_bytes(size[0], size[1])


def test_the_frames_go_to_one_file_and_not_a_numbered_sequence():
    """A numbered sequence stops at the first gap. A dropped frame here
    shortens the clip, which is the honest outcome, rather than truncating it
    at the frame that went missing."""
    assert raw_path("/tmp/f").name == RAW_NAME


# -------------------------------------------------------------- the raw rows
def test_a_tightly_packed_message_comes_through_unchanged():
    rgb = solid(4, 3)
    assert as_rgb(ENCODING, rgb, 4, 3, 12) == rgb


def test_a_padded_stride_is_unpacked_rather_than_assumed_away():
    """The failure this catches looks like a rendering fault, not a bug here."""
    width, height, step = 4, 3, 16
    rows = [bytes([y]) * (width * 3) + b"\x00" * (step - width * 3)
            for y in range(height)]
    got = as_rgb(ENCODING, b"".join(rows), width, height, step)
    assert got == b"".join(bytes([y]) * (width * 3) for y in range(height))


def test_an_encoding_the_writer_does_not_pack_is_refused():
    with pytest.raises(RecorderError, match="R8G8B8"):
        as_rgb("bgr8", solid(2, 2), 2, 2, 6)


def test_a_stride_narrower_than_a_row_is_refused():
    with pytest.raises(RecorderError, match="stride"):
        as_rgb(ENCODING, solid(4, 2), 4, 2, 6)


def test_a_message_shorter_than_it_promised_is_refused():
    with pytest.raises(RecorderError, match="promised"):
        as_rgb(ENCODING, solid(4, 1), 4, 3, 12)


# --------------------------------------------------------------- the rate
def test_the_rate_is_the_intervals_and_not_the_frames():
    """N frames have N-1 gaps between the first and the last."""
    assert capture_rate(420, 60.0) == pytest.approx(419 / 60.0)


def test_the_clip_is_never_shorter_than_the_window_it_covered():
    """The defect this formula fixes, stated as the property that failed.

    418 frames across exactly 60.0 s of simulated time encoded at frames over
    span produced a 59.96 s clip, and the rehearsal refused it because a clip
    shorter than the window does not prove a longer capture holds up. The
    duration ffmpeg produces for N frames at rate R is N over R, so R has to
    be small enough that it lands at or above the span.
    """
    for frames, span in ((418, 60.0), (3, 1.0), (1200, 240.0), (37, 7.5)):
        rate = capture_rate(frames, span)
        assert frames / rate >= span


def test_a_capture_of_one_frame_has_no_rate():
    with pytest.raises(RecorderError, match="at least two"):
        capture_rate(1, 60.0)


@pytest.mark.parametrize("span", [0.0, -1.0, float("nan"), float("inf")])
def test_a_capture_with_no_span_has_no_rate(span):
    with pytest.raises(RecorderError):
        capture_rate(100, span)


def test_a_slideshow_is_refused_rather_than_encoded():
    with pytest.raises(RecorderError, match="slideshow"):
        capture_rate(60, 60.0)


def test_the_floor_is_where_it_says_it_is():
    assert capture_rate(int(MIN_CAPTURE_HZ * 60) + 2, 60.0) > MIN_CAPTURE_HZ


# ------------------------------------------------------------- the overlay
def test_the_run_id_goes_into_the_filter():
    got = overlay_filter(RUN_ID)
    assert f"text={RUN_ID}" in got
    assert got.startswith("drawtext=fontfile=")


@pytest.mark.parametrize("bad", [
    "run:id",                      # a colon separates filter options
    "run\\id",                     # a backslash escapes the next character
    "run id",                      # a space ends the filter in some shells
    "run'id",
    "",
    None,
    "x" * 200,
])
def test_anything_that_is_not_a_run_id_is_refused_and_not_escaped(bad):
    with pytest.raises(RecorderError, match="not a run id"):
        overlay_filter(bad)


# ------------------------------------------------------------- the command
def test_the_measured_rate_is_the_input_rate_and_not_the_output_rate():
    """Swapping them is the mistake that makes the clip play at the wrong
    speed while still decoding and still having a plausible duration."""
    command = encode_command("/tmp/f", "/tmp/clip.mp4", 7.25, 960, 540, RUN_ID)
    assert command[command.index("-framerate") + 1] == "7.250000"
    assert command[command.index("-i") + 1].endswith(RAW_NAME)
    assert command.index("-framerate") < command.index("-i")
    assert command.index("-i") < command.index("-r")
    assert command[command.index("-r") + 1] == str(OUTPUT_FPS)


def test_the_raw_input_is_described_completely():
    """rawvideo carries no header, so a wrong size decodes to a sheared mess
    rather than to an error."""
    command = encode_command("/tmp/f", "/tmp/clip.mp4", 7.0, 960, 540, RUN_ID)
    assert command[command.index("-video_size") + 1] == "960x540"
    assert command[command.index("-pixel_format") + 1] == "rgb24"
    assert command[command.index("-f") + 1] == "rawvideo"


def test_the_clip_is_encoded_for_something_that_will_play_it():
    command = encode_command("/tmp/f", "/tmp/clip.mp4", 7.0, 960, 540, RUN_ID)
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"


@pytest.mark.parametrize("rate", [0.0, -1.0, float("nan")])
def test_a_command_cannot_be_built_without_a_rate(rate):
    with pytest.raises(RecorderError):
        encode_command("/tmp/f", "/tmp/clip.mp4", rate, 960, 540, RUN_ID)


def test_a_command_cannot_be_built_for_a_frame_with_no_area():
    with pytest.raises(RecorderError, match="not an image"):
        encode_command("/tmp/f", "/tmp/clip.mp4", 7.0, 0, 540, RUN_ID)


def test_the_command_refuses_an_overlay_it_would_have_to_escape():
    with pytest.raises(RecorderError, match="not a run id"):
        encode_command("/tmp/f", "/tmp/clip.mp4", 7.0, 960, 540, "run:id")


# --------------------------------------------------------------- the frames
def test_the_frames_are_swept_and_the_bytes_are_reported(tmp_path):
    raw_path(tmp_path).write_bytes(b"x" * 4096)
    (tmp_path / "keep.txt").write_text("not a frame", encoding="utf-8")
    assert sweep(tmp_path) == 4096
    assert (tmp_path / "keep.txt").is_file()
    assert sweep(tmp_path) == 0


def test_sweeping_a_directory_that_is_not_there_is_not_a_failure(tmp_path):
    assert sweep(tmp_path / "gone") == 0


# ----------------------------------------------------------------- the clip
def test_a_missing_clip_is_the_first_thing_reported(tmp_path):
    problems = clip_problems(tmp_path / "clip.mp4", 60.0, 60.0, 420)
    assert len(problems) == 1
    assert "no clip" in problems[0]


def test_a_clip_shorter_than_it_was_asked_for_is_reported(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0" * 4096)
    problems = clip_problems(clip, 60.0, 41.5, 300)
    assert any("41.5" in p and "60.0" in p for p in problems)


def test_a_clip_that_is_long_enough_has_nothing_wrong_with_it(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0" * 4096)
    assert clip_problems(clip, 60.0, 60.0, 420) == []


def test_a_file_too_small_to_be_a_video_is_reported(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0" * 12)
    assert any("not a video" in p for p in clip_problems(clip, 1.0, 1.0, 4))


def test_the_record_says_how_the_clip_was_made():
    fields = receipt_fields(960, 540, 420, 60.0, 7.0)
    assert fields["video_capture_hz"] == 7.0
    assert fields["video_playback_fps"] == OUTPUT_FPS
    assert "gzserver" in fields["video_source"]
