"""Explicit units, frames and synchronized acquisition contracts."""
from dataclasses import dataclass
from typing import Literal, Protocol
import hashlib
import json
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


class CalibrationError(ValueError):
    pass


def transform(value):
    a = np.asarray(value, dtype=float)
    if a.shape != (4, 4) or not np.isfinite(a).all():
        raise CalibrationError('Transform must be a finite 4x4 matrix')
    if not np.allclose(a[3], [0, 0, 0, 1], atol=1e-8):
        raise CalibrationError('Invalid homogeneous transform bottom row')
    if not np.allclose(a[:3, :3].T @ a[:3, :3], np.eye(3), atol=1e-6) or abs(np.linalg.det(a[:3, :3])-1) > 1e-6:
        raise CalibrationError('Rotation must be orthonormal and right-handed')
    return a


def inverse(value):
    a = transform(value)
    b = np.eye(4); b[:3, :3] = a[:3, :3].T
    b[:3, 3] = -b[:3, :3] @ a[:3, 3]
    return b


def fingerprint(value):
    if isinstance(value, BaseModel): value = value.model_dump()
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class BoardSpec(Record):
    name: str = 'd405_charuco_a4_25mm_v1'
    squares_x: int = Field(default=7, ge=3, le=10)
    squares_y: int = Field(default=10, ge=3, le=14)
    square_m: FiniteFloat = Field(default=.025, gt=0)
    marker_m: FiniteFloat = Field(default=.018, gt=0)
    dictionary: Literal['DICT_4X4_50'] = 'DICT_4X4_50'
    legacy_pattern: Literal[False] = False

    @model_validator(mode='after')
    def dimensions(self):
        if self.marker_m >= self.square_m:
            raise ValueError('Marker must fit inside a square')
        if self.squares_x * self.squares_y // 2 > 50:
            raise ValueError('Board exceeds the dictionary marker capacity')
        return self


class CameraIntrinsics(Record):
    camera_id: str = Field(min_length=1)
    stream_id: str = Field(min_length=1)
    optical_frame: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    K: list[list[FiniteFloat]]
    distortion_model: Literal['none', 'opencv_brown']
    distortion: list[FiniteFloat]
    provenance: str = Field(min_length=1)

    @model_validator(mode='after')
    def valid_projection(self):
        k = np.asarray(self.K)
        if k.shape != (3, 3) or k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1]):
            raise ValueError('K must be a valid 3x3 pinhole intrinsic matrix')
        if abs(k[0, 1]) > 1e-8 or abs(k[1, 0]) > 1e-8:
            raise ValueError('Only zero-skew intrinsics are supported')
        if not (0 <= k[0, 2] < self.width and 0 <= k[1, 2] < self.height):
            raise ValueError('Principal point is outside the image')
        if self.distortion_model == 'none' and any(abs(c) > 1e-12 for c in self.distortion):
            raise ValueError('Rectified/none images must have zero distortion')
        if self.distortion_model == 'opencv_brown' and len(self.distortion) not in (4, 5, 8, 12, 14):
            raise ValueError('Unsupported OpenCV Brown distortion coefficient count')
        return self

    def matrices(self):
        coefficients = self.distortion if self.distortion_model != 'none' else [0.]*5
        return np.asarray(self.K, dtype=float), np.asarray(coefficients, dtype=float)

    @classmethod
    def from_realsense_intrinsics(cls, *, camera_id, stream_id, optical_frame,
                                 width, height, fx, fy, ppx, ppy, model, coeffs):
        """Accept SDK values, not a device handle. No RealSense SDK import here."""
        name = str(model).split('.')[-1].lower()
        if name not in ('none', 'brown_conrady'):
            raise CalibrationError('Rectify this RealSense distortion model in the driver first; '
                                   'modified/inverse Brown and fisheye are not OpenCV Brown coefficients')
        return cls(camera_id=camera_id, stream_id=stream_id, optical_frame=optical_frame,
                   width=int(width), height=int(height), K=[[float(fx), 0., float(ppx)], [0., float(fy), float(ppy)], [0., 0., 1.]],
                   distortion_model='none' if name == 'none' else 'opencv_brown',
                   distortion=[float(c) for c in coeffs], provenance='RealSense active stream profile; supplied by external adapter')


class FrameMetadata(Record):
    timestamp_s: FiniteFloat = Field(ge=0)
    clock_id: str = Field(min_length=1)
    # Must match the active image stream profile, not merely the camera model.
    camera: CameraIntrinsics
    depth_timestamp_s: FiniteFloat | None = None
    depth_optical_frame: str | None = None


class RobotPose(Record):
    timestamp_s: FiniteFloat = Field(ge=0)
    clock_id: str = Field(min_length=1)
    base_frame: str = Field(min_length=1)
    end_frame: str = Field(min_length=1)
    stationary: bool
    T_base_end: list[list[FiniteFloat]]
    translation_unit: Literal['m'] = 'm'

    @model_validator(mode='after')
    def valid_transform(self):
        transform(self.T_base_end)
        return self


class SessionConfig(Record):
    schema_version: Literal['1.0'] = '1.0'
    mount: Literal['eye-in-hand', 'eye-to-hand']
    data_origin: Literal['measured', 'synthetic']
    base_frame: str = Field(min_length=1)
    end_frame: str = Field(min_length=1)
    clock_id: str = Field(min_length=1)
    board: BoardSpec
    camera: CameraIntrinsics
    max_timestamp_skew_s: FiniteFloat = Field(default=.03, gt=0)
    min_corners: int = Field(default=12, ge=6)
    max_reprojection_rms_px: FiniteFloat = Field(default=1., gt=0)
    min_image_coverage: FiniteFloat = Field(default=.02, gt=0, lt=1)


@dataclass
class CapturePacket:
    image: np.ndarray  # uint8 grayscale or BGR, unmirrored, at declared resolution
    frame: FrameMetadata
    robot: RobotPose
    depth_m: np.ndarray | None = None  # optional, already aligned to image optical frame


class SynchronizedSource(Protocol):
    def read_stationary_packet(self) -> CapturePacket:
        """Return measured synchronized data. This interface has no motion methods."""
        ...
