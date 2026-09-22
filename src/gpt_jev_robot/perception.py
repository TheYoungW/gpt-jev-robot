"""Small, explicit RGB-D baseline. Never reads object body poses or segmentation IDs."""
import numpy as np


def unproject(pixels, depths, calibration):
    K = np.asarray(calibration["K"])
    T = np.asarray(calibration["T_world_camera"])
    uv = np.c_[pixels, np.ones(len(pixels))]
    points = (np.linalg.inv(K) @ uv.T).T * np.asarray(depths)[:, None]
    return points @ T[:3, :3].T + T[:3, 3]


def detect_objects(sim):
    rgb = sim.render("center").astype(float) / 255
    depth = sim.render("center", depth=True)
    r, g, b = rgb.transpose(2, 0, 1)
    masks = {"coral": (r > .40) & (r > 1.55*g) & (r > 1.6*b), "teal": (g > .25) & (b > .25) & (g > 1.55*r) & (b > 1.55*r), "gold": (r > .5) & (g > .34) & (b < .30) & (r > 1.1*g)}
    result = {}
    for name, mask in masks.items():
        v, u = np.where(mask & np.isfinite(depth) & (depth > 0) & (depth < 2))
        if len(u) < 30:
            result[name] = {"visible": False, "pixels": len(u)}
            continue
        xyz = unproject(np.c_[u, v], depth[v, u], sim.calibration("center"))
        xyz = xyz[(xyz[:, 2] > .625) & (xyz[:, 2] < 1.1)]
        if len(xyz) < 30:
            result[name] = {"visible": False, "pixels": len(xyz)}
            continue
        lo, hi = np.percentile(xyz, [4, 96], axis=0)
        result[name] = {"visible": True, "pixels": len(xyz), "surface_center": ((lo+hi)/2).tolist(), "bounds": [lo.tolist(), hi.tolist()], "source": "color_thresholds_and_camera_depth", "assumptions": "known distinct object colors; approximate grasp height from known 36 mm core"}
    return result
