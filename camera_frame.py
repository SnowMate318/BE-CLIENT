"""Shared camera-frame preparation for calibration and capture."""

SOURCE_WIDTH = 1920
SOURCE_HEIGHT = 1080
IMAGE_WIDTH = 1024
IMAGE_HEIGHT = 1024


class FramePreparationError(RuntimeError):
    """The camera frame cannot be converted to the required input image."""


def prepare_frame(cv2, frame):
    """Center-crop a 1920x1080 frame to 1080x1080, then resize to 1024x1024."""
    if frame is None:
        raise FramePreparationError("카메라 프레임이 비어 있습니다.")

    actual_height, actual_width = frame.shape[:2]
    if (actual_width, actual_height) != (SOURCE_WIDTH, SOURCE_HEIGHT):
        raise FramePreparationError(
            "카메라가 필요한 원본 해상도를 제공하지 않습니다: "
            f"요청 {SOURCE_WIDTH}x{SOURCE_HEIGHT}, 실제 {actual_width}x{actual_height}"
        )

    crop_size = SOURCE_HEIGHT
    left = (SOURCE_WIDTH - crop_size) // 2
    square = frame[0:crop_size, left : left + crop_size]
    return cv2.resize(
        square,
        (IMAGE_WIDTH, IMAGE_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )
