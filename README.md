# Raspberry Pi USB 카메라 클라이언트

USB fisheye 카메라를 1920×1080으로 촬영하고, 중앙 영역을 1024×1024 입력으로 변환해 캘리브레이션 및 전송한 뒤 callback으로 받은 BEV PNG를 Raspberry Pi 디스플레이에 표시합니다.

주요 프로그램은 다음과 같습니다.

- `calibrate_camera.py`: A4 체커보드로 fisheye intrinsic을 구해 `camera.json` 생성
- `capture_and_send.py`: 1024×1024 JPEG 한 장만 전송하는 기본 클라이언트
- `capture_bev_display.py`: callback listener 시작 → 촬영·전송 → BEV PNG 수신 → 전체 화면 표시

현재 캘리브레이션 모델은 `BirdEyeFinal` 규격에 맞춘 OpenCV `fisheye` 전용입니다. 일반 pinhole 렌즈에는 이 결과를 사용하면 안 됩니다.

## 1. Raspberry Pi 준비

64-bit Raspberry Pi OS와 Python 3.9 이상을 권장합니다.

```bash
sudo apt update
sudo apt install -y python3-venv v4l-utils

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

카메라가 1920×1080을 지원하는지 확인합니다.

```bash
v4l2-ctl --list-devices
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

카메라 접근 권한이 없다면 사용자를 `video` 그룹에 추가한 뒤 다시 로그인합니다.

```bash
sudo usermod -aG video "$USER"
```

BEV 화면은 Pygame/SDL로 표시합니다. Raspberry Pi OS Desktop의 로컬 그래픽 세션에서 실행하는 것이 기본입니다. SSH 세션이나 Raspberry Pi OS Lite에서 직접 DRM/KMS로 표시하려면 해당 장치의 SDL 비디오 드라이버 설정을 별도로 확인해야 합니다.

## 2. A4 체커보드 준비

OpenCV의 [A4 체커보드 패턴](https://github.com/opencv/opencv/blob/4.x/doc/pattern.png)을 사용할 수 있습니다.

1. 페이지 맞춤을 끄고 100% 배율로 인쇄합니다.
2. 종이가 휘지 않도록 평평하고 단단한 판에 붙입니다.
3. 인쇄된 사각형 한 변을 자로 측정합니다. 25 mm라면 `0.025` m입니다.
4. `--board-cols`와 `--board-rows`에는 사각형 수가 아니라 내부 코너 수를 입력합니다. 기본값은 9×6입니다.

## 3. 1024×1024 카메라 캘리브레이션

캘리브레이션과 실제 전송은 모두 같은 프레임 처리 과정을 사용합니다.

1. USB 카메라에서 1920×1080 프레임을 촬영합니다.
2. 좌우 420픽셀씩 제외해 중앙 1080×1080 영역을 자릅니다.
3. OpenCV `INTER_AREA`로 1024×1024로 축소합니다.

카메라가 실제로 1920×1080 프레임을 제공하지 않으면 즉시 실패합니다. `camera.json`의 intrinsic은 최종 1024×1024 이미지를 기준으로 계산되므로, 서버에 전송하는 이미지와 좌표계가 같습니다.

```bash
python calibrate_camera.py \
  --camera-index 0 \
  --board-cols 9 \
  --board-rows 6 \
  --square-size-m 0.025 \
  --samples 20 \
  --output camera.json
```

영상 미리보기는 없습니다. 체커보드를 중앙과 네 모서리, 가까운 위치와 먼 위치, 좌우·상하로 기울인 자세로 천천히 움직입니다. 서로 다른 자세가 검출될 때마다 `accepted view 1/20`처럼 표시됩니다.

완료되면 다음 intrinsic이 기록됩니다.

```json
{
  "model": "fisheye",
  "width": 1024,
  "height": 1024,
  "fx": 390.0,
  "fy": 390.0,
  "cx": 512.0,
  "cy": 512.0,
  "distortion": [0.05, 0.01, -0.003, -0.0005]
}
```

`cx`와 `cy`는 OpenCV 결과에 0.5를 더해 현재 `BirdEyeFinal`의 픽셀 셀 중심 좌표 규격으로 저장합니다.

`world_from_camera`와 `camera_position_world`는 intrinsic 촬영으로 구할 수 없습니다. 최종 설치 상태에서 별도로 구한 값을 기록할 때만 다음 옵션을 함께 사용합니다.

```bash
python calibrate_camera.py \
  --square-size-m 0.025 \
  --world-from-camera -1 0 0 0 1 0 0 0 -1 \
  --camera-position-world -2 12 14
```

`clipping_near_m`, `clipping_far_m`, `geometry_z_eps`도 캘리브레이션 결과가 아니므로 자동 생성하지 않습니다. 생성된 파일은 PC의 `BirdEyeFinal/inputs/camera.json`으로 복사합니다. `inputs`는 복수형입니다.

## 4. 촬영 → callback → BEV 표시

예를 들어 PC가 `192.168.0.10`, Raspberry Pi가 `192.168.0.20`이라면 다음처럼 실행합니다.

```bash
python capture_bev_display.py \
  --url http://192.168.0.10:8000/upload \
  --callback-host 192.168.0.20 \
  --callback-port 8765 \
  --camera-index 0
```

프로그램은 callback listener를 먼저 연 뒤 사진을 전송하므로, 서버가 업로드 응답 전에 즉시 callback하더라도 결과가 유실되지 않습니다. callback은 기본 300초 동안 기다립니다.

BEV를 받으면 전체 화면으로 표시하며 `Esc`, `Q` 또는 창 닫기로 종료합니다. 테스트할 때는 `--windowed`를 사용하면 1024×1024 창으로 표시합니다.

Pi의 `localhost`는 Pi 자신을 가리킵니다. `--url`에는 PC의 내부망 주소를, `--callback-host`에는 PC에서 접근할 수 있는 Pi의 내부망 주소를 입력해야 합니다. PC에서 Pi의 callback 포트로 접근할 수 있어야 합니다.

## 5. HTTP callback 계약

`BirdEyeFinal`에는 네트워크 callback 구현이 없고 PNG 파일 출력만 있으므로, 이 클라이언트는 다음 계약을 사용합니다.

### 최초 업로드

- 메서드: `POST`
- 형식: `multipart/form-data`
- `image`: `capture.jpg`, `image/jpeg`, 1024×1024(1920×1080 중앙 crop 후 축소)
- `camera`: 클라이언트의 `capture_and_send.py`와 같은 디렉터리에 있는 `camera.json`, `application/json`
- `callback_url`: Pi가 생성한 일회용 callback URL
- 성공 조건: HTTP 2xx
- 리다이렉트: 허용하지 않음

이미지와 callback 필드명이 다르면 `--field-name`과 `--callback-field`로 변경할 수 있습니다. `camera.json` 파일 필드명은 `camera`로 고정되어 있습니다. 파일이 없거나 읽을 수 없으면 클라이언트는 HTTP 요청을 보내지 않고 전송 오류로 종료합니다.

### 서버 callback

- 메서드: `POST <callback_url>`
- `Content-Type: image/png`
- body: 표시할 PNG 파일의 원본 바이트
- 권장 파일: `BirdEyeFinal`의 `bev_occupied_unknown_free.png`
- 성공 응답: HTTP 204

서버 측 전송 예시는 다음과 같습니다.

```python
from pathlib import Path

import requests

requests.post(
    callback_url,
    data=Path("bev_occupied_unknown_free.png").read_bytes(),
    headers={"Content-Type": "image/png"},
    timeout=10,
).raise_for_status()
```

callback URL에는 실행마다 임의 토큰이 포함됩니다. 클라이언트는 PNG 한 장만 받으며, 최대 크기는 16 MiB입니다.

## 6. BEV 표시 규칙

`BirdEyeFinal/bev_test/infer.py`의 tri-state BEV는 8-bit grayscale PNG입니다.

- occupied: `0`, 검정
- unknown: `127`, 회색
- free: `255`, 흰색

수신 단계에서는 임계값, 이진화, smoothing, morphology, denoise, 클래스 변경 같은 성능 관련 후처리를 하지 않습니다. PNG 형식만 검사하고 원본을 그대로 표시합니다.

BEV 크기는 모델 설정에 따라 192×192 또는 512×512일 수 있습니다. 화면 표시 때만 nearest-neighbor로 확대하며, 화면 종횡비가 다르면 검은 여백을 추가합니다. 의미값과 셀 경계는 변경하지 않습니다.

## 7. callback 없이 사진만 전송

```bash
python capture_and_send.py \
  --camera-index 0 \
  --url http://192.168.0.10:8000/upload
```

촬영과 캘리브레이션 모두 카메라 원본이 1920×1080이 아니면 즉시 실패합니다. 두 프로그램은 동일한 중앙 crop 및 축소 함수를 사용합니다.

## 8. 종료 코드와 테스트

- `0`: 성공
- `3`: 카메라 촬영 실패
- `4`: 업로드/HTTP 실패
- `5`: callback bind·수신·timeout 실패
- `6`: 디스플레이 실패

```bash
python -m unittest discover -s tests -v
```

자동 테스트는 1920×1080 중앙 crop 및 1024×1024 축소, 합성 fisheye 캘리브레이션, 실제 localhost multipart/callback 왕복, 업로드 응답 전 즉시 callback, PNG 검증 및 화면 맞춤을 확인합니다. 실제 USB 카메라와 Raspberry Pi 디스플레이는 대상 장치에서 마지막으로 확인해야 합니다.
