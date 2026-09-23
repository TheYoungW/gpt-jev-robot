"""Fill in acquisition later. This file imports no device SDK and issues no motion.

The adapter owns stream selection, SDK distortion conversion, metric depth,
exposure/pose time alignment and the measured stationary flag.
"""
from gpt_jev_robot.handeye.models import (
    CameraIntrinsics, FrameMetadata, RobotPose, CapturePacket,
)
from gpt_jev_robot.handeye.session import CalibrationSession


def camera_profile_from_sdk(intrinsics, *, serial, stream_id, optical_frame):
    """Call with get_intrinsics() of the exact profile that produced the image."""
    return CameraIntrinsics.from_realsense_intrinsics(
        camera_id=serial, stream_id=stream_id, optical_frame=optical_frame,
        width=intrinsics.width, height=intrinsics.height,
        fx=intrinsics.fx, fy=intrinsics.fy, ppx=intrinsics.ppx, ppy=intrinsics.ppy,
        model=intrinsics.model, coeffs=intrinsics.coeffs,
    )


def make_packet(*, image_bgr, camera_profile, image_time_s, robot_time_s,
                common_clock_id, measured_T_base_end_m, base_frame, end_frame,
                measured_robot_stationary, aligned_depth_m=None,
                depth_time_s=None, depth_optical_frame=None):
    """Inputs must be measured; never supply a commanded robot target as feedback.

    Map both timestamps to the same clock before calling. Aligned depth, if any,
    must be float meters in the selected image optical frame, with its timestamp.
    """
    return CapturePacket(
        image=image_bgr,
        frame=FrameMetadata(timestamp_s=float(image_time_s),clock_id=common_clock_id,
                            camera=camera_profile,depth_timestamp_s=depth_time_s,
                            depth_optical_frame=depth_optical_frame),
        robot=RobotPose(timestamp_s=float(robot_time_s),clock_id=common_clock_id,
                        base_frame=base_frame,end_frame=end_frame,
                        stationary=measured_robot_stationary,
                        T_base_end=measured_T_base_end_m.tolist()),
        depth_m=aligned_depth_m,
    )


class YourDriverSource:
    def read_stationary_packet(self) -> CapturePacket:
        # Implement acquisition and synchronization in your own driver layer.
        # The calibration package deliberately does not move or enable the robot.
        raise NotImplementedError('Connect your camera and measured robot-pose acquisition here')


def record_one(session_directory, source):
    return CalibrationSession(session_directory).capture_from(source)


if __name__ == '__main__':
    raise SystemExit('Adapter template only. Implement read_stationary_packet() in your driver layer first.')
