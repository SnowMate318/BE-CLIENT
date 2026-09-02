"""USB 카메라에서 한 장을 촬영해 HTTP multipart로 전송한다."""

from __future__ import annotations

import argparse
import sys
from typing import Mapping, Optional, Sequence

from camera_frame import (
    FramePreparationError,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    SOURCE_HEIGHT,
    SOURCE_WIDTH,
    prepare_frame,
)

try:
    import cv2
except ImportError:  # requirements.txt가 설치되지 않은 경우에도 명확한 CLI 오류를 낸다.
    cv2 = None

try:
    import requests
except ImportError:  # requirements.txt가 설치되지 않은 경우에도 명확한 CLI 오류를 낸다.
    requests = None


EXIT_CAPTURE_ERROR = 3
EXIT_SEND_ERROR = 4


class CaptureError(RuntimeError):
    """카메라 촬영 또는 JPEG 인코딩 실패."""


class SendError(RuntimeError):
    """HTTP 전송 실패."""


def capture_jpeg(
    camera_index: int,
    width: int,
    height: int,
    warmup_frames: int,
    jpeg_quality: int,
) -> bytes:
    """1920x1080을 촬영해 중앙 crop한 1024x1024 JPEG를 반환한다."""
    if cv2 is None:
        raise CaptureError("OpenCV가 설치되지 않았습니다.")
    if (width, height) != (IMAGE_WIDTH, IMAGE_HEIGHT):
        raise CaptureError("출력 해상도는 1024x1024로 고정되어 있습니다.")

    try:
        camera = cv2.VideoCapture(camera_index)
    except Exception as exc:
        raise CaptureError(f"카메라 {camera_index}를 열 수 없습니다: {exc}") from exc

    try:
        if not camera.isOpened():
            raise CaptureError(f"카메라 {camera_index}를 열 수 없습니다.")

        camera.set(cv2.CAP_PROP_FRAME_WIDTH, SOURCE_WIDTH)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, SOURCE_HEIGHT)

        for _ in range(warmup_frames):
            camera.read()

        ok, frame = camera.read()
        if not ok or frame is None:
            raise CaptureError("카메라에서 프레임을 읽지 못했습니다.")

        try:
            frame = prepare_frame(cv2, frame)
        except FramePreparationError as exc:
            raise CaptureError(str(exc)) from exc

        encoded_ok, encoded = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
        )
        if not encoded_ok:
            raise CaptureError("JPEG 인코딩에 실패했습니다.")
        return encoded.tobytes()
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError(f"카메라 처리에 실패했습니다: {exc}") from exc
    finally:
        camera.release()


def send_image(
    jpeg: bytes,
    url: str,
    field_name: str = "image",
    timeout: float = 10.0,
    form_data: Optional[Mapping[str, str]] = None,
) -> int:
    """JPEG를 multipart/form-data로 전송하고 HTTP 상태 코드를 반환한다."""
    if requests is None:
        raise SendError("requests가 설치되지 않았습니다.")

    files = {field_name: ("capture.jpg", jpeg, "image/jpeg")}
    request_kwargs = {
        "files": files,
        "timeout": timeout,
        "allow_redirects": False,
    }
    if form_data:
        request_kwargs["data"] = dict(form_data)
    try:
        response = requests.post(url, **request_kwargs)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SendError(f"{url}로 전송하지 못했습니다: {exc}") from exc
    if not 200 <= response.status_code < 300:
        raise SendError(f"{url}이(가) HTTP {response.status_code}을(를) 반환했습니다.")
    return response.status_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="USB 카메라 사진 한 장을 촬영해 HTTP로 전송합니다."
    )
    parser.add_argument("--url", required=True, help="수신 서버 URL")
    parser.add_argument("--camera-index", type=int, default=0, help="USB 카메라 인덱스")
    parser.add_argument(
        "--width", type=int, default=IMAGE_WIDTH, help="전송 이미지 가로 해상도(고정: 1024)"
    )
    parser.add_argument(
        "--height", type=int, default=IMAGE_HEIGHT, help="전송 이미지 세로 해상도(고정: 1024)"
    )
    parser.add_argument(
        "--warmup-frames", type=int, default=5, help="촬영 전 버릴 프레임 수"
    )
    parser.add_argument("--jpeg-quality", type=int, default=90, help="JPEG 품질(1~100)")
    parser.add_argument(
        "--field-name", default="image", help="multipart 파일 필드명(기본: image)"
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP 타임아웃(초)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if (args.width, args.height) != (IMAGE_WIDTH, IMAGE_HEIGHT):
        parser.error("전송 이미지 해상도는 1024x1024로 고정되어 있습니다.")
    if args.warmup_frames < 0:
        parser.error("--warmup-frames는 0 이상이어야 합니다.")
    if not 1 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality는 1에서 100 사이여야 합니다.")
    if not args.field_name:
        parser.error("--field-name은 비워 둘 수 없습니다.")
    if args.timeout <= 0:
        parser.error("--timeout은 0보다 커야 합니다.")

    try:
        jpeg = capture_jpeg(
            camera_index=args.camera_index,
            width=args.width,
            height=args.height,
            warmup_frames=args.warmup_frames,
            jpeg_quality=args.jpeg_quality,
        )
    except CaptureError as exc:
        print(f"촬영 실패: {exc}", file=sys.stderr)
        return EXIT_CAPTURE_ERROR

    try:
        status_code = send_image(
            jpeg=jpeg,
            url=args.url,
            field_name=args.field_name,
            timeout=args.timeout,
        )
    except SendError as exc:
        print(f"전송 실패: {exc}", file=sys.stderr)
        return EXIT_SEND_ERROR

    print(f"전송 완료: HTTP {status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
