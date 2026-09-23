"""Deterministic muscles, RGB-D observations and auditable physical feedback."""
import json
import os
import re
from pathlib import Path
import time
os.environ.setdefault("MUJOCO_GL", "egl")
import mujoco
import numpy as np
from scipy.spatial.transform import Rotation
from scipy.optimize import least_squares
from PIL import Image, ImageDraw
import imageio.v2 as imageio
from .scene import build_scene, OBJECTS, TABLE_Z, TRAY


class MotionError(RuntimeError):
    pass


class RobotSim:
    def __init__(self, run_dir="runs/session", reset=False, video=False, telemetry=False):
        self.path = Path(run_dir).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        scene = self.path / "scene.xml"
        if reset or not scene.exists():
            build_scene(scene)
        self.model = mujoco.MjModel.from_xml_path(str(scene))
        self.data = mujoco.MjData(self.model)
        self.ikdata = mujoco.MjData(self.model)
        self.renderer = None
        self.writer = None
        self.frame_count = 0
        self.steps = 0
        self.label = "Initial observation"
        self.render_option = mujoco.MjvOption()
        self.render_option.geomgroup[3:] = 0
        self.arm_q = {}
        self.arm_v = {}
        self.arm_ctrl = {}
        self.telemetry = None
        for side in ("l", "r"):
            js = [self.model.joint(f"{side}-joint{i}").id for i in range(1, 8)]
            self.arm_q[side] = self.model.jnt_qposadr[js]
            self.arm_v[side] = self.model.jnt_dofadr[js]
            self.arm_ctrl[side] = np.array([self.model.actuator(f"{side}-joint{i}").id for i in range(1, 8)])
        if telemetry:
            from .telemetry import RobotTelemetry
            restored_time = None
            if not reset and (self.path / "state.npz").exists():
                with np.load(self.path / "state.npz") as saved:
                    restored_time = float(saved["time"])
            self.telemetry = RobotTelemetry(self.model, self.path / "robot_telemetry.npz", restored_time)
        if not reset and (self.path / "state.npz").exists():
            a = np.load(self.path / "state.npz")
            self.data.qpos[:] = a["qpos"]; self.data.qvel[:] = a["qvel"]
            self.data.ctrl[:] = a["ctrl"]; self.data.time = float(a["time"])
        else:
            mujoco.mj_forward(self.model, self.data)
            # Initial poses are reset state only, never a manipulation shortcut.
            for side, pos in (("l", [.30, .20, .80]), ("r", [.30, -.25, .76])):
                q = self.solve_ik(side, pos, np.pi/2, multistart=True)
                self.data.qpos[self.arm_q[side]] = q
                self.data.ctrl[self.arm_ctrl[side]] = q
            self._finger_controls("l", False); self._finger_controls("r", False)
            for side in ("l", "r"):
                for i in (8, 9):
                    j = self.model.joint(f"{side}-joint{i}")
                    self.data.qpos[j.qposadr] = self.data.ctrl[self.model.actuator(j.name).id]
            mujoco.mj_forward(self.model, self.data)
            self.step(.8)
        mujoco.mj_forward(self.model, self.data)
        if video:
            self.writer = imageio.get_writer(str(self.path / "episode_4x.mp4"), fps=30, codec="libx264", quality=8, macro_block_size=1)
        self.save()

    def save(self):
        np.savez(self.path / "state.npz", qpos=self.data.qpos, qvel=self.data.qvel, ctrl=self.data.ctrl, time=self.data.time)
        if self.telemetry is not None:
            self.telemetry.save(self.path / "robot_telemetry.npz")

    def event(self, kind, **details):
        record = {"kind": kind, "simulation_time": round(self.data.time, 4), "wall_time": time.time(), **details}
        with (self.path / "events.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        return record

    def tcp(self, side="l"):
        return self.data.site(f"{side}_tcp").xpos.copy()

    def solve_ik(self, side, position, yaw=np.pi/2, multistart=False):
        position = np.asarray(position, dtype=float)
        target_R = Rotation.from_euler("z", yaw).as_matrix()
        qidx = self.arm_q[side]; vidx = self.arm_v[side]
        js = [self.model.joint(f"{side}-joint{i}").id for i in range(1, 8)]
        limits = self.model.jnt_range[js]
        self.ikdata.qpos[:] = self.data.qpos
        q0 = self.data.qpos[qidx].copy()
        seeds = [q0]
        if multistart:
            rng = np.random.default_rng(42)
            seeds += [rng.uniform(limits[:, 0] * .65, limits[:, 1] * .65) for _ in range(20)]
        best = (float("inf"), None)
        for seed in seeds:
            q = seed.copy()
            for _ in range(220):
                self.ikdata.qpos[qidx] = q
                mujoco.mj_forward(self.model, self.ikdata)
                s = self.ikdata.site(f"{side}_tcp")
                ep = position - s.xpos
                er = Rotation.from_matrix(target_R @ s.xmat.reshape(3, 3).T).as_rotvec()
                e = np.r_[ep, .25 * er]
                score = np.linalg.norm(e)
                if score < best[0]: best = (score, q.copy())
                if np.linalg.norm(ep) < .0003 and np.linalg.norm(er) < .006:
                    return q
                jp = np.zeros((3, self.model.nv)); jr = jp.copy()
                mujoco.mj_jacSite(self.model, self.ikdata, jp, jr, self.model.site(f"{side}_tcp").id)
                J = np.vstack([jp[:, vidx], .25 * jr[:, vidx]])
                pinv = J.T @ np.linalg.solve(J @ J.T + .00001 * np.eye(6), np.eye(6))
                center = limits.mean(axis=1)
                span = (limits[:, 1] - limits[:, 0]) / 2
                # Use the seventh DOF to keep a margin to joint limits.
                dq = pinv @ e + (np.eye(7) - pinv @ J) @ (.035 * (center-q) / span**2)
                q = np.clip(q + np.clip(dq, -.12, .12), limits[:, 0] + .002, limits[:, 1] - .002)
        if best[0] < .004:
            return best[1]
        # Bound-constrained refinement can slide along a joint limit where
        # clipped damped least squares otherwise stalls. Weak continuity cost
        # favors the current redundant configuration over an elbow flip.
        def residual(q):
            self.ikdata.qpos[qidx] = q
            mujoco.mj_forward(self.model, self.ikdata)
            site = self.ikdata.site(f"{side}_tcp")
            er = Rotation.from_matrix(target_R @ site.xmat.reshape(3, 3).T).as_rotvec()
            return np.r_[site.xpos-position, .25*er, .0001*(q-q0)]
        solution = least_squares(residual, best[1], bounds=(limits[:,0]+.0001, limits[:,1]-.0001), max_nfev=180, ftol=1e-10, xtol=1e-10, gtol=1e-10)
        if np.linalg.norm(residual(solution.x)[:6]) < .001:
            return solution.x
        raise MotionError(f"IK unreachable for {side}: {position.tolist()}, residual={best[0]:.4f}")

    def step(self, seconds):
        for _ in range(round(seconds / self.model.opt.timestep)):
            mujoco.mj_step(self.model, self.data)
            if self.telemetry is not None:
                self.telemetry.sample(self.data)
            self.steps += 1
            if not np.isfinite(self.data.qpos).all():
                raise MotionError("Non-finite simulation state")
            # 500 Hz / 67 steps per frame = 7.46 capture fps -> 30 fps = 4.02x.
            if self.writer and self.steps % 67 == 0:
                frame = Image.fromarray(self.render("overview"))
                draw = ImageDraw.Draw(frame)
                draw.rectangle((0, 0, frame.width, 48), fill=(19, 26, 36))
                draw.text((15, 8), f"YUNYI / Agent + Jev / 4.02x   sim {self.data.time:.1f}s", fill="white")
                draw.text((15, 28), self.label, fill=(114, 224, 203))
                self.writer.append_data(np.asarray(frame))
                self.frame_count += 1

    def plan_move(self, position, side="l", yaw=np.pi/2, duration=None):
        """Validate workspace and every IK waypoint without advancing physics."""
        p = np.asarray(position, dtype=float)
        if p.shape != (3,) or not np.isfinite(p).all() or not np.isfinite(yaw):
            raise MotionError("Expected finite XYZ meters and yaw radians")
        if side not in self.arm_q:
            raise MotionError("Unknown arm")
        if not (.26 <= p[0] <= .70 and -.39 <= p[1] <= .39 and .647 <= p[2] <= 1.02):
            raise MotionError("Target outside simulation TCP workspace")
        start = self.tcp(side)
        distance = np.linalg.norm(p - start)
        duration = max(1., distance / .075) if duration is None else float(duration)
        if not np.isfinite(duration) or duration < max(.3, distance / .15) or duration > 30:
            raise MotionError("Duration violates speed/time bounds")
        # Preflight every Cartesian waypoint before touching the live simulation.
        old_q = self.data.qpos.copy()
        targets = []
        try:
            for t in np.linspace(0, 1, max(10, int(duration * 25))):
                blend = t*t*(3 - 2*t)
                q = self.solve_ik(side, start + blend * (p - start), yaw)
                targets.append(q)
                self.data.qpos[self.arm_q[side]] = q
        finally:
            self.data.qpos[:] = old_q
            mujoco.mj_forward(self.model, self.data)
        return p, duration, targets

    def move(self, position, side="l", yaw=np.pi/2, duration=None):
        p, duration, targets = self.plan_move(position, side, yaw, duration)
        for q in targets:
            previous = self.data.ctrl[self.arm_ctrl[side]].copy()
            segment_time = max(duration / len(targets), float(np.max(np.abs(q-previous))) / .6)
            ticks = max(1, int(np.ceil(segment_time / self.model.opt.timestep)))
            for tick in range(1, ticks+1):
                self.data.ctrl[self.arm_ctrl[side]] = previous + (q-previous)*(tick/ticks)
                self.step(self.model.opt.timestep)
        self.step(.25)
        error = float(np.linalg.norm(self.tcp(side) - p))
        self.save()
        result = self.event("move", arm=side, target=p.tolist(), actual=self.tcp(side).tolist(), error_m=error)
        if error > .012:
            raise MotionError(f"Tracking error {error:.4f} m; stopped at current state")
        return result

    def nudge(self, direction, distance=.025, side="l"):
        axes = {"up": [0,0,1], "down": [0,0,-1], "forward": [1,0,0], "back": [-1,0,0], "left": [0,1,0], "right": [0,-1,0]}
        if direction not in axes or not 0 < distance <= .05:
            raise MotionError("Nudge must be a named direction, 0 < distance <= .05 m")
        return self.move(self.tcp(side) + distance * np.array(axes[direction]), side)

    def _finger_controls(self, side, closed):
        for i, value in ((8, -.044 if closed else 0), (9, .044 if closed else 0)):
            # Right URDF has reversed prismatic signs.
            aid = self.model.actuator(f"{side}-joint{i}").id
            lo, hi = self.model.actuator_ctrlrange[aid]
            self.data.ctrl[aid] = np.clip(value if side == "l" else -value, lo, hi)

    def gripper(self, closed, side="l"):
        if side not in self.arm_q: raise MotionError("Unknown arm")
        self._finger_controls(side, closed)
        self.step(.8); self.save()
        return self.event("gripper", arm=side, closed=bool(closed), contacts=self.contacts())

    def contacts(self):
        pairs = []
        for c in self.data.contact:
            names = [self.model.geom(int(i)).name for i in c.geom]
            if any("pad" in n for n in names):
                pairs.append(names)
        return pairs

    def render(self, camera="center", depth=False):
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, height=720, width=960)
        if depth: self.renderer.enable_depth_rendering()
        else: self.renderer.disable_depth_rendering()
        self.renderer.update_scene(self.data, camera=camera, scene_option=self.render_option)
        result = self.renderer.render().copy()
        self.renderer.disable_depth_rendering()
        return result

    def calibration(self, camera):
        cid = self.model.camera(camera).id
        f = 720 / (2 * np.tan(np.deg2rad(self.model.cam_fovy[cid]) / 2))
        # OpenCV convention: +X right, +Y down, +Z forward.
        R = self.data.cam_xmat[cid].reshape(3, 3) @ np.diag([1, -1, -1])
        T = np.eye(4); T[:3, :3] = R; T[:3, 3] = self.data.cam_xpos[cid]
        return {"width": 960, "height": 720, "K": [[f, 0, 479.5], [0, f, 359.5], [0, 0, 1]], "T_world_camera": T.tolist(), "depth": "optical-axis meters", "source": "MuJoCo exact simulated calibration; approximate physical camera mounts"}

    def observe(self, label="observation"):
        if not isinstance(label, str) or re.fullmatch(r"[\w-]{1,80}", label) is None:
            raise ValueError("Observation label must contain 1..80 letters, digits, underscores or hyphens")
        stamp = f"{self.data.time:09.3f}_{label}"
        folder = self.path / "observations" / stamp
        folder.mkdir(parents=True, exist_ok=True)
        cameras = {}
        for name in ("center", "l_wrist", "r_wrist", "overview"):
            Image.fromarray(self.render(name)).save(folder / f"{name}.png")
            np.save(folder / f"{name}_depth.npy", self.render(name, depth=True))
            cameras[name] = self.calibration(name)
        (folder / "calibration.json").write_text(json.dumps(cameras, indent=2))
        result = {"images": {n: str(folder / f"{n}.png") for n in cameras}, "calibration": str(folder / "calibration.json"), "tcp": {s:self.tcp(s).tolist() for s in ("l", "r")}, "contacts": self.contacts(), "simulation_time": self.data.time}
        self.event("observe", **result)
        return result

    def oracle(self):
        """Evaluation-only privileged state, never labelled camera perception."""
        objects = {}
        for name in OBJECTS:
            p = self.data.body(name).xpos
            bid = self.model.body(name).id
            velocity = np.zeros(6); mujoco.mj_objectVelocity(self.model, self.data, mujoco.mjtObj.mjOBJ_BODY, bid, velocity, 0)
            geoms = np.flatnonzero(self.model.geom_bodyid == bid)
            bounds = []
            for gid in geoms:
                R = self.data.geom_xmat[gid].reshape(3,3)
                size = self.model.geom_size[gid]
                kind = self.model.geom_type[gid]
                if kind == mujoco.mjtGeom.mjGEOM_BOX:
                    ext = np.abs(R) @ size
                elif kind == mujoco.mjtGeom.mjGEOM_ELLIPSOID:
                    ext = np.sqrt((R**2) @ (size**2))
                elif kind == mujoco.mjtGeom.mjGEOM_CAPSULE:
                    ext = size[0] + np.abs(R[:,2])*size[1]
                else:
                    ext = np.full(3, self.model.geom_rbound[gid])
                bounds.append((self.data.geom_xpos[gid]-ext, self.data.geom_xpos[gid]+ext))
            lo = np.min([b[0] for b in bounds], axis=0)
            hi = np.max([b[1] for b in bounds], axis=0)
            inside = bool(np.all(lo[:2] > TRAY[:2]-[.142,.117]) and np.all(hi[:2] < TRAY[:2]+[.142,.117]) and .65 < p[2] < .70)
            floor_gid = self.model.geom("tray_floor").id
            supported = any(floor_gid in c.geom and any(int(g) in geoms for g in c.geom) for c in self.data.contact)
            touching_gripper = any(any(n.startswith(name+"_") for n in pair) for pair in self.contacts())
            objects[name] = {"position": p.tolist(), "in_tray": inside, "supported_by_tray": bool(supported), "touching_gripper": touching_gripper, "speed": float(np.linalg.norm(velocity[3:])), "angular_speed": float(np.linalg.norm(velocity[:3])), "world_bounds": [lo.tolist(), hi.tolist()]}
        return {"source": "simulator_ground_truth_evaluation_only", "objects": objects, "success": all(o["in_tray"] and o["supported_by_tray"] and not o["touching_gripper"] and o["speed"] < .02 and o["angular_speed"] < .15 for o in objects.values())}

    def close(self):
        self.save()
        if self.writer:
            self.writer.close(); self.writer = None
        if self.renderer:
            self.renderer.close(); self.renderer = None
