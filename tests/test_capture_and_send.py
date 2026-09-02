import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import capture_and_send


class CaptureJpegTests(unittest.TestCase):
    def setUp(self):
        self.camera = MagicMock()
        self.camera.isOpened.return_value = True
        self.frame = MagicMock()
        self.frame.shape = (480, 640, 3)
        self.camera.read.return_value = (True, self.frame)

        self.encoded = MagicMock()
        self.encoded.tobytes.return_value = b"jpeg-data"
        self.cv2 = MagicMock()
        self.cv2.CAP_PROP_FRAME_WIDTH = 3
        self.cv2.CAP_PROP_FRAME_HEIGHT = 4
        self.cv2.IMWRITE_JPEG_QUALITY = 1
        self.cv2.VideoCapture.return_value = self.camera
        self.cv2.imencode.return_value = (True, self.encoded)

    def test_captures_requested_resolution_after_warmup_and_encodes_jpeg(self):
        with patch.object(capture_and_send, "cv2", self.cv2):
            result = capture_and_send.capture_jpeg(2, 640, 480, 2, 85)

        self.assertEqual(result, b"jpeg-data")
        self.cv2.VideoCapture.assert_called_once_with(2)
        self.camera.set.assert_any_call(self.cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.camera.set.assert_any_call(self.cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.assertEqual(self.camera.read.call_count, 3)
        self.cv2.imencode.assert_called_once_with(
            ".jpg", self.frame, [self.cv2.IMWRITE_JPEG_QUALITY, 85]
        )
        self.camera.release.assert_called_once_with()

    def test_releases_camera_when_final_frame_cannot_be_read(self):
        self.camera.read.return_value = (False, None)

        with patch.object(capture_and_send, "cv2", self.cv2):
            with self.assertRaisesRegex(capture_and_send.CaptureError, "프레임"):
                capture_and_send.capture_jpeg(0, 640, 480, 0, 90)

        self.camera.release.assert_called_once_with()

    def test_rejects_resolution_different_from_request(self):
        self.frame.shape = (720, 1280, 3)

        with patch.object(capture_and_send, "cv2", self.cv2):
            with self.assertRaisesRegex(
                capture_and_send.CaptureError, "요청 640x480, 실제 1280x720"
            ):
                capture_and_send.capture_jpeg(0, 640, 480, 0, 90)

        self.cv2.imencode.assert_not_called()
        self.camera.release.assert_called_once_with()


class SendImageTests(unittest.TestCase):
    def test_sends_jpeg_as_configured_multipart_field(self):
        response = MagicMock(status_code=201)
        requests_mock = MagicMock()
        requests_mock.post.return_value = response

        with patch.object(capture_and_send, "requests", requests_mock):
            status = capture_and_send.send_image(
                b"jpeg-data",
                "http://192.168.0.10:8000/upload",
                field_name="photo",
                timeout=3.5,
            )

        self.assertEqual(status, 201)
        requests_mock.post.assert_called_once_with(
            "http://192.168.0.10:8000/upload",
            files={"photo": ("capture.jpg", b"jpeg-data", "image/jpeg")},
            timeout=3.5,
            allow_redirects=False,
        )
        response.raise_for_status.assert_called_once_with()

    def test_sends_callback_form_data(self):
        response = MagicMock(status_code=202)
        requests_mock = MagicMock()
        requests_mock.post.return_value = response

        with patch.object(capture_and_send, "requests", requests_mock):
            status = capture_and_send.send_image(
                b"jpeg-data",
                "http://server/upload",
                form_data={"callback_url": "http://pi:8765/bev/token"},
            )

        self.assertEqual(status, 202)
        requests_mock.post.assert_called_once_with(
            "http://server/upload",
            files={"image": ("capture.jpg", b"jpeg-data", "image/jpeg")},
            timeout=10.0,
            allow_redirects=False,
            data={"callback_url": "http://pi:8765/bev/token"},
        )

    def test_wraps_request_failure_as_send_error(self):
        class FakeRequestException(Exception):
            pass

        requests_mock = SimpleNamespace(
            RequestException=FakeRequestException,
            post=MagicMock(side_effect=FakeRequestException("connection refused")),
        )

        with patch.object(capture_and_send, "requests", requests_mock):
            with self.assertRaisesRegex(
                capture_and_send.SendError, "connection refused"
            ):
                capture_and_send.send_image(
                    b"jpeg-data", "http://192.168.0.10:8000/upload"
                )

    def test_rejects_non_2xx_response_that_raise_for_status_allows(self):
        response = MagicMock(status_code=302)
        requests_mock = MagicMock()
        requests_mock.post.return_value = response

        with patch.object(capture_and_send, "requests", requests_mock):
            with self.assertRaisesRegex(capture_and_send.SendError, "HTTP 302"):
                capture_and_send.send_image(
                    b"jpeg-data", "http://192.168.0.10:8000/upload"
                )


class MainTests(unittest.TestCase):
    @patch("capture_and_send.send_image", return_value=200)
    @patch("capture_and_send.capture_jpeg", return_value=b"jpeg-data")
    def test_success_returns_zero(self, capture_mock, send_mock):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = capture_and_send.main(
                ["--url", "http://server/upload"]
            )

        self.assertEqual(result, 0)
        capture_mock.assert_called_once_with(
            camera_index=0,
            width=1024,
            height=1024,
            warmup_frames=5,
            jpeg_quality=90,
        )
        send_mock.assert_called_once_with(
            jpeg=b"jpeg-data",
            url="http://server/upload",
            field_name="image",
            timeout=10.0,
        )
        self.assertIn("HTTP 200", stdout.getvalue())

    def test_rejects_non_fixed_resolution(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            capture_and_send.main(["--url", "http://server/upload", "--width", "640"])
        self.assertIn("1024x1024", stderr.getvalue())

    @patch(
        "capture_and_send.capture_jpeg",
        side_effect=capture_and_send.CaptureError("카메라 없음"),
    )
    def test_capture_failure_returns_capture_exit_code(self, _capture_mock):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = capture_and_send.main(["--url", "http://server/upload"])

        self.assertEqual(result, capture_and_send.EXIT_CAPTURE_ERROR)
        self.assertIn("촬영 실패", stderr.getvalue())

    @patch(
        "capture_and_send.send_image",
        side_effect=capture_and_send.SendError("연결 실패"),
    )
    @patch("capture_and_send.capture_jpeg", return_value=b"jpeg-data")
    def test_send_failure_returns_send_exit_code(self, _capture_mock, _send_mock):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = capture_and_send.main(["--url", "http://server/upload"])

        self.assertEqual(result, capture_and_send.EXIT_SEND_ERROR)
        self.assertIn("전송 실패", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
