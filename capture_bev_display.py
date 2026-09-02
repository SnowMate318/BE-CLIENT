"""Capture one 1024x1024 image, receive a BEV callback, and display it."""

from __future__ import annotations

import argparse
import io
import sys
import time

from bev_callback import CallbackError, CallbackReceiver
from capture_and_send import (
    CAMERA_FIELD_NAME,
    CaptureError,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    SendError,
    capture_jpeg,
    send_image,
)


EXIT_CALLBACK_ERROR = 5
EXIT_DISPLAY_ERROR = 6


class DisplayError(RuntimeError):
    """The BEV image could not be displayed."""


def fit_rect(source_size: tuple[int, int], target_size: tuple[int, int]) -> tuple[int, int, int, int]:
    source_width, source_height = source_size
    target_width, target_height = target_size
    if min(source_width, source_height, target_width, target_height) < 1:
        raise DisplayError("image and display dimensions must be positive")
    scale = min(target_width / source_width, target_height / source_height)
    width = max(1, round(source_width * scale))
    height = max(1, round(source_height * scale))
    return width, height, (target_width - width) // 2, (target_height - height) // 2


def display_bev(pygame, png: bytes, windowed: bool, display_seconds: float) -> None:
    """Show a BEV PNG until Esc/Q/window close, or for the requested duration."""
    try:
        pygame.display.init()
        flags = 0 if windowed else pygame.FULLSCREEN
        requested_size = (IMAGE_WIDTH, IMAGE_HEIGHT) if windowed else (0, 0)
        screen = pygame.display.set_mode(requested_size, flags)
        pygame.display.set_caption("BEV Map")
        pygame.mouse.set_visible(windowed)
        image = pygame.image.load(io.BytesIO(png), "bev.png").convert()
        width, height, x, y = fit_rect(image.get_size(), screen.get_size())
        # Nearest-neighbor display scaling only: keep BEV class values unchanged.
        scaled = pygame.transform.scale(image, (width, height))
        screen.fill((0, 0, 0))
        screen.blit(scaled, (x, y))
        pygame.display.flip()

        deadline = time.monotonic() + display_seconds if display_seconds > 0 else None
        clock = pygame.time.Clock()
        running = True
        while running and (deadline is None or time.monotonic() < deadline):
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
            clock.tick(30)
    except Exception as exc:
        raise DisplayError(f"cannot display BEV map: {exc}") from exc
    finally:
        pygame.quit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="1024x1024 사진을 전송하고 callback BEV PNG를 화면에 표시합니다."
    )
    parser.add_argument("--url", required=True, help="PC의 이미지 업로드 URL")
    parser.add_argument(
        "--callback-host",
        required=True,
        help="PC에서 접근 가능한 Raspberry Pi 내부망 IP 또는 호스트명",
    )
    parser.add_argument("--callback-bind", default="0.0.0.0", help="callback 수신 bind 주소")
    parser.add_argument("--callback-port", type=int, default=8765, help="callback 수신 포트")
    parser.add_argument(
        "--callback-field", default="callback_url", help="업로드에 포함할 callback URL 필드명"
    )
    parser.add_argument("--callback-timeout", type=float, default=300.0, help="callback 대기 시간(초)")
    parser.add_argument("--camera-index", type=int, default=0, help="USB 카메라 인덱스")
    parser.add_argument("--warmup-frames", type=int, default=5, help="촬영 전 버릴 프레임 수")
    parser.add_argument("--jpeg-quality", type=int, default=90, help="JPEG 품질(1~100)")
    parser.add_argument("--field-name", default="image", help="업로드 이미지 필드명")
    parser.add_argument("--upload-timeout", type=float, default=10.0, help="업로드 HTTP 타임아웃(초)")
    parser.add_argument("--windowed", action="store_true", help="전체 화면 대신 1024x1024 창 사용")
    parser.add_argument(
        "--display-seconds",
        type=float,
        default=0.0,
        help="표시 시간(초). 0이면 Esc/Q/창 닫기까지 유지",
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not 0 <= args.callback_port <= 65535:
        parser.error("--callback-port는 0에서 65535 사이여야 합니다.")
    if not args.callback_field or not args.field_name:
        parser.error("업로드 필드명은 비워 둘 수 없습니다.")
    if args.field_name == CAMERA_FIELD_NAME:
        parser.error(f"--field-name은 {CAMERA_FIELD_NAME!r}일 수 없습니다.")
    if args.callback_timeout <= 0 or args.upload_timeout <= 0:
        parser.error("timeout은 0보다 커야 합니다.")
    if args.warmup_frames < 0:
        parser.error("--warmup-frames는 0 이상이어야 합니다.")
    if not 1 <= args.jpeg_quality <= 100:
        parser.error("--jpeg-quality는 1에서 100 사이여야 합니다.")
    if args.display_seconds < 0:
        parser.error("--display-seconds는 0 이상이어야 합니다.")


def run(args: argparse.Namespace, pygame) -> int:
    try:
        with CallbackReceiver(args.callback_bind, args.callback_port) as receiver:
            callback_url = receiver.callback_url(args.callback_host)
            print(f"callback 대기 주소: {callback_url}")
            jpeg = capture_jpeg(
                camera_index=args.camera_index,
                width=IMAGE_WIDTH,
                height=IMAGE_HEIGHT,
                warmup_frames=args.warmup_frames,
                jpeg_quality=args.jpeg_quality,
            )
            status = send_image(
                jpeg=jpeg,
                url=args.url,
                field_name=args.field_name,
                timeout=args.upload_timeout,
                form_data={args.callback_field: callback_url},
            )
            print(f"사진 전송 완료: HTTP {status}; BEV callback 대기 중")
            png = receiver.wait(args.callback_timeout)
    except CaptureError as exc:
        print(f"촬영 실패: {exc}", file=sys.stderr)
        return 3
    except SendError as exc:
        print(f"전송 실패: {exc}", file=sys.stderr)
        return 4
    except (CallbackError, OSError) as exc:
        print(f"callback 실패: {exc}", file=sys.stderr)
        return EXIT_CALLBACK_ERROR

    try:
        display_bev(pygame, png, args.windowed, args.display_seconds)
    except DisplayError as exc:
        print(f"화면 표시 실패: {exc}", file=sys.stderr)
        return EXIT_DISPLAY_ERROR
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    try:
        import pygame
    except ImportError:
        print("화면 표시 실패: pygame이 설치되지 않았습니다.", file=sys.stderr)
        return EXIT_DISPLAY_ERROR
    return run(args, pygame)


if __name__ == "__main__":
    raise SystemExit(main())
