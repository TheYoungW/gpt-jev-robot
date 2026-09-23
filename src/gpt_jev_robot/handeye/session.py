"""Append-only offline captures, driver adapter entry point and audit files."""
from pathlib import Path
import hashlib
import json
import time
import uuid
import cv2
import numpy as np
from .models import CalibrationError, SessionConfig, CapturePacket, fingerprint
from .detection import estimate_board
from .solver import solve_handeye, pose_errors


def save_json(path, value):
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False))


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CalibrationSession:
    def __init__(self, directory):
        self.path=Path(directory)
        self.config=SessionConfig.model_validate_json((self.path/'session.json').read_text())

    @classmethod
    def create(cls, directory, config:SessionConfig):
        path=Path(directory)
        if path.exists():raise CalibrationError('Use a new calibration session directory')
        path.mkdir(parents=True)
        save_json(path/'session.json',config.model_dump())
        return cls(path)

    def capture_from(self, source):
        return self.record(source.read_stationary_packet())

    def record(self, packet:CapturePacket):
        c=self.config;f=packet.frame;r=packet.robot
        identifier=uuid.uuid4().hex[:16];dest=self.path/'captures'/identifier
        dest.mkdir(parents=True)
        metadata={'id':identifier,'recorded_wall_time':time.time(),'frame':f.model_dump(),
                  'robot':r.model_dump(),'configuration_fingerprint':fingerprint(c),'data_origin':c.data_origin}
        try:
            if not cv2.imwrite(str(dest/'image.png'),packet.image):raise CalibrationError('Could not save source image')
            metadata['image_sha256']=file_hash(dest/'image.png')
            if fingerprint(f.camera)!=fingerprint(c.camera):raise CalibrationError('Camera serial, stream or intrinsics changed; start another session')
            if f.clock_id!=c.clock_id or r.clock_id!=c.clock_id:raise CalibrationError('Camera and robot timestamps must use the same declared clock domain')
            if abs(f.timestamp_s-r.timestamp_s)>c.max_timestamp_skew_s:raise CalibrationError('Camera/robot timestamps are too far apart')
            if r.base_frame!=c.base_frame or r.end_frame!=c.end_frame:raise CalibrationError('Robot base/end frame changed')
            if not r.stationary:raise CalibrationError('Capture only after robot and board settle')
            if packet.depth_m is not None:
                if f.depth_optical_frame!=c.camera.optical_frame or f.depth_timestamp_s is None:
                    raise CalibrationError('Depth must be explicitly aligned to the selected image optical frame')
                if abs(f.depth_timestamp_s-f.timestamp_s)>c.max_timestamp_skew_s:raise CalibrationError('Depth/image timestamps are too far apart')
                np.save(dest/'depth_m.npy',packet.depth_m,allow_pickle=False)
                metadata['depth_sha256']=file_hash(dest/'depth_m.npy')
            detection,overlay=estimate_board(packet.image,c.board,c.camera,min_corners=c.min_corners,
                max_rms_px=c.max_reprojection_rms_px,min_coverage=c.min_image_coverage,depth_m=packet.depth_m)
            # Repeating one stationary pose must not masquerade as motion diversity.
            for previous in self.accepted_samples():
                error=pose_errors(np.asarray(previous['T_base_end']),[np.asarray(r.T_base_end)])[0]
                if error['translation_m']<.002 and error['rotation_deg']<1.:
                    raise CalibrationError('Near-duplicate robot pose: vary position or orientation before capturing again')
            cv2.imwrite(str(dest/'detected.png'),overlay)
            metadata.update(status='accepted',detection=detection)
        except (ValueError,cv2.error) as exc:
            metadata.update(status='rejected',reason=str(exc))
        save_json(dest/'capture.json',metadata)
        # Both successes and failures are preserved; never silently drop captures.
        with (self.path/'captures.jsonl').open('a') as log:
            log.write(json.dumps({'id':identifier,'status':metadata['status'],'capture_sha256':file_hash(dest/'capture.json')})+'\n')
        return metadata

    def accepted_samples(self):
        result=[]
        log=self.path/'captures.jsonl'
        if not log.exists():return result
        seen=set()
        for line in log.read_text().splitlines():
            entry=json.loads(line);identifier=entry['id']
            if identifier in seen:raise CalibrationError('Duplicate capture in the audit log')
            seen.add(identifier)
            folder=self.path/'captures'/identifier
            if file_hash(folder/'capture.json')!=entry['capture_sha256']:raise CalibrationError('Capture metadata changed after recording')
            data=json.loads((folder/'capture.json').read_text())
            if data['status']!='accepted':continue
            if data['configuration_fingerprint']!=fingerprint(self.config):raise CalibrationError('Session configuration changed after capture')
            if file_hash(folder/'image.png')!=data['image_sha256']:raise CalibrationError('Image changed after capture')
            if 'depth_sha256' in data and file_hash(folder/'depth_m.npy')!=data['depth_sha256']:raise CalibrationError('Depth changed after capture')
            result.append({'id':identifier,'T_base_end':data['robot']['T_base_end'],
                           'T_camera_board':data['detection']['T_camera_board'],
                           'capture_sha256':entry['capture_sha256']})
        return result

    def solve(self, output, **kwargs):
        output=Path(output)
        if output.exists():raise CalibrationError('Refusing to overwrite an existing calibration result')
        samples=self.accepted_samples()
        result=solve_handeye(samples,self.config.mount,**kwargs)
        result.update(data_origin=self.config.data_origin,camera=self.config.camera.model_dump(),
                      base_frame=self.config.base_frame,end_frame=self.config.end_frame,
                      board=self.config.board.model_dump(),configuration_fingerprint=fingerprint(self.config),
                      sample_ids=[s['id'] for s in samples],sample_metadata_hashes=[s['capture_sha256'] for s in samples],
                      solved_wall_time=time.time(),opencv_version=cv2.__version__)
        output.parent.mkdir(parents=True,exist_ok=True);save_json(output,result)
        return result
