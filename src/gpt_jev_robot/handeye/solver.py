"""Frame-explicit hand-eye solution with an untouched validation subset."""
import cv2
import numpy as np
from scipy.spatial.transform import Rotation
from .models import CalibrationError, inverse, transform


def mean_transform(values):
    a=np.array(values)
    result=np.eye(4)
    result[:3,:3]=Rotation.from_matrix(a[:,:3,:3]).mean().as_matrix()
    result[:3,3]=a[:,:3,3].mean(axis=0)
    return result


def pose_errors(reference, values):
    out=[]
    for value in values:
        delta=inverse(reference) @ value
        out.append({'translation_m':float(np.linalg.norm(delta[:3,3])),
                    'rotation_deg':float(np.rad2deg(np.linalg.norm(Rotation.from_matrix(delta[:3,:3]).as_rotvec())))})
    return out


def motion_diversity(poses):
    relative=[inverse(poses[i]) @ poses[j] for i in range(len(poses)) for j in range(i+1,len(poses))]
    vectors=np.array([Rotation.from_matrix(t[:3,:3]).as_rotvec() for t in relative])
    singular=np.linalg.svd(vectors,compute_uv=False)
    maximum=float(np.rad2deg(np.linalg.norm(vectors,axis=1).max()))
    ratio=float(singular[1]/singular[0]) if singular[0]>1e-10 else 0.
    if maximum<15 or ratio<.1:
        raise CalibrationError('Insufficient rotational excitation: use different nonparallel rotation axes '
                               'and at least 15 degrees total relative rotation, not translations alone')
    return {'maximum_relative_rotation_deg':maximum,'second_to_first_rotation_singular_ratio':ratio,
            'rotation_singular_values':singular.tolist()}


def solve_handeye(samples, mount, *, method='park', max_translation_m=.005, max_rotation_deg=1.):
    if mount not in ('eye-in-hand','eye-to-hand'):raise CalibrationError('Unknown camera mount')
    if len(samples)<12:raise CalibrationError('At least 12 accepted synchronized image/pose pairs are required; prefer 20–30')
    if max_translation_m<=0 or max_rotation_deg<=0 or not np.isfinite([max_translation_m,max_rotation_deg]).all():
        raise CalibrationError('Validation thresholds must be finite and positive')
    poses=[transform(s['T_base_end']) for s in samples]
    boards=[transform(s['T_camera_board']) for s in samples]
    # Fixed, order-based split: no selecting an estimator on these held-out poses.
    train=[i for i in range(len(samples)) if i%4!=3]
    holdout=[i for i in range(len(samples)) if i%4==3]
    excitation=motion_diversity([poses[i] for i in train])
    # Eye-to-hand: inv(T_base_end) plays the role of OpenCV's first input,
    # so the solver result is T_base_camera, not T_end_camera.
    inputs=poses if mount=='eye-in-hand' else [inverse(t) for t in poses]
    algorithms={'park':cv2.CALIB_HAND_EYE_PARK,'tsai':cv2.CALIB_HAND_EYE_TSAI,'horaud':cv2.CALIB_HAND_EYE_HORAUD}
    if method not in algorithms:raise CalibrationError('Unknown hand-eye method')
    try:
        rotation,translation=cv2.calibrateHandEye([inputs[i][:3,:3] for i in train],
            [inputs[i][:3,3] for i in train],[boards[i][:3,:3] for i in train],
            [boards[i][:3,3] for i in train],method=algorithms[method])
    except cv2.error as exc:raise CalibrationError('Hand-eye solver failed; inspect motion diversity and frames') from exc
    x=np.eye(4);x[:3,:3]=rotation;x[:3,3]=translation.reshape(3);transform(x)
    invariant=[inputs[i] @ x @ boards[i] for i in range(len(samples))]
    anchor=mean_transform([invariant[i] for i in train])
    errors=pose_errors(anchor,invariant)
    def metrics(indices):
        t=np.array([errors[i]['translation_m'] for i in indices]);r=np.array([errors[i]['rotation_deg'] for i in indices])
        return {'count':len(indices),'translation_rms_m':float(np.sqrt(np.mean(t*t))),
                'translation_max_m':float(t.max()),'rotation_rms_deg':float(np.sqrt(np.mean(r*r))),
                'rotation_max_deg':float(r.max())}
    training,validation=metrics(train),metrics(holdout)
    passed=all(m['translation_max_m']<=max_translation_m and m['rotation_max_deg']<=max_rotation_deg for m in (training,validation))
    name='T_end_camera' if mount=='eye-in-hand' else 'T_base_camera'
    return {'status':'passed_validation' if passed else 'failed_validation','mount':mount,'method':method,
            'transform_name':name,'candidate_transform':x.tolist(),'accepted_transform':x.tolist() if passed else None,
            'candidate_inverse_transform':inverse(x).tolist(),'translation_unit':'m',
            'quaternion_xyzw':Rotation.from_matrix(x[:3,:3]).as_quat().tolist(),
            'matrix_convention':'T_parent_child maps child-frame points into parent frame; column vectors',
            'invariant_name':'T_base_board' if mount=='eye-in-hand' else 'T_end_board',
            'invariant_training_mean':anchor.tolist(),'motion_diversity':excitation,
            'training_indices':train,'heldout_indices':holdout,'refitted_on_holdout':False,
            'training':training,'heldout':validation,'per_sample_errors':errors,
            'thresholds':{'maximum_translation_m':max_translation_m,'maximum_rotation_deg':max_rotation_deg},
            'limits':'Validation tests frame consistency on held-out captures, not absolute robot accuracy. '
                     'Printed scale, robot kinematics, rigid mounting and intrinsics still require independent checks.'}
