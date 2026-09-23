import copy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import numpy as np
import pytest
cv2=pytest.importorskip('cv2')
pytest.importorskip('reportlab')
from gpt_jev_robot.handeye.board import write_board, make_board
from gpt_jev_robot.handeye.models import (BoardSpec, CameraIntrinsics, FrameMetadata, RobotPose,
    SessionConfig, CapturePacket, CalibrationError, inverse, transform)
from gpt_jev_robot.handeye.session import CalibrationSession
from gpt_jev_robot.handeye.solver import solve_handeye, pose_errors
from gpt_jev_robot.handeye.synthetic import generate_demo, pose, render_board
from gpt_jev_robot.handeye.intrinsics import calibrate_intrinsics


@pytest.fixture
def packet():
    camera=CameraIntrinsics(camera_id='unit_test_camera',stream_id='gray',optical_frame='camera_optical',
        width=960,height=720,K=[[600.,0.,479.5],[0.,600.,359.5],[0.,0.,1.]],
        distortion_model='none',distortion=[],provenance='synthetic unit test')
    board_pose=pose([12,-18,5],[-.09,-.12,.45])
    image=render_board(BoardSpec(),camera,board_pose)
    return CapturePacket(image,FrameMetadata(timestamp_s=1.,clock_id='test_clock',camera=camera),
        RobotPose(timestamp_s=1.,clock_id='test_clock',base_frame='base',end_frame='flange',stationary=True,T_base_end=np.eye(4).tolist()))


def new_session(path, packet):
    return CalibrationSession.create(path,SessionConfig(mount='eye-in-hand',data_origin='synthetic',
        base_frame='base',end_frame='flange',clock_id='test_clock',board=BoardSpec(),camera=packet.frame.camera))


def test_a4_pdf_physical_scale_and_detector_match(tmp_path):
    pypdf=pytest.importorskip('pypdf')
    spec=BoardSpec();pdf=write_board(tmp_path/'board',spec)
    page=pypdf.PdfReader(pdf).pages[0]
    assert len(pypdf.PdfReader(pdf).pages)==1
    np.testing.assert_allclose([float(page.mediabox.width),float(page.mediabox.height)],np.array([210,297])*72/25.4,atol=1e-3)
    if not shutil.which('pdftoppm'):pytest.skip('PDF rasterization check requires poppler')
    subprocess.run(['pdftoppm','-png','-r','144','-singlefile',str(pdf),str(tmp_path/'printed')],check=True)
    image=cv2.imread(str(tmp_path/'printed.png'),cv2.IMREAD_GRAYSCALE)
    board=make_board(spec)
    corners,ids,_,marker_ids=cv2.aruco.CharucoDetector(board).detectBoard(image)
    assert len(ids)==54 and set(marker_ids.reshape(-1))==set(range(35))
    obj=board.getChessboardCorners()[ids.reshape(-1),:2]
    affine=np.linalg.lstsq(np.c_[obj,np.ones(len(obj))],corners.reshape(-1,2),rcond=None)[0]
    # Independent PDF rasterization must preserve the specified metric square scale.
    np.testing.assert_allclose(np.diag(affine[:2]),np.full(2,144/25.4*1000),rtol=.002)
    assert abs(affine[0,1])<2 and abs(affine[1,0])<2
    with pytest.raises(CalibrationError):write_board(tmp_path/'board')


@pytest.mark.parametrize('model',['inverse_brown_conrady','modified_brown_conrady','kannala_brandt4'])
def test_realsense_distortion_is_not_silently_reinterpreted(model):
    with pytest.raises(CalibrationError,match='Rectify'):
        CameraIntrinsics.from_realsense_intrinsics(camera_id='D405',stream_id='ir1',optical_frame='ir_optical',
            width=640,height=480,fx=400,fy=400,ppx=320,ppy=240,model=model,coeffs=[0]*5)


def test_transform_rejects_reflection_and_wrong_units(packet):
    reflected=np.eye(4);reflected[0,0]=-1
    with pytest.raises(CalibrationError):transform(reflected)
    with pytest.raises(ValueError):RobotPose.model_validate({**packet.robot.model_dump(),'translation_unit':'mm'})


@pytest.mark.parametrize('change,reason',[
    ('clock','clock'),('late','timestamps'),('moving','settle'),('frame','frame changed'),('profile','intrinsics changed'),('resolution','resolution')])
def test_bad_synchronized_capture_is_preserved_and_rejected(tmp_path,packet,change,reason):
    session=new_session(tmp_path/'session',packet)
    if change=='clock':packet=replace(packet,frame=packet.frame.model_copy(update={'clock_id':'camera_hardware_clock'}))
    if change=='late':packet=replace(packet,robot=packet.robot.model_copy(update={'timestamp_s':1.2}))
    if change=='moving':packet=replace(packet,robot=packet.robot.model_copy(update={'stationary':False}))
    if change=='frame':packet=replace(packet,robot=packet.robot.model_copy(update={'end_frame':'different_tcp'}))
    if change=='profile':packet=replace(packet,frame=packet.frame.model_copy(update={'camera':packet.frame.camera.model_copy(update={'stream_id':'another_stream'})}))
    if change=='resolution':packet=replace(packet,image=packet.image[::2,::2].copy())
    result=session.record(packet)
    assert result['status']=='rejected' and reason in result['reason']
    assert (session.path/'captures'/result['id']/'image.png').exists()
    assert session.accepted_samples()==[]


def test_duplicate_capture_and_changed_image_cannot_enter_solver(tmp_path,packet):
    session=new_session(tmp_path/'session',packet)
    r=session.record(packet);assert r['status']=='accepted'
    duplicate=session.record(packet)
    assert duplicate['status']=='rejected' and 'duplicate' in duplicate['reason']
    (session.path/'captures'/r['id']/'image.png').write_bytes(b'changed')
    with pytest.raises(CalibrationError,match='Image changed'):session.accepted_samples()


def test_depth_requires_aligned_meter_values(tmp_path,packet):
    session=new_session(tmp_path/'session',packet)
    r=session.record(replace(packet,depth_m=np.ones(packet.image.shape,dtype=np.float32)*.4))
    assert r['status']=='rejected' and 'aligned' in r['reason']
    frame=packet.frame.model_copy(update={'depth_timestamp_s':1.,'depth_optical_frame':packet.frame.camera.optical_frame})
    r=session.record(replace(packet,frame=frame,depth_m=np.ones(packet.image.shape,dtype=np.uint16)*400))
    assert r['status']=='rejected' and 'floating-point' in r['reason']


def exact_samples(mount):
    rng=np.random.default_rng(12);x=pose([10,-14,6],[.045,-.02,.07]);anchor=pose([-3,9,2],[.45,.1,.8]);out=[]
    for i in range(24):
        g=pose(rng.uniform(-40,40,3),rng.uniform(-.2,.2,3))
        c=inverse(x)@inverse(g)@anchor if mount=='eye-in-hand' else inverse(x)@g@anchor
        out.append({'T_base_end':g.tolist(),'T_camera_board':c.tolist()})
    return out,x


@pytest.mark.parametrize('mount',['eye-in-hand','eye-to-hand'])
def test_frame_direction_and_heldout_validation(mount):
    samples,expected=exact_samples(mount)
    result=solve_handeye(samples,mount)
    np.testing.assert_allclose(result['accepted_transform'],expected,atol=1e-9)
    assert len(result['heldout_indices'])==6 and not result['refitted_on_holdout']
    corrupted=copy.deepcopy(samples)
    corrupted[3]['T_camera_board'][0][3]+=.04  # only corrupt an untouched validation frame
    result=solve_handeye(corrupted,mount)
    assert result['status']=='failed_validation' and result['accepted_transform'] is None
    np.testing.assert_allclose(result['candidate_transform'],expected,atol=1e-9)


@pytest.mark.parametrize('rotation',[False,True])
def test_translation_only_or_one_axis_motion_is_rejected(rotation):
    samples=[{'T_base_end':pose([0,0,i*4 if rotation else 0],[i*.01,0,0]).tolist(),
              'T_camera_board':pose([0,0,0],[0,0,.4]).tolist()} for i in range(12)]
    with pytest.raises(CalibrationError,match='rotational excitation'):solve_handeye(samples,'eye-in-hand')


@pytest.fixture(scope='module')
def demo_runs(tmp_path_factory):
    root=tmp_path_factory.mktemp('handeye_demos');out={}
    for mount in ('eye-in-hand','eye-to-hand'):
        folder=root/mount;report=generate_demo(folder,mount)
        out[mount]=(folder,report)
    return out


@pytest.mark.parametrize('mount',['eye-in-hand','eye-to-hand'])
def test_rendered_images_recover_known_extrinsics(demo_runs,mount):
    _,r=demo_runs[mount]
    assert r['accepted_captures']==24 and r['result_status']=='passed_validation'
    assert r['known_transform_error']['translation_m']<.002
    assert r['known_transform_error']['rotation_deg']<.5


def test_optional_intrinsics_on_varied_rendered_images(tmp_path,demo_runs):
    folder,_=demo_runs['eye-in-hand']
    report=calibrate_intrinsics(sorted(folder.glob('captures/*/image.png')),BoardSpec(),camera_id='synthetic',
        stream_id='gray',optical_frame='optical',output=tmp_path/'camera.json',data_origin='synthetic')
    assert report['status']=='passed_validation'
    k=np.array(report['candidate_camera']['K'])
    np.testing.assert_allclose([k[0,0],k[1,1]],[600,600],rtol=.02)
