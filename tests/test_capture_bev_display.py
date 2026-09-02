import argparse
import base64
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import capture_bev_display as app
from bev_callback import CallbackError
from capture_and_send import CaptureError, SendError


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def arguments():
    return argparse.Namespace(
        url="http://server/upload",
        callback_host="192.168.0.20",
        callback_bind="0.0.0.0",
        callback_port=8765,
        callback_field="callback_url",
        callback_timeout=30.0,
        camera_index=0,
        warmup_frames=5,
        jpeg_quality=90,
        field_name="image",
        upload_timeout=10.0,
        windowed=False,
        display_seconds=0.0,
    )


class FitRectTests(unittest.TestCase):
    def test_centers_square_map_without_distortion(self):
        self.assertEqual(app.fit_rect((192, 192), (1280, 720)), (720, 720, 280, 0))

    def test_rejects_invalid_dimensions(self):
        with self.assertRaises(app.DisplayError):
            app.fit_rect((0, 192), (1024, 1024))

    def test_pygame_decodes_png_with_dummy_display(self):
        try:
            with patch.dict(
                os.environ,
                {"SDL_VIDEODRIVER": "dummy", "PYGAME_HIDE_SUPPORT_PROMPT": "1"},
            ):
                import pygame
                app.display_bev(pygame, PNG, windowed=True, display_seconds=0.01)
        except ImportError:
            self.skipTest("pygame is not installed")

    def test_nearest_scaling_preserves_bev_class_values(self):
        try:
            import pygame
        except ImportError:
            self.skipTest("pygame is not installed")

        source = pygame.Surface((3, 1), depth=24)
        source.set_at((0, 0), (0, 0, 0))
        source.set_at((1, 0), (127, 127, 127))
        source.set_at((2, 0), (255, 255, 255))

        scaled = pygame.transform.scale(source, (9, 3))

        expected = [(0, 0, 0)] * 3 + [(127, 127, 127)] * 3 + [(255, 255, 255)] * 3
        for y in range(3):
            self.assertEqual([scaled.get_at((x, y))[:3] for x in range(9)], expected)


class WorkflowTests(unittest.TestCase):
    @patch("capture_bev_display.display_bev")
    @patch("capture_bev_display.send_image", return_value=202)
    @patch("capture_bev_display.capture_jpeg", return_value=b"jpeg")
    @patch("capture_bev_display.CallbackReceiver")
    def test_starts_listener_then_sends_callback_url_and_displays(
        self, receiver_class, capture, send, display
    ):
        receiver = receiver_class.return_value.__enter__.return_value
        receiver.callback_url.return_value = "http://192.168.0.20:8765/bev/token"
        receiver.wait.return_value = PNG
        pygame = object()

        with redirect_stdout(io.StringIO()):
            result = app.run(arguments(), pygame)

        self.assertEqual(result, 0)
        receiver_class.assert_called_once_with("0.0.0.0", 8765)
        capture.assert_called_once_with(
            camera_index=0,
            width=1024,
            height=1024,
            warmup_frames=5,
            jpeg_quality=90,
        )
        send.assert_called_once_with(
            jpeg=b"jpeg",
            url="http://server/upload",
            field_name="image",
            timeout=10.0,
            form_data={"callback_url": "http://192.168.0.20:8765/bev/token"},
        )
        receiver.wait.assert_called_once_with(30.0)
        display.assert_called_once_with(pygame, PNG, False, 0.0)

    @patch("capture_bev_display.CallbackReceiver")
    @patch("capture_bev_display.capture_jpeg", side_effect=CaptureError("camera"))
    def test_capture_error_exit_code(self, _capture, receiver_class):
        receiver_class.return_value.__enter__.return_value.callback_url.return_value = "http://pi/cb"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(app.run(arguments(), object()), 3)

    @patch("capture_bev_display.CallbackReceiver")
    @patch("capture_bev_display.capture_jpeg", return_value=b"jpeg")
    @patch("capture_bev_display.send_image", side_effect=SendError("network"))
    def test_send_error_exit_code(self, _send, _capture, receiver_class):
        receiver_class.return_value.__enter__.return_value.callback_url.return_value = "http://pi/cb"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(app.run(arguments(), object()), 4)

    @patch("capture_bev_display.CallbackReceiver")
    @patch("capture_bev_display.capture_jpeg", return_value=b"jpeg")
    @patch("capture_bev_display.send_image", return_value=202)
    def test_callback_error_exit_code(self, _send, _capture, receiver_class):
        receiver = receiver_class.return_value.__enter__.return_value
        receiver.callback_url.return_value = "http://pi/cb"
        receiver.wait.side_effect = CallbackError("timeout")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(app.run(arguments(), object()), app.EXIT_CALLBACK_ERROR)

    @patch("capture_bev_display.display_bev", side_effect=app.DisplayError("display"))
    @patch("capture_bev_display.CallbackReceiver")
    @patch("capture_bev_display.capture_jpeg", return_value=b"jpeg")
    @patch("capture_bev_display.send_image", return_value=202)
    def test_display_error_exit_code(self, _send, _capture, receiver_class, _display):
        receiver = receiver_class.return_value.__enter__.return_value
        receiver.callback_url.return_value = "http://pi/cb"
        receiver.wait.return_value = PNG
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(app.run(arguments(), object()), app.EXIT_DISPLAY_ERROR)


if __name__ == "__main__":
    unittest.main()
