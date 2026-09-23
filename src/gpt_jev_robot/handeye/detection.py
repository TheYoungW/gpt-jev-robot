"""ChArUco image observations with pixel residual and optional depth diagnostics."""
import cv2
import numpy as np
from .board import make_board
from .models import CalibrationError, transform


def detect_corners(image, spec, camera=None, min_corners=12, min_coverage=.02):
    if image.dtype != np.uint8 or image.ndim not in (2, 3) or (image.ndim == 3 and image.shape[2] != 3):
        raise CalibrationError('Image must be uint8 grayscale or BGR, not a depth image')
    board = make_board(spec)
    params = cv2.aruco.CharucoParameters()
    if camera is not None:
        if image.shape[:2] != (camera.height, camera.width):
            raise CalibrationError('Image resolution differs from its intrinsic calibration')
        params.cameraMatrix, params.distCoeffs = camera.matrices()
    detector = cv2.aruco.CharucoDetector(board, params)
    corners, ids, marker_corners, marker_ids = detector.detectBoard(image)
    count = 0 if ids is None else len(ids)
    if count < min_corners:
        raise CalibrationError(f'Only {count} ChArUco corners; need at least {min_corners}')
    obj, img = board.matchImagePoints(corners, ids)
    obj, img = obj.reshape(-1, 3), img.reshape(-1, 2)
    if np.linalg.matrix_rank(obj[:, :2]-obj[:, :2].mean(axis=0), tol=1e-6) < 2:
        raise CalibrationError('Detected board corners are collinear')
    coverage = cv2.contourArea(cv2.convexHull(img.astype(np.float32))) / (image.shape[0]*image.shape[1])
    if coverage < min_coverage:
        raise CalibrationError('Board corner coverage is too small')
    return board, obj, img, ids.reshape(-1), float(coverage)


def estimate_board(image, spec, camera, *, min_corners=12, max_rms_px=1., min_coverage=.02, depth_m=None):
    board, obj, img, ids, coverage = detect_corners(image, spec, camera, min_corners, min_coverage)
    k, dist = camera.matrices()
    solutions = cv2.solvePnPGeneric(obj, img, k, dist, flags=cv2.SOLVEPNP_IPPE)
    candidates = []
    for rvec, tvec in zip(solutions[1], solutions[2]):
        rotation = cv2.Rodrigues(rvec)[0]
        camera_points = obj @ rotation.T + tvec.reshape(3)
        if np.any(camera_points[:, 2] <= 0):continue
        predicted = cv2.projectPoints(obj, rvec, tvec, k, dist)[0].reshape(-1, 2)
        rms = float(np.sqrt(np.mean(np.sum((predicted-img)**2, axis=1))))
        candidates.append((rms, rvec.copy(), tvec.copy()))
    if not candidates:raise CalibrationError('No planar pose solution puts the board in front of the camera')
    candidates.sort(key=lambda c:c[0])
    _, rvec, tvec = candidates[0]
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, k, dist, rvec, tvec)
    predicted = cv2.projectPoints(obj, rvec, tvec, k, dist)[0].reshape(-1, 2)
    errors = np.linalg.norm(predicted-img, axis=1)
    rms = float(np.sqrt(np.mean(errors**2)))
    if not np.isfinite(rms) or rms > max_rms_px:
        raise CalibrationError(f'Board reprojection RMS {rms:.3f} px exceeds {max_rms_px:.3f} px')
    pose = np.eye(4);pose[:3, :3] = cv2.Rodrigues(rvec)[0];pose[:3, 3] = tvec.reshape(3)
    transform(pose)
    points_camera = obj @ pose[:3, :3].T + pose[:3, 3]
    if np.any(points_camera[:, 2] <= 0):raise CalibrationError('Refinement placed the board behind the camera')
    result = {'T_camera_board':pose.tolist(), 'corner_ids':ids.tolist(), 'image_points_px':img.tolist(),
              'object_points_board_m':obj.tolist(), 'corner_count':len(ids), 'image_coverage':coverage,
              'reprojection_rms_px':rms, 'reprojection_max_px':float(errors.max()),
              'planar_alternative_rms_gap_px':candidates[1][0]-candidates[0][0] if len(candidates)>1 else None}
    result['depth_check'] = None
    if depth_m is not None:
        if depth_m.shape != image.shape[:2] or not np.issubdtype(depth_m.dtype, np.floating):
            raise CalibrationError('Optional depth must be an aligned floating-point meter array at image resolution')
        residuals=[]
        for (x, y), expected in zip(img, points_camera[:, 2]):
            x, y = int(round(float(x))), int(round(float(y)))
            patch = depth_m[max(0,y-2):y+3, max(0,x-2):x+3]
            valid = patch[np.isfinite(patch) & (patch>0)]
            if len(valid)>=5:residuals.append(float(np.median(valid)-expected))
        result['depth_check']={'valid_corner_count':len(residuals),
            'median_signed_error_m':float(np.median(residuals)) if residuals else None,
            'median_absolute_error_m':float(np.median(np.abs(residuals))) if residuals else None,
            'diagnostic_only':True, 'note':'No depth-scale correction applied. Printed board depth can contain holes.'}
    overlay = image.copy() if image.ndim==3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    cv2.aruco.drawDetectedCornersCharuco(overlay, img.reshape(-1,1,2), ids.reshape(-1,1))
    cv2.drawFrameAxes(overlay, k, dist, rvec, tvec, .04)
    return result, overlay
