import base64
import http.client
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import requests

from bev_callback import CallbackError, CallbackReceiver, validate_png
from capture_and_send import send_image


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class PngValidationTests(unittest.TestCase):
    def test_reads_dimensions(self):
        self.assertEqual(validate_png(PNG), (1, 1))

    def test_rejects_non_png(self):
        with self.assertRaises(CallbackError):
            validate_png(b"not-png")


class CallbackReceiverTests(unittest.TestCase):
    def test_receives_one_raw_png(self):
        with CallbackReceiver("127.0.0.1", 0) as receiver:
            response = requests.post(
                receiver.callback_url("127.0.0.1"),
                data=PNG,
                headers={"Content-Type": "image/png"},
                timeout=2,
            )

            self.assertEqual(response.status_code, 204)
            self.assertEqual(receiver.wait(1), PNG)

    def test_rejects_wrong_path_method_mime_and_invalid_png(self):
        with CallbackReceiver("127.0.0.1", 0) as receiver:
            base = f"http://127.0.0.1:{receiver.port}"
            self.assertEqual(requests.post(base + "/wrong", data=PNG, timeout=2).status_code, 404)
            self.assertEqual(requests.get(base + receiver.path, timeout=2).status_code, 405)
            self.assertEqual(
                requests.post(
                    base + receiver.path,
                    data=PNG,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=2,
                ).status_code,
                415,
            )
            self.assertEqual(
                requests.post(
                    base + receiver.path,
                    data=b"broken",
                    headers={"Content-Type": "image/png"},
                    timeout=2,
                ).status_code,
                400,
            )

    def test_rejects_missing_length_oversize_and_duplicate(self):
        with CallbackReceiver("127.0.0.1", 0, max_bytes=len(PNG)) as receiver:
            connection = http.client.HTTPConnection("127.0.0.1", receiver.port, timeout=2)
            connection.putrequest("POST", receiver.path)
            connection.putheader("Content-Type", "image/png")
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 411)
            connection.close()

            url = receiver.callback_url("127.0.0.1")
            self.assertEqual(
                requests.post(
                    url,
                    data=PNG + b"x",
                    headers={"Content-Type": "image/png"},
                    timeout=2,
                ).status_code,
                413,
            )
            self.assertEqual(
                requests.post(
                    url,
                    data=PNG,
                    headers={"Content-Type": "image/png"},
                    timeout=2,
                ).status_code,
                204,
            )
            self.assertEqual(
                requests.post(
                    url,
                    data=PNG,
                    headers={"Content-Type": "image/png"},
                    timeout=2,
                ).status_code,
                409,
            )

    def test_timeout_and_invalid_advertise_host(self):
        with CallbackReceiver("127.0.0.1", 0) as receiver:
            with self.assertRaisesRegex(CallbackError, "did not arrive"):
                receiver.wait(0.01)
            with self.assertRaises(CallbackError):
                receiver.callback_url("http://bad-host")

    def test_immediate_callback_before_upload_response_is_not_lost(self):
        callback_status = []
        with CallbackReceiver("127.0.0.1", 0) as receiver:
            callback_url = receiver.callback_url("127.0.0.1")

            class UploadHandler(BaseHTTPRequestHandler):
                def do_POST(self):
                    body = self.rfile.read(int(self.headers["Content-Length"]))
                    self.server.received_body = body
                    callback = requests.post(
                        callback_url,
                        data=PNG,
                        headers={"Content-Type": "image/png"},
                        timeout=2,
                    )
                    callback_status.append(callback.status_code)
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                def log_message(self, _format, *args):
                    pass

            upload_server = HTTPServer(("127.0.0.1", 0), UploadHandler)
            upload_thread = Thread(target=upload_server.serve_forever, daemon=True)
            upload_thread.start()
            try:
                status = send_image(
                    b"jpeg",
                    f"http://127.0.0.1:{upload_server.server_port}/upload",
                    form_data={"callback_url": callback_url},
                    camera_json=b'{"model":"fisheye"}',
                )
                self.assertEqual(status, 202)
                self.assertEqual(callback_status, [204])
                self.assertIn(callback_url.encode(), upload_server.received_body)
                self.assertIn(b'filename="camera.json"', upload_server.received_body)
                self.assertIn(b'{"model":"fisheye"}', upload_server.received_body)
                self.assertEqual(receiver.wait(1), PNG)
            finally:
                upload_server.shutdown()
                upload_server.server_close()
                upload_thread.join()


if __name__ == "__main__":
    unittest.main()
