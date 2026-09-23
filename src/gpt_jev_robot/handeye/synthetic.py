"""Synthetic integration fixtures only; no claim of real D405 calibration."""
from pathlib import Path
import cv2
import numpy as np
from scipy.spatial.transform import Rotation
from .board import make_board
from .models import BoardSpec, CameraIntrinsics, FrameMetadata, RobotPose, SessionConfig, CapturePacket, inverse
from .session import CalibrationSession, save_json
from .solver import pose_errors


def pose(angles, xyz):
    out=np.eye(4);out[:3,:3]=Rotation.from_euler('xyz',angles,degrees=True).as_matrix();out[:3,3]=xyz
    return out


def render_board(spec, camera, T_camera_board):
    texture=make_board(spec).generateImage((spec.squares_x*400,spec.squares_y*400),marginSize=0,borderBits=1)
    w,h=spec.squares_x*spec.square_m,spec.squares_y*spec.square_m
    points=np.array([[0,0,0],[w,0,0],[w,h,0],[0,h,0]],dtype=float)
    k,d=camera.matrices()
    projected=cv2.projectPoints(points,cv2.Rodrigues(T_camera_board[:3,:3])[0],T_camera_board[:3,3],k,d)[0].reshape(4,2)
    src=np.array([[0,0],[texture.shape[1],0],[texture.shape[1],texture.shape[0]],[0,texture.shape[0]]],dtype=np.float32)
    warp=cv2.getPerspectiveTransform(src,projected.astype(np.float32))
    image=cv2.warpPerspective(texture,warp,(camera.width,camera.height),flags=cv2.INTER_LINEAR,borderValue=255)
    return cv2.GaussianBlur(image,(3,3),.45)


def generate_demo(directory, mount='eye-in-hand', count=24):
    spec=BoardSpec()
    camera=CameraIntrinsics(camera_id='SYNTHETIC_NOT_A_D405',stream_id='synthetic_gray_960x720',
        optical_frame='synthetic_camera_optical',width=960,height=720,
        K=[[600.,0.,479.5],[0.,600.,359.5],[0.,0.,1.]],distortion_model='none',distortion=[],
        provenance='Synthetic pinhole intrinsics for software testing only')
    config=SessionConfig(mount=mount,data_origin='synthetic',base_frame='synthetic_base',end_frame='synthetic_flange',
                         clock_id='synthetic_clock',camera=camera,board=spec)
    session=CalibrationSession.create(directory,config)
    rng=np.random.default_rng(20260925)
    x=pose([6,-11,4],[.035,.012,.07]) if mount=='eye-in-hand' else pose([10,-20,15],[.6,.2,1.1])
    anchor=pose([2,3,-4],[.42,.08,.60]) if mount=='eye-in-hand' else pose([-8,4,3],[.03,.02,.09])
    expected=[];accepted=0
    center=np.array([spec.squares_x*spec.square_m/2,spec.squares_y*spec.square_m/2,0])
    for i in range(count):
        c=pose(rng.uniform([-28,-28,-15],[28,28,15]),[0,0,0])
        c[:3,3]=np.array([rng.uniform(-.025,.025),rng.uniform(-.02,.02),rng.uniform(.34,.46)])-c[:3,:3]@center
        g=anchor @ inverse(c) @ inverse(x) if mount=='eye-in-hand' else x @ c @ inverse(anchor)
        image=render_board(spec,camera,c)
        packet=CapturePacket(image=image,frame=FrameMetadata(timestamp_s=float(i+1),clock_id=config.clock_id,camera=camera),
            robot=RobotPose(timestamp_s=float(i+1),clock_id=config.clock_id,base_frame=config.base_frame,end_frame=config.end_frame,
                            stationary=True,T_base_end=g.tolist()))
        record=session.record(packet)
        accepted+=record['status']=='accepted'
        expected.append({'capture_id':record['id'],'T_camera_board':c.tolist(),'T_base_end':g.tolist(),'status':record['status']})
    save_json(Path(directory)/'synthetic_truth.json',{'data_origin':'synthetic','expected_transform':x.tolist(),'samples':expected})
    result=session.solve(Path(directory)/'result.json')
    accuracy=pose_errors(x,[np.asarray(result['candidate_transform'])])[0]
    report={'data_origin':'synthetic','mount':mount,'captures':count,'accepted_captures':accepted,
            'result_status':result['status'],'known_transform_error':accuracy,
            'heldout':result['heldout'],'note':'Tests rendered board detection + PnP + hand-eye direction; no physical camera/robot was used.'}
    save_json(Path(directory)/'synthetic_validation.json',report)
    return report
