"""Capture one raw camera frame and save the processed 1024x1024 image."""

import cv2

from camera_frame import SOURCE_HEIGHT, SOURCE_WIDTH, prepare_frame


camera = cv2.VideoCapture(0)
camera.set(cv2.CAP_PROP_FRAME_WIDTH, SOURCE_WIDTH)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, SOURCE_HEIGHT)

try:
    if not camera.isOpened():
        raise RuntimeError("카메라를 열 수 없습니다.")

    for _ in range(5):
        camera.read()

    ok, raw = camera.read()
    if not ok:
        raise RuntimeError("프레임을 촬영하지 못했습니다.")

    processed = prepare_frame(cv2, raw)
    cv2.imwrite("camera_raw.jpg", raw)
    cv2.imwrite("camera_1024.jpg", processed)
    print("camera_raw.jpg와 camera_1024.jpg를 저장했습니다.")
finally:
    camera.release()
