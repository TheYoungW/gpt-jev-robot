"""Preserve the supplied URDF; add scene/cameras/actuators in generated MJCF."""
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import mujoco

ROOT = Path(__file__).resolve().parents[2]
OBJECTS = ("coral", "teal", "gold")
TABLE_Z = 0.62
TRAY = np.array([0.35, -0.035, 0.636])


def add(parent, tag, **attrs):
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})


def look_at(pos, target):
    z = np.asarray(pos) - np.asarray(target)
    z /= np.linalg.norm(z)
    x = np.cross([0, 0, 1], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return " ".join(map(str, np.r_[x, y]))


def build_scene(output: Path, urdf: Path | None = None) -> Path:
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    urdf = Path(urdf or ROOT / "assets/robot/models/yunyi_v1_0.urdf").resolve()
    tree = ET.parse(urdf)
    r = tree.getroot()
    for mesh in r.findall(".//mesh"):
        path = (urdf.parent / mesh.get("filename")).resolve()
        if not path.exists():
            path = ROOT / "assets/robot/meshes/bessica_M1.3" / path.name
        if not path.exists():
            raise FileNotFoundError(path)
        mesh.set("filename", str(path))
    extension = add(r, "mujoco")
    add(extension, "compiler", discardvisual="false", fusestatic="false", strippath="false", balanceinertia="true")
    temp = output.parent / "import.urdf"
    tree.write(temp)
    robot = mujoco.MjModel.from_xml_path(str(temp))
    mujoco.mj_saveLastXML(str(output), robot)
    temp.unlink()
    tree = ET.parse(output)
    r = tree.getroot()
    option = r.find("option")
    if option is None:
        option = add(r, "option")
    option.attrib.update(timestep="0.002", integrator="implicitfast", cone="elliptic", iterations="80")
    visual = add(r, "visual")
    add(visual, "global", offwidth="1280", offheight="960")
    add(visual, "quality", shadowsize="2048")
    add(visual, "headlight", ambient="0.2 0.2 0.2", diffuse="0.3 0.3 0.3", specular="0 0 0")
    asset = r.find("asset")
    add(asset, "texture", name="floor_tex", type="2d", builtin="checker", rgb1="0.18 0.22 0.27", rgb2="0.22 0.26 0.31", width="256", height="256")
    add(asset, "material", name="floor_mat", texture="floor_tex", texrepeat="3 3", reflectance="0.1")
    w = r.find("worldbody")
    for geom in w.findall(".//geom"):
        if geom.get("contype") != "0":
            geom.set("group", "3")
    add(w, "light", pos="0.3 -0.3 2.8", dir="0 0 -1", diffuse="0.55 0.55 0.55", specular="0 0 0", castshadow="true")
    add(w, "geom", name="floor", type="plane", size="3 3 .05", material="floor_mat")
    add(w, "geom", name="table", type="box", pos="0.51 0 .59", size=".33 .43 .03", rgba=".69 .53 .36 1", friction="1 .01 .001")
    for x in (.3, .81):
        for y in (-.37, .37):
            add(w, "geom", type="box", pos=f"{x} {y} .28", size=".025 .025 .28", rgba=".19 .23 .28 1")
    tray = add(w, "body", name="tray", pos=" ".join(map(str, TRAY)))
    add(tray, "geom", name="tray_floor", type="box", size=".145 .12 .008", rgba=".9 .94 .97 1", friction="1 .01 .001")
    for pos, size in ((".15 0 .025", ".008 .128 .025"), ("-.15 0 .025", ".008 .128 .025"), ("0 .125 .025", ".15 .008 .025"), ("0 -.125 .025", ".15 .008 .025")):
        add(tray, "geom", type="box", pos=pos, size=size, rgba=".82 .88 .94 1")
    specs = [(.375, .16, ".92 .28 .22 1"), (.34, .23, ".05 .61 .61 1"), (.27, .18, ".96 .67 .12 1")]
    for i, (name, (x, y, color)) in enumerate(zip(OBJECTS, specs)):
        b = add(w, "body", name=name, pos=f"{x} {y} .65")
        add(b, "freejoint", name=f"{name}_free")
        # Compound non-convex objects: physical contacts on every component.
        add(b, "geom", name=f"{name}_core", type="box", size=".022 .018 .018", mass=".035", rgba=color, friction="2 .02 .002", condim="4")
        if i == 0:
            add(b, "geom", name=f"{name}_lobe", type="ellipsoid", pos=".024 .006 0", size=".02 .014 .015", mass=".01", rgba=color, friction="2 .02 .002", condim="4")
        elif i == 1:
            add(b, "geom", name=f"{name}_lobe", type="box", pos="-.023 .011 -.002", size=".016 .013 .016", mass=".01", rgba=color, friction="2 .02 .002", condim="4")
        else:
            add(b, "geom", name=f"{name}_lobe", type="capsule", fromto="-.026 0 0 .027 0 0", size=".012", mass=".01", rgba=color, friction="2 .02 .002", condim="4")
    # Keep original tool0 frames. Mesh-derived simulation TCP fixes their CAD offset.
    for side in ("l", "r"):
        body = r.find(f".//body[@name='{side}-link7']")
        add(body, "site", name=f"{side}_tcp", pos="-.002 0 -.155", size=".004", rgba="0 1 0 .6")
        add(body, "camera", name=f"{side}_wrist", pos=".055 0 -.085", xyaxes="0 -1 0 1 0 0", fovy="72")
        add(body, "geom", type="box", pos=".055 0 -.075", size=".022 .019 .012", rgba=".09 .12 .16 1", contype="0", conaffinity="0", mass="0")
    pos = [0.80, 0, 1.55]
    add(w, "camera", name="center", pos=" ".join(map(str, pos)), xyaxes=look_at(pos, [.36, .06, .64]), fovy="58")
    pos = [1.6, -1.55, 1.5]
    add(w, "camera", name="overview", pos=" ".join(map(str, pos)), xyaxes=look_at(pos, [.25, 0, .72]), fovy="48")
    act = add(r, "actuator")
    contact = add(r, "contact")
    for side in ("l", "r"):
        add(contact, "exclude", body1=f"{side}-base-link", body2=f"{side}-link1")
        add(contact, "exclude", body1=f"{side}-link5", body2=f"{side}-link7")
    for j in r.findall(".//joint"):
        name = j.get("name", "")
        if "-joint" not in name:
            continue
        finger = name.endswith(("8", "9"))
        j.set("damping", "2" if finger else "4")
        j.set("armature", ".005" if finger else ".04")
        add(act, "position", name=name, joint=name, kp="350" if finger else "1600", kv="8" if finger else "70", ctrlrange=j.get("range"), forcerange="-15 15" if finger else "-100 100")
    for b in r.findall(".//body"):
        if "-link" in b.get("name", ""):
            b.set("gravcomp", "1")
    # Original meshes are convexified by MuJoCo; replace finger collision hulls
    # with slim pads at their CAD tips, retaining the visual meshes.
    for side in ("l", "r"):
        for index in (8, 9):
            b = r.find(f".//body[@name='{side}-link{index}']")
            for g in b.findall("geom"):
                g.set("contype", "0"); g.set("conaffinity", "0")
            # CAD finger geometry is offset relative to its joint frame.
            y = (.352 if index == 8 else .3625) * (1 if side == "l" else -1)
            add(b, "geom", name=f"{side}_pad{index}", type="box", pos=f".000383 {y} .026", size=".021 .005 .025", mass="0", rgba=".12 .15 .19 1", friction="3 .05 .005", condim="4", solref=".006 1")
    ET.indent(tree)
    tree.write(output)
    mujoco.MjModel.from_xml_path(str(output))
    return output
