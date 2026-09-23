# D405 手眼标定工具与 A4 打印板

已提供离线 Python 标定工具、数据采集接口、A4 ChArUco PDF，以及两种相机安装方式的合成验证。当前没有连接 D405 或真实机器人；驱动层由后续接入。标定程序不使能、不移动机器人，也不写 D405 工厂参数。

## 先打印这一份

[下载 A4 标定板 PDF](../calibration/boards/d405_A4/d405_charuco_A4.pdf)。与之匹配的参数是 [board.json](../calibration/boards/d405_A4/board.json)。

| 项目 | 数值 |
| --- | --- |
| 纸张 | A4，210 × 297 mm，纵向，单页 |
| 图案 | ChArUco，7 × 10 个方格，DICT_4X4_50 |
| 方格边长 | 25.00 mm |
| ArUco 标记边长 | 18.00 mm，包括黑色边框 |
| 图案外尺寸 | 175 × 250 mm，不含纸边 |
| 标记编号 | 0–34 |
| ChArUco 内角点数 | 54 |
| 校验尺 | 横向、纵向各 100.00 mm |

打印时选择 **A4、实际大小 / 100%**，关闭“适合页面”“缩放到可打印区域”。打印后量两个 100 mm 校验尺，并检查横纵方格都是 25 mm。若缩放或横纵比例不一致，应修正打印设置再打印；不要把未测量的纸面尺寸当成真实尺度。

使用哑光白纸，平整粘贴在硬板上，避免翘曲和反光。不要裁掉棋盘外的白边。代码使用板内坐标，不把 A4 纸边当成板原点；具体角点坐标与图案哈希保存在 [board_manifest.json](../calibration/boards/d405_A4/board_manifest.json)。

D405 官方理想工作范围为 7–50 cm。对于这张 250 mm 高的图案，可以先从约 30–45 cm 取景，再按实际图像调整，保证板清晰、不过曝且尽量完整入镜；这是取景建议，不是精度保证。[D405 官方规格](https://www.realsenseai.com/products/stereo-depth-camera-d405/)

## 两种安装方式

| 模式 | 相机与标定板如何固定 | 输入机器人位姿 | 输出 |
| --- | --- | --- | --- |
| `eye-in-hand` | 相机固定在末端；板相对机器人基座保持不动 | `T_base_end` | `T_end_camera` |
| `eye-to-hand` | 相机相对基座固定；板刚性固定在末端上 | `T_base_end` | `T_base_camera` |

左右腕部相机、中央固定相机分别建立会话。固定相机模式不能让人手持板随意移动而仍使用机器臂位姿；板必须刚性随末端移动。腕部模式不能一边采样一边搬动板。

本项目约定：`T_parent_child` 把 child 坐标中的列向量变换到 parent 坐标，平移单位 **米**。例如 `p_base = T_base_end @ p_end`。相机坐标必须是选定图像流的 optical frame，通常 X 向右、Y 向下、Z 向前；不要把外壳坐标或 ROS camera_link 当成 optical frame。

`end_frame` 可以是法兰或已经定义好的 TCP，但整组采集必须使用同一个、明确命名的坐标系。输入需要实测关节反馈经 FK 得到的末端位姿，不能使用尚未到达的命令目标。工具不改变原 URDF 的 tool0，也不自动把本项目 MuJoCo TCP 当成实机 TCP。

公式核对：腕部模式 `T_base_end × T_end_camera × T_camera_board` 应保持固定；固定相机模式 `inverse(T_base_end) × T_base_camera × T_camera_board` 应保持固定。[OpenCV 手眼标定接口](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html)

## 安装与入口

在仓库根目录使用项目 `.venv`，无需安装 RealSense SDK 或机器人 SDK：

```bash
source .venv/bin/activate
python -m pip install -e '.[calibration]'
jev-calibrate --help
```

如果终端加载了 ROS，使用 `env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv/bin/python -m gpt_jev_robot.handeye.cli ...` 替代 `jev-calibrate ...`。不要改其他 Conda 环境。

主要入口：

- `board`：生成匹配的 PDF、图案 PNG 和参数文件。
- `inspect`：只检查一张图片的角点、板位姿和重投影残差，无需机器人位姿。
- `init` / `add` / `solve`：建立会话、追加图像与位姿、求解并做留出验证。
- `intrinsics`：可选的图像流内参估计，不是 D405 深度/双目工厂标定。
- `demo`：生成明确标注为 synthetic 的图像与机器人位姿，验证软件流程。

## 驱动稍后需要提供什么

[驱动接入模板](../examples/handeye/driver_adapter_template.py)中有 `make_packet()`、`camera_profile_from_sdk()` 和 `YourDriverSource.read_stationary_packet()`。具体契约在 [models.py](../src/gpt_jev_robot/handeye/models.py)。

每个 `CapturePacket` 包含：

1. `image`：未镜像、未任意裁剪缩放的 uint8 灰度或 BGR 图像。RGB 输入需要由驱动先转成 BGR。
2. `frame.camera`：相机序列号、图像流标识、optical frame、当前分辨率、K、畸变模型及系数。使用生成这张图片的实际 stream profile，不硬编码“D405 通用内参”。
3. `frame.timestamp_s` 与 `robot.timestamp_s`：映射到同一时钟域的曝光时间和位姿时间，单位秒。不能只给两个不同设备时钟相同的名字；时钟映射由驱动完成。默认允许差值不超过 30 ms。
4. `robot.T_base_end`：4×4 实测变换，米制平移；`base_frame`、`end_frame` 在会话中固定。
5. `robot.stationary`：驱动根据实际反馈确认已停稳；采集过程中板也必须稳定。
6. 可选 `depth_m`：已对齐到该图像 optical frame 的浮点米制深度，同时提供深度时间戳与坐标系。不要直接传 uint16 深度计数，也不要假定原始深度和彩色图片天然具有同一坐标。

D405 的 RGB 来自左深度成像器和 ISP；仍应逐流读取内参和畸变模型，不能据此跳过 stream profile 检查。工具直接接受无畸变图像和标准 OpenCV Brown 模型；RealSense `brown_conrady` 可显式转换。`modified_brown_conrady`、`inverse_brown_conrady` 或 fisheye 会被拒绝，需在驱动层按正确模型矫正图像并提供矫正后的 K，不能只改模型名称或把畸变系数置零。[RealSense 投影与畸变说明](https://dev.realsenseai.com/docs/projection-in-realsense-sdk-2-0/)

主手眼解算使用角点和已知板尺寸，不依赖深度读数。可选深度只用于比较角点附近深度与 PnP 预测深度，输出差值诊断；不会自动修改 depth scale。

## 接口使用示例

先把真实 stream profile 保存为 `camera.json`，再建立会话。这里的路径仅是示例，不会自动采集实机：

```bash
jev-calibrate init \
  --session runs/d405_left_calib \
  --camera camera.json \
  --board calibration/boards/d405_A4/board.json \
  --mount eye-in-hand \
  --base-frame robot_base \
  --end-frame left_flange \
  --clock-id synchronized_host_clock \
  --data-origin measured
```

驱动接好后，用 Python 追加一对数据：

```python
from gpt_jev_robot.handeye.session import CalibrationSession

session = CalibrationSession("runs/d405_left_calib")
# source 是你实现的驱动适配器，只有读取接口，没有机器人运动接口。
record = session.capture_from(source)
print(record["status"], record.get("reason"))
```

也可以先保存图片、`FrameMetadata` JSON 和 `RobotPose` JSON，再离线追加：

字段格式可参考合成示例：[相机参数](../examples/handeye/camera.synthetic.example.json)、[图像元数据](../examples/handeye/frame.synthetic.example.json)、[机器人位姿](../examples/handeye/pose.synthetic.example.json)。这些数值仅用于软件测试，接实机时必须替换为真实测量。

```bash
jev-calibrate add --session runs/d405_left_calib \
  --image capture_001.png --frame frame_001.json --pose pose_001.json
```

建议收集 20–30 组位置和朝向有变化的停稳样本，覆盖工作区域及图像不同位置，至少包含两个非平行旋转轴。只平移、绕同一个轴转动或重复拍摄一个姿态，都不能替代充分的旋转激励。程序至少要求 12 个有效样本；相近重复位姿会拒绝。这个数量门槛不是精度保证。

```bash
jev-calibrate solve --session runs/d405_left_calib \
  --output runs/d405_left_calib/result.json
```

默认 Park 方法；可显式选择 `--method tsai` 或 `horaud`。不要用同一组留出数据反复选择方法再把它当独立验证；方法调优后应重新采集验证集。

## 如何看结果

- 每张图保存源图、角点/坐标轴叠加图、PnP 位姿、角点数量、覆盖率、重投影误差和时间信息。
- 默认至少 12 个角点，图像覆盖率至少 2%，重投影 RMS 不超过 1 px。
- 时间不匹配、未停稳、图像分辨率改变、相机 profile 改变、重复姿态或图案质量不足，记录为 rejected，原图片与原因保留。
- 原图与元数据用哈希关联。记录后更改图片、深度、会话配置或元数据会阻止求解。
- 固定每第四个有效样本作为留出集，只用其余样本求解；不在留出集上重拟合。
- 训练集与留出集相对于固定板坐标关系的最大平移误差默认不超过 5 mm、最大旋转误差不超过 1°。可用 `--max-translation-mm` / `--max-rotation-deg` 调整，但这只是项目验收阈值，需要按任务精度确定。
- 通过时 `accepted_transform` 给出可用矩阵；失败时它为 null，候选矩阵仅留作诊断。退化数据会明确报错，不生成看似有效的外参。
- `data_origin=synthetic` 的结果只能作为软件测试，不能用来驱动实机。

低重投影误差不等于整体手眼准确，内部一致性也不等于绝对精度。实机最终还要用未参与标定的固定检查点/测试块验证位置误差，并确认纸面尺寸、刚性安装与机器人 FK。不要把厂商“深度精度”直接当作整套手眼系统精度。

## 内参的可选离线路径

优先读取 D405 当前图像流的工厂内参。如果确实需要用打印板重新估计某个图像流的内参，可以单独拍摄多视角图片，然后：

```bash
jev-calibrate intrinsics \
  --images intrinsics_images/*.png \
  --board calibration/boards/d405_A4/board.json \
  --camera-id YOUR_CAMERA_SERIAL \
  --stream-id YOUR_ACTIVE_STREAM \
  --optical-frame YOUR_OPTICAL_FRAME \
  --data-origin measured \
  --output runs/intrinsics_fit/camera.json
```

此命令把候选参数与训练/留出像素误差写入独立报告，通过检查才写 `camera.json`。输出不会回写 D405，不能替代双目基线、深度尺度或工厂自标定。

## 无硬件验证

```bash
jev-calibrate demo --output runs/handeye_demo_new --mount eye-in-hand
jev-calibrate demo --output runs/handeye_fixed_demo_new --mount eye-to-hand
```

两种模式各生成 24 张带透视变化的合成板图，经过真实 ChArUco 检测、PnP、手眼解算与六个留出姿态验证；同时比较已知外参。已有[软件验证记录](../examples/handeye/validation.json)、[合成原图](../examples/handeye/synthetic_image.png)与[检测叠加图](../examples/handeye/synthetic_detected.png)供核对。生成的 PDF 还经过独立 PDF 渲染、角点识别和纸面比例检查；未验证实际打印机输出。

合成测试验证软件方向与数据流程；它不包含真实镜头误差、纸张翘曲、机械间隙、时钟偏差或运动模糊，不能当作实机标定成绩。
