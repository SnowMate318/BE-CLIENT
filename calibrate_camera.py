"""Headless fisheye calibration for a USB camera."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Iterable, NamedTuple, Optional, Sequence, Tuple


IMAGE_WIDTH = 1024
IMAGE_HEIGHT = 1024


class CalibrationError(RuntimeError):
    """A user-actionable calibration failure."""


class Pose(NamedTuple):
    center_x: float
    center_y: float
    scale: float
    angle: float
    horizontal_skew: float
    vertical_skew: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibrate a USB fisheye camera from a printed checkerboard (no GUI).",
        epilog=(
            "Print the board at 100% scale, measure one square, then slowly move and "
            "tilt it so it reaches the center, edges, and corners of the image."
        ),
    )
    parser.add_argument(
        "--square-size-m",
        type=float,
        required=True,
        help="measured checkerboard square side length in metres",
    )
    parser.add_argument(
        "--camera",
        "--camera-index",
        dest="camera",
        default="0",
        help="camera index or device path (default: 0)",
    )
    parser.add_argument("--board-cols", type=int, default=9, help="internal corner columns (default: 9)")
    parser.add_argument("--board-rows", type=int, default=6, help="internal corner rows (default: 6)")
    parser.add_argument("--samples", type=int, default=20, help="diverse views to collect (default: 20)")
    parser.add_argument("--timeout-s", type=float, default=180.0, help="collection timeout in seconds (default: 180)")
    parser.add_argument("--scan-interval-s", type=float, default=0.2, help="seconds between checkerboard scans (default: 0.2)")
    parser.add_argument(
        "--width", type=int, default=IMAGE_WIDTH, help="capture width (fixed: 1024)"
    )
    parser.add_argument(
        "--height", type=int, default=IMAGE_HEIGHT, help="capture height (fixed: 1024)"
    )
    parser.add_argument("--output", type=Path, default=Path("camera.json"), help="output JSON path (default: camera.json)")
    parser.add_argument(
        "--world-from-camera",
        type=float,
        nargs=9,
        metavar="N",
        help="optional 3x3 world-from-camera matrix, in row-major order",
    )
    parser.add_argument(
        "--camera-position-world",
        type=float,
        nargs=3,
        metavar="M",
        help="optional camera x y z position in world coordinates",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not math.isfinite(args.square_size_m) or args.square_size_m <= 0:
        raise CalibrationError("--square-size-m must be a positive finite number")
    if args.board_cols < 2 or args.board_rows < 2:
        raise CalibrationError("checkerboard dimensions must each contain at least 2 internal corners")
    if args.samples < 10:
        raise CalibrationError("--samples must be at least 10")
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        raise CalibrationError("--timeout-s must be a positive finite number")
    if not math.isfinite(args.scan_interval_s) or args.scan_interval_s < 0:
        raise CalibrationError("--scan-interval-s must be a non-negative finite number")
    if (args.width, args.height) != (IMAGE_WIDTH, IMAGE_HEIGHT):
        raise CalibrationError("camera resolution is fixed at 1024x1024")
    if (args.world_from_camera is None) != (args.camera_position_world is None):
        raise CalibrationError(
            "--world-from-camera and --camera-position-world must be supplied together"
        )
    pose_values = (args.world_from_camera or []) + (args.camera_position_world or [])
    if not all(math.isfinite(value) for value in pose_values):
        raise CalibrationError("world pose values must be finite numbers")


def _point_xy(point: Sequence[object]) -> Tuple[float, float]:
    while len(point) == 1 and isinstance(point[0], (list, tuple)):
        point = point[0]  # type: ignore[assignment]
    return float(point[0]), float(point[1])


def _distance(first: Tuple[float, float], second: Tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def describe_pose(
    corners: Sequence[Sequence[object]], image_size: Tuple[int, int], cols: int, rows: int
) -> Pose:
    """Summarize board position, size, rotation, and perspective using no OpenCV calls."""
    points = [_point_xy(point) for point in corners]
    if len(points) != cols * rows:
        raise CalibrationError("detected checkerboard has an unexpected corner count")

    width, height = image_size
    top_left, top_right = points[0], points[cols - 1]
    bottom_left, bottom_right = points[-cols], points[-1]
    outer = (top_left, top_right, bottom_right, bottom_left)
    area = abs(
        sum(
            outer[index][0] * outer[(index + 1) % 4][1]
            - outer[(index + 1) % 4][0] * outer[index][1]
            for index in range(4)
        )
    ) / 2.0
    top = max(_distance(top_left, top_right), 1e-9)
    bottom = max(_distance(bottom_left, bottom_right), 1e-9)
    left = max(_distance(top_left, bottom_left), 1e-9)
    right = max(_distance(top_right, bottom_right), 1e-9)
    return Pose(
        sum(point[0] for point in points) / len(points) / width,
        sum(point[1] for point in points) / len(points) / height,
        math.sqrt(max(area, 1e-12) / (width * height)),
        math.atan2(top_right[1] - top_left[1], top_right[0] - top_left[0]),
        math.log(top / bottom),
        math.log(left / right),
    )


def _angle_distance(first: float, second: float) -> float:
    return abs((first - second + math.pi) % (2 * math.pi) - math.pi)


def is_diverse_pose(candidate: Pose, accepted: Iterable[Pose]) -> bool:
    for previous in accepted:
        is_near_duplicate = (
            math.hypot(candidate.center_x - previous.center_x, candidate.center_y - previous.center_y) < 0.09
            and abs(math.log(candidate.scale / previous.scale)) < 0.16
            and _angle_distance(candidate.angle, previous.angle) < math.radians(10)
            and abs(candidate.horizontal_skew - previous.horizontal_skew) < 0.14
            and abs(candidate.vertical_skew - previous.vertical_skew) < 0.14
        )
        if is_near_duplicate:
            return False
    return True


def _camera_source(value: str):
    try:
        return int(value)
    except ValueError:
        return value


def collect_views(cv2, args: argparse.Namespace):
    capture = cv2.VideoCapture(_camera_source(args.camera))
    if not capture.isOpened():
        capture.release()
        raise CalibrationError(f"cannot open camera {args.camera!r}")
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, IMAGE_WIDTH)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, IMAGE_HEIGHT)

    image_points, poses = [], []
    image_size: Optional[Tuple[int, int]] = None
    deadline = time.monotonic() + args.timeout_s
    next_scan = 0.0
    failed_reads = 0
    pattern = (args.board_cols, args.board_rows)
    flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    try:
        while len(image_points) < args.samples and time.monotonic() < deadline:
            ok, frame = capture.read()
            if not ok:
                failed_reads += 1
                if failed_reads >= 30:
                    raise CalibrationError("camera returned 30 unreadable frames in a row")
                continue
            failed_reads = 0
            now = time.monotonic()
            if now < next_scan:
                continue
            next_scan = now + args.scan_interval_s
            current_size = (int(frame.shape[1]), int(frame.shape[0]))
            if current_size != (IMAGE_WIDTH, IMAGE_HEIGHT):
                raise CalibrationError(
                    f"camera provided {current_size[0]}x{current_size[1]}, not the requested "
                    f"{IMAGE_WIDTH}x{IMAGE_HEIGHT}"
                )
            if image_size is not None and current_size != image_size:
                raise CalibrationError("camera resolution changed during calibration")
            image_size = current_size
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCorners(gray, pattern, flags)
            if not found:
                continue
            corners = cv2.cornerSubPix(gray, corners, (5, 5), (-1, -1), criteria)
            pose = describe_pose(corners.tolist(), current_size, *pattern)
            if is_diverse_pose(pose, poses):
                poses.append(pose)
                image_points.append(corners.copy())
                print(f"accepted view {len(image_points)}/{args.samples}", flush=True)
    finally:
        capture.release()

    if len(image_points) < args.samples:
        raise CalibrationError(
            f"timed out after collecting {len(image_points)}/{args.samples} diverse views; "
            "move and tilt the checkerboard across more of the camera image"
        )
    return image_points, image_size


def calibrate_fisheye(cv2, np, image_points, image_size, cols, rows, square_size_m):
    object_template = np.zeros((1, cols * rows, 3), dtype=np.float64)
    for row in range(rows):
        for col in range(cols):
            object_template[0, row * cols + col, :2] = (
                col * square_size_m,
                row * square_size_m,
            )
    object_points = [object_template.copy() for _ in image_points]
    image_points = [np.asarray(points, dtype=np.float64) for points in image_points]
    matrix = np.zeros((3, 3), dtype=np.float64)
    distortion = np.zeros((4, 1), dtype=np.float64)
    rotations = [np.zeros((1, 1, 3), dtype=np.float64) for _ in image_points]
    translations = [np.zeros((1, 1, 3), dtype=np.float64) for _ in image_points]
    flags = (
        cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
        | cv2.fisheye.CALIB_CHECK_COND
        | cv2.fisheye.CALIB_FIX_SKEW
    )
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-6)
    rms, matrix, distortion, _, _ = cv2.fisheye.calibrate(
        object_points,
        image_points,
        image_size,
        matrix,
        distortion,
        rotations,
        translations,
        flags,
        criteria,
    )
    return float(rms), matrix, distortion


def _matrix_value(matrix, row: int, column: int) -> float:
    try:
        return float(matrix[row, column])
    except TypeError:
        return float(matrix[row][column])


def _flatten(values) -> list:
    values = values.tolist() if hasattr(values, "tolist") else values
    if isinstance(values, (list, tuple)):
        return [item for value in values for item in _flatten(value)]
    return [float(values)]


def make_camera_config(
    image_size,
    matrix,
    distortion,
    world_from_camera=None,
    camera_position_world=None,
):
    coefficients = _flatten(distortion)
    if len(coefficients) != 4:
        raise CalibrationError("fisheye calibration did not return exactly 4 distortion values")
    config = {
        "model": "fisheye",
        "width": int(image_size[0]),
        "height": int(image_size[1]),
        "fx": _matrix_value(matrix, 0, 0),
        "fy": _matrix_value(matrix, 1, 1),
        # OpenCV uses integer pixel-center coordinates. BirdEyeFinal stores
        # pixel-cell-center coordinates and adds 0.5 before projection.
        "cx": _matrix_value(matrix, 0, 2) + 0.5,
        "cy": _matrix_value(matrix, 1, 2) + 0.5,
        "distortion": coefficients,
    }
    intrinsics = (config["fx"], config["fy"], config["cx"], config["cy"], *coefficients)
    if not all(math.isfinite(value) for value in intrinsics) or config["fx"] <= 0 or config["fy"] <= 0:
        raise CalibrationError("calibration returned invalid intrinsic parameters")
    if world_from_camera is not None:
        config["world_from_camera"] = [
            [float(value) for value in world_from_camera[start : start + 3]]
            for start in range(0, 9, 3)
        ]
        config["camera_position_world"] = [float(value) for value in camera_position_world]
    return config


def write_camera_config(path: Path, config) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        json.dump(config, output, indent=2, ensure_ascii=False)
        output.write("\n")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_args(args)
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise CalibrationError(
                "OpenCV and NumPy are required; install dependencies from requirements.txt"
            ) from exc

        print(
            "Collecting checkerboard views. Move the board across the full image and vary its tilt.",
            flush=True,
        )
        image_points, image_size = collect_views(cv2, args)
        rms, matrix, distortion = calibrate_fisheye(
            cv2,
            np,
            image_points,
            image_size,
            args.board_cols,
            args.board_rows,
            args.square_size_m,
        )
        config = make_camera_config(
            image_size,
            matrix,
            distortion,
            args.world_from_camera,
            args.camera_position_world,
        )
        write_camera_config(args.output, config)
    except KeyboardInterrupt:
        print("error: calibration cancelled", file=sys.stderr)
        return 130
    except CalibrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        if exc.__class__.__module__.startswith("cv2"):
            print(f"error: OpenCV calibration failed: {exc}", file=sys.stderr)
            return 2
        raise

    print(f"wrote {args.output} (RMS reprojection error: {rms:.4f} px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
