"""Optional offline Brown-model intrinsics fit; RealSense factory profiles are preferred."""
from pathlib import Path
import cv2
import numpy as np
from .models import CameraIntrinsics, CalibrationError
from .detection import detect_corners, estimate_board
from .session import save_json, file_hash


def calibrate_intrinsics(paths, spec, *, camera_id, stream_id, optical_frame, output, data_origin):
    if data_origin not in ('measured','synthetic'):raise CalibrationError('Declare the intrinsic dataset origin')
    output=Path(output)
    report_path=output.with_name(output.stem+'_report.json')
    if output.exists() or report_path.exists():raise CalibrationError('Refusing to overwrite camera intrinsics or report')
    found=[];rejected=[];size=None
    for path in paths:
        path=Path(path);image=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
        if image is None:
            rejected.append({'image':str(path),'reason':'Unreadable image'});continue
        if size is None:size=(image.shape[1],image.shape[0])
        if size!=(image.shape[1],image.shape[0]):raise CalibrationError('All intrinsic images must have identical resolution')
        try:
            _,obj,img,ids,coverage=detect_corners(image,spec)
            found.append({'path':path,'object':obj,'image':img})
        except CalibrationError as exc:rejected.append({'image':str(path),'reason':str(exc)})
    if len(found)<12:raise CalibrationError('Need at least 12 detected intrinsic images; prefer 20–30 varied tilts and image locations')
    train=[i for i in range(len(found)) if i%4!=3];holdout=[i for i in range(len(found)) if i%4==3]
    rms,k,d,rv,tv=cv2.calibrateCamera([found[i]['object'] for i in train],[found[i]['image'] for i in train],size,None,None)
    camera=CameraIntrinsics(camera_id=camera_id,stream_id=stream_id,optical_frame=optical_frame,width=size[0],height=size[1],
        K=k.tolist(),distortion_model='opencv_brown',distortion=d.reshape(-1).tolist(),
        provenance=f'Offline ChArUco intrinsic fit, data_origin={data_origin}; changes no device factory settings')
    errors=[]
    for i in holdout:
        image=cv2.imread(str(found[i]['path']),cv2.IMREAD_GRAYSCALE)
        result,_=estimate_board(image,spec,camera,max_rms_px=1e6)
        errors.append(result['reprojection_rms_px'])
    normals=np.array([cv2.Rodrigues(r)[0][:,2] for r in rv])
    spread=np.linalg.svd(normals-normals.mean(axis=0),compute_uv=False)
    passed=bool(np.isfinite(rms) and rms<=1. and max(errors)<=1. and spread[1]>.1)
    output.parent.mkdir(parents=True,exist_ok=True)
    report={'data_origin':data_origin,'status':'passed_validation' if passed else 'failed_validation',
        'training_rms_px':float(rms),'heldout_rms_px':errors,'tilt_spread_singular_values':spread.tolist(),
        'training_indices':train,'heldout_indices':holdout,'rejected_images':rejected,
        'used_images':[{'file':str(f['path']),'sha256':file_hash(f['path'])} for f in found],
        'candidate_camera':camera.model_dump(),'opencv_version':cv2.__version__,
        'limits':'Pixel validation alone does not establish absolute metric accuracy or recover printer scale. '
                 'This is image-stream calibration, not RealSense stereo/depth factory calibration.'}
    save_json(report_path,report)
    if passed:save_json(output,camera.model_dump())
    return report
