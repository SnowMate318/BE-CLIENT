"""Show the processed 1024x1024 USB camera image in real time."""

import cv2
import pygame

from camera_frame import SOURCE_HEIGHT, SOURCE_WIDTH, prepare_frame


def main() -> None:
    camera = cv2.VideoCapture(0)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, SOURCE_WIDTH)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, SOURCE_HEIGHT)

    try:
        if not camera.isOpened():
            raise RuntimeError("카메라를 열 수 없습니다.")

        pygame.init()
        screen = pygame.display.set_mode((1024, 1024))
        pygame.display.set_caption("Camera Preview - 1024x1024")
        clock = pygame.time.Clock()
        print("실시간 카메라를 표시합니다. Esc 또는 Q를 누르면 종료됩니다.")

        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key in (
                    pygame.K_ESCAPE,
                    pygame.K_q,
                ):
                    running = False

            if not running:
                break

            ok, raw = camera.read()
            if not ok:
                raise RuntimeError("프레임을 읽지 못했습니다.")

            processed = prepare_frame(cv2, raw)
            rgb = cv2.cvtColor(processed, cv2.COLOR_BGR2RGB)
            surface = pygame.surfarray.make_surface(rgb.swapaxes(0, 1))
            screen.blit(surface, (0, 0))
            pygame.display.flip()
            clock.tick(30)
    finally:
        camera.release()
        pygame.quit()


if __name__ == "__main__":
    main()
