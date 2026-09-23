"""Offline calibration CLI; no camera driver or robot motion connection."""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from .models import BoardSpec, CameraIntrinsics, FrameMetadata, RobotPose, SessionConfig, CapturePacket, CalibrationError
from .board import write_board
from .session import CalibrationSession, save_json
from .detection import estimate_board
from .intrinsics import calibrate_intrinsics
from .synthetic import generate_demo


def load(cls,path):return cls.model_validate_json(Path(path).read_text())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('board',help='Generate the matching A4 PDF, board JSON and detector fixture')
    p.add_argument('--output',type=Path,required=True)
    p=commands.add_parser('init',help='Create an empty session using active-stream camera intrinsics')
    p.add_argument('--session',type=Path,required=True);p.add_argument('--camera',type=Path,required=True)
    p.add_argument('--board',type=Path,required=True);p.add_argument('--mount',choices=['eye-in-hand','eye-to-hand'],required=True)
    p.add_argument('--base-frame',required=True);p.add_argument('--end-frame',required=True);p.add_argument('--clock-id',required=True)
    p.add_argument('--data-origin',choices=['measured','synthetic'],required=True)
    p=commands.add_parser('add',help='Validate and append one saved synchronized capture')
    p.add_argument('--session',type=Path,required=True);p.add_argument('--image',type=Path,required=True)
    p.add_argument('--frame',type=Path,required=True);p.add_argument('--pose',type=Path,required=True)
    p.add_argument('--depth-m',type=Path)
    p=commands.add_parser('inspect',help='Detect a board and save PnP diagnostics without a robot pose')
    for name in ('image','camera','board','output'):p.add_argument('--'+name,type=Path,required=True)
    p=commands.add_parser('solve',help='Fit on training captures and check untouched held-out poses')
    p.add_argument('--session',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--method',choices=['park','tsai','horaud'],default='park')
    p.add_argument('--max-translation-mm',type=float,default=5.);p.add_argument('--max-rotation-deg',type=float,default=1.)
    p=commands.add_parser('intrinsics',help='Optional intrinsic estimation from images; does not change D405 factory settings')
    p.add_argument('--images',type=Path,nargs='+',required=True);p.add_argument('--board',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    for name in ('camera-id','stream-id','optical-frame'):p.add_argument('--'+name,required=True)
    p.add_argument('--data-origin',choices=['measured','synthetic'],required=True)
    p=commands.add_parser('demo',help='Render a synthetic end-to-end test; no hardware used')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--mount',choices=['eye-in-hand','eye-to-hand'],default='eye-in-hand')
    args=parser.parse_args(argv)
    try:
        if args.command=='board':print(write_board(args.output));return 0
        if args.command=='init':
            config=SessionConfig(mount=args.mount,data_origin=args.data_origin,base_frame=args.base_frame,
                end_frame=args.end_frame,clock_id=args.clock_id,board=load(BoardSpec,args.board),camera=load(CameraIntrinsics,args.camera))
            CalibrationSession.create(args.session,config);print(args.session);return 0
        if args.command=='add':
            image=cv2.imread(str(args.image),cv2.IMREAD_UNCHANGED)
            if image is None:raise CalibrationError('Unreadable image')
            depth=np.load(args.depth_m,allow_pickle=False) if args.depth_m else None
            result=CalibrationSession(args.session).record(CapturePacket(image,load(FrameMetadata,args.frame),load(RobotPose,args.pose),depth))
            print(json.dumps({'id':result['id'],'status':result['status'],'reason':result.get('reason')},ensure_ascii=False))
            return 0 if result['status']=='accepted' else 2
        if args.command=='inspect':
            if args.output.suffix.lower()!='.json':raise CalibrationError('Inspection --output must end in .json')
            overlay=args.output.with_suffix('.png')
            if args.output.exists() or overlay.exists():raise CalibrationError('Inspection output already exists')
            image=cv2.imread(str(args.image),cv2.IMREAD_UNCHANGED)
            if image is None:raise CalibrationError('Unreadable image')
            result,picture=estimate_board(image,load(BoardSpec,args.board),load(CameraIntrinsics,args.camera))
            args.output.parent.mkdir(parents=True,exist_ok=True)
            save_json(args.output,result);cv2.imwrite(str(overlay),picture)
        elif args.command=='solve':
            result=CalibrationSession(args.session).solve(args.output,method=args.method,
                max_translation_m=args.max_translation_mm/1000,max_rotation_deg=args.max_rotation_deg)
        elif args.command=='intrinsics':
            result=calibrate_intrinsics(args.images,load(BoardSpec,args.board),camera_id=args.camera_id,
                stream_id=args.stream_id,optical_frame=args.optical_frame,output=args.output,data_origin=args.data_origin)
        else:result=generate_demo(args.output,args.mount)
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 2 if result.get('status',result.get('result_status'))=='failed_validation' else 0
    except (ValueError,OSError,cv2.error) as exc:
        parser.exit(2,f'Calibration error: {exc}\n')


if __name__=='__main__':raise SystemExit(main())
