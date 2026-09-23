"""Robot-only sampled feedback. No object state or grasp classifier is read here."""
from collections import deque
from pathlib import Path
import numpy as np


class RobotTelemetry:
    rate_hz = 100
    window_seconds = 12

    def __init__(self, model, saved_path=None, restored_time=None):
        self.names = [f"{arm}-joint{i}" for arm in ("l", "r") for i in range(1, 10)]
        joints = [model.joint(n).id for n in self.names]
        self.qidx = model.jnt_qposadr[joints]
        self.vidx = model.jnt_dofadr[joints]
        self.aidx = [model.actuator(n).id for n in self.names]
        self.rows = deque(maxlen=self.rate_hz * self.window_seconds + 1)
        self.physics_dt = float(model.opt.timestep)
        self.model = model
        if saved_path and Path(saved_path).exists() and restored_time is not None:
            with np.load(saved_path, allow_pickle=False) as saved:
                for row in saved["samples"]:
                    if restored_time - self.window_seconds <= row[0] <= restored_time + 1e-8:
                        self.rows.append(row.copy())

    def sample(self, data):
        if self.rows and data.time - self.rows[-1][0] < 1 / self.rate_hz - 1e-8:
            return
        gaps = []
        for arm in ("l", "r"):
            g8, g9 = data.geom(f"{arm}_pad8"), data.geom(f"{arm}_pad9")
            axis = data.site(f"{arm}_tcp").xmat.reshape(3, 3)[:, 1]
            # Inner-face separation along tool Y; pad geometry is robot geometry.
            thickness = sum(self.model.geom(f"{arm}_pad{i}").size[1] for i in (8, 9))
            gaps.append(abs(float((g9.xpos-g8.xpos) @ axis)) - thickness)
        row = np.r_[data.time, data.qpos[self.qidx], data.qvel[self.vidx],
                    data.qfrc_actuator[self.vidx], data.ctrl[self.aidx], gaps]
        if not np.isfinite(row).all():
            raise ValueError("Non-finite robot telemetry")
        self.rows.append(row)

    def save(self, path):
        np.savez_compressed(path, samples=np.array(self.rows).reshape(-1, 75),
                            joint_names=np.array(self.names))

    def summary(self, arm="l"):
        if arm not in ("l", "r"):
            raise ValueError("Unknown telemetry arm")
        if not self.rows:
            return {"available": False, "reason": "No sampled physics steps in this process"}
        a = np.array(self.rows)
        offset = 0 if arm == "l" else 9
        early = a[a[:, 0] <= a[0, 0] + .2 + 1e-8]
        late = a[a[:, 0] >= a[-1, 0] - .2 - 1e-8]
        clean = lambda v: np.round(v, 6).tolist()
        def stats(start, width=9):
            b = a[:, start:start+width]
            return {"start": clean(b[0]), "end": clean(b[-1]),
                    "min": clean(b.min(axis=0)), "max": clean(b.max(axis=0)),
                    "early_200ms_mean": clean(early[:, start:start+width].mean(axis=0)),
                    "late_200ms_mean": clean(late[:, start:start+width].mean(axis=0))}
        return {"available": True, "source": "simulated robot encoders and actuator outputs",
                "arm": arm, "joint_order": self.names[offset:offset+9],
                "sample_rate_hz": self.rate_hz, "sample_count": len(a),
                "start_simulation_s": float(a[0, 0]), "end_simulation_s": float(a[-1, 0]),
                "force_alignment_limit_s": self.physics_dt,
                "units": {"joints_1_to_7": ["rad", "rad/s", "N*m"],
                          "joints_8_and_9": ["m", "m/s", "N"], "pad_gap": "m"},
                "q": stats(1+offset), "dq": stats(19+offset),
                "actuator_generalized_force": stats(37+offset),
                "commanded_q_end": clean(a[-1, 55+offset:64+offset]),
                "pad_gap": stats(73+(arm == "r"), 1),
                "limits": "Actuator output is not isolated contact force. Gravity compensation is enabled. "
                          "Forces and derived geometry can lag post-integration q by one physics step. "
                          "Early/late means use up to 200 ms. No object identity, pose or ground-truth grasp label included."}
