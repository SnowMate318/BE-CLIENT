import argparse
import json
import tempfile
import unittest
from pathlib import Path

import calibrate_camera as calibration

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None


def checkerboard(x, y, step, cols=9, rows=6):
    return [
        [[[x + col * step, y + row * step][axis] for axis in range(2)]]
        for row in range(rows)
        for col in range(cols)
    ]


class CalibrationTests(unittest.TestCase):
    def test_pose_filter_rejects_duplicate_and_accepts_new_position(self):
        first = calibration.describe_pose(checkerboard(100, 100, 20), (640, 480), 9, 6)
        duplicate = calibration.describe_pose(checkerboard(102, 101, 20), (640, 480), 9, 6)
        shifted = calibration.describe_pose(checkerboard(300, 180, 20), (640, 480), 9, 6)

        self.assertFalse(calibration.is_diverse_pose(duplicate, [first]))
        self.assertTrue(calibration.is_diverse_pose(shifted, [first]))

    def test_config_contains_only_measured_intrinsics_by_default(self):
        config = calibration.make_camera_config(
            (1024, 768),
            [[390.0, 0.0, 511.5], [0.0, 391.0, 383.5], [0.0, 0.0, 1.0]],
            [[0.05], [0.01], [-0.003], [-0.0005]],
        )

        self.assertEqual(config["model"], "fisheye")
        self.assertEqual(config["distortion"], [0.05, 0.01, -0.003, -0.0005])
        self.assertEqual(config["cx"], 512.0)
        self.assertEqual(config["cy"], 384.0)
        self.assertNotIn("world_from_camera", config)
        self.assertNotIn("camera_position_world", config)

    def test_config_adds_explicit_world_pose(self):
        config = calibration.make_camera_config(
            (640, 480),
            [[300.0, 0.0, 319.5], [0.0, 301.0, 239.5], [0.0, 0.0, 1.0]],
            [0.1, 0.01, 0.0, 0.0],
            [-1, 0, 0, 0, 1, 0, 0, 0, -1],
            [-2, 12, 14],
        )

        self.assertEqual(config["world_from_camera"], [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]])
        self.assertEqual(config["camera_position_world"], [-2.0, 12.0, 14.0])

    def test_argument_validation_requires_fixed_resolution_and_paired_pose(self):
        base = dict(
            square_size_m=0.025,
            board_cols=9,
            board_rows=6,
            samples=20,
            timeout_s=60.0,
            scan_interval_s=0.2,
            width=1024,
            height=1024,
            world_from_camera=None,
            camera_position_world=None,
        )
        calibration.validate_args(argparse.Namespace(**base))

        base.update(width=640)
        with self.assertRaisesRegex(calibration.CalibrationError, "fixed at 1024x1024"):
            calibration.validate_args(argparse.Namespace(**base))

        base.update(width=1024, world_from_camera=[1.0] * 9)
        with self.assertRaisesRegex(calibration.CalibrationError, "must be supplied together"):
            calibration.validate_args(argparse.Namespace(**base))

        base.update(world_from_camera=None, samples=9)
        with self.assertRaisesRegex(calibration.CalibrationError, "at least 10"):
            calibration.validate_args(argparse.Namespace(**base))

    def test_write_camera_config_creates_parent_and_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "inputs" / "camera.json"
            calibration.write_camera_config(output, {"model": "fisheye", "width": 640})

            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {"model": "fisheye", "width": 640},
            )

    def test_camera_argument_accepts_index_or_device_path(self):
        self.assertEqual(calibration._camera_source("0"), 0)
        self.assertEqual(calibration._camera_source("/dev/video2"), "/dev/video2")

    @unittest.skipIf(cv2 is None or np is None, "OpenCV and NumPy are not installed")
    def test_fisheye_calibration_recovers_synthetic_intrinsics(self):
        cols, rows = 9, 6
        object_points = np.zeros((1, cols * rows, 3), np.float64)
        object_points[0, :, :2] = (
            np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * 0.025
        )
        expected_matrix = np.array(
            [[420.0, 0.0, 511.5], [0.0, 415.0, 511.5], [0.0, 0.0, 1.0]],
            np.float64,
        )
        expected_distortion = np.array(
            [0.02, -0.01, 0.001, -0.0001], np.float64
        ).reshape(4, 1)
        poses = [
            ((-0.25, -0.12, 0.72), (0.15, 0.05, -0.02)),
            ((0.15, -0.12, 0.68), (-0.12, 0.10, 0.04)),
            ((-0.12, 0.08, 0.65), (0.08, -0.15, 0.10)),
            ((0.18, 0.10, 0.75), (-0.18, -0.08, -0.05)),
            ((0.0, 0.0, 0.55), (0.20, 0.18, 0.08)),
            ((-0.20, 0.05, 0.80), (-0.10, 0.20, -0.10)),
            ((0.10, -0.05, 0.58), (0.12, -0.20, 0.15)),
            ((-0.05, 0.12, 0.70), (-0.22, 0.05, 0.18)),
            ((0.22, -0.08, 0.82), (0.16, -0.12, -0.15)),
            ((-0.18, -0.10, 0.62), (-0.14, -0.18, 0.12)),
        ]
        image_points = []
        for translation, rotation in poses:
            projected, _ = cv2.fisheye.projectPoints(
                object_points,
                np.asarray(rotation, np.float64).reshape(3, 1),
                np.asarray(translation, np.float64).reshape(3, 1),
                expected_matrix,
                expected_distortion,
            )
            image_points.append(projected)

        rms, matrix, distortion = calibration.calibrate_fisheye(
            cv2, np, image_points, (1024, 1024), cols, rows, 0.025
        )

        self.assertLess(rms, 1e-5)
        np.testing.assert_allclose(matrix, expected_matrix, atol=1e-4)
        np.testing.assert_allclose(distortion, expected_distortion, atol=1e-5)


if __name__ == "__main__":
    unittest.main()
