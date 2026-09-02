import unittest

import camera_frame

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None


@unittest.skipIf(cv2 is None or np is None, "OpenCV and NumPy are not installed")
class CameraFrameTests(unittest.TestCase):
    def test_center_crops_1920x1080_and_resizes_to_1024_square(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[:, :420] = (10, 20, 30)
        frame[:, 420:1500] = (40, 50, 60)
        frame[:, 1500:] = (70, 80, 90)

        result = camera_frame.prepare_frame(cv2, frame)

        self.assertEqual(result.shape, (1024, 1024, 3))
        self.assertTrue(np.all(result == (40, 50, 60)))

    def test_rejects_camera_resolution_other_than_1920x1080(self):
        frame = np.zeros((750, 1080, 3), dtype=np.uint8)

        with self.assertRaisesRegex(
            camera_frame.FramePreparationError,
            "요청 1920x1080, 실제 1080x750",
        ):
            camera_frame.prepare_frame(cv2, frame)


if __name__ == "__main__":
    unittest.main()
