# gpt-jev-robot

云弈双臂机器人在 MuJoCo 中把桌上的不规则物体收纳进盘子。采用 **当前会话中的 Agent → Jev 决策 → Python/MCP 运动接口 → 物理反馈** 的分层结构。

这个仓库不调用 GPT API。Agent 在会话中读取图像、给出目标和候选动作；Jev 通过 TypeSafe API 回答明确的“下一步选哪个动作”；Python 负责执行、限位检查、图像记录和结果验证。

## 演示

实际运行结果见 [examples/verified](examples/verified/)，包含抓取前后图片、约 4 倍速视频、真实 Jev 返回结果和验收报告。视频来自 MuJoCo 渲染，不是生成式视频。

**范围：仿真研究原型。** 当前演示用左臂搬运三个具有不同颜色的组合刚体，右臂待机；左右夹爪相机和中央相机构成三路观测，另有一个只用于录像的全景视角。夹取依赖执行器、接触和摩擦，没有物体瞬移、吸附或焊接约束。

## 安装与运行

在仓库根目录执行，Python 3.10+：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
export MUJOCO_GL=egl
```

本工作区已建立 `.venv`。它不使用 Articore-SDK 的 Conda `at` 环境。

先运行无网络的基线，确认物理环境：

```bash
jev-robot --run-dir runs/baseline demo --video
```

真实 Jev 模式：使用 TypeSafe 官方账户密钥，避免把密钥直接写进 shell 历史。

```bash
read -rs TYPESAFE_API_KEY
export TYPESAFE_API_KEY
jev-robot --run-dir runs/online demo --online --video
```

每次使用新的 run 目录。`demo` 是 **Agent 编写的有限步骤实验流程**：Jev 对每一步实际调用，程序校验阶段前置条件。不应把它称为“无人值守持续运行的 GPT Agent”。无需网络的基线明确标为 `deterministic_baseline_no_jev`，接口失败不会偷偷降级成成功的 Jev 演示。

运行输出：

- `before_*.png` / `after_*.png`：抓取前后四个视角。
- `observations/`：每次动作后的 RGB、深度和当时的相机内外参。
- `episode_4x.mp4`：约 4.02 倍的**仿真时间**回放；不包含模型等待时暂停的仿真时间。
- `decisions.jsonl`：候选动作、输入状态、Jev 原始选择、分布、置信度和门控结果。
- `events.jsonl`：实际控制与接触反馈；`report.json`：独立验收结果。
- `decision_summary.md`：便于学习的任务级决策摘要、动作与结果，不是模型内部思维链。

## 在会话里由 Agent 操作

```bash
jev-robot --run-dir runs/agent init
jev-robot --run-dir runs/agent observe
jev-robot --run-dir runs/agent perceive
jev-robot --run-dir runs/agent act examples/move_up.json
```

`act` 支持受限 JSON 动作，也支持 `op=decide`：Agent 提供当前观测状态和多个候选动作，Jev 选择后执行。必须引用当前 `simulation_time`，过期提案在联网前即被拒绝。完整格式见 [工具接口](docs/TOOLS.md)。`serve --video` 在同一进程中持续读取 JSONL，保留仿真、录像和会话状态。

MCP stdio 服务：

```bash
jev-robot-mcp
```

设置 `ROBOT_RUN_DIR` 可指定会话目录。提供 `observe`、`perceive`、`move_to`、`nudge`、`set_gripper`、`decide_next`、`verify_completion`。这些基础工具就是“肌肉层”；接入客户端后，Agent 可自由组合，Jev 接口在 Python 决策层。参见 [架构](docs/ARCHITECTURE.md) 与 [实验计划](docs/EXPERIMENTS.md)。

## 模型、标定与边界

- 原始 URDF：`assets/robot/models/yunyi_v1_0.urdf`，保留原始关节、限位与 tool0。
- 网格来自本地配套 SDK；生成的 MJCF 只保存在运行目录。
- 仿真 TCP 是根据夹爪网格确定的指尖中心，**不同于原 URDF tool0**。详见 [标定说明](docs/CALIBRATION.md)。
- 相机安装位置是暂定值；仿真内参和每次观测的外参是精确读取值，不代表实机标定已完成。
- 视觉基线只适用于已知颜色、尺寸的演示物体；没有把仿真物体位姿冒充视觉感知。独立验收明确使用仿真真值。
- 保留关节限位并检查 IK、目标工作空间、跟踪误差和夹持接触；这里没有完整的碰撞规划、真实机器人安全认证或实机接口。

## 验证

```bash
pytest -q
```

如果当前终端加载了 ROS，使用 `env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv/bin/pytest -q` 隔离其 pytest 插件，避免把 ROS 的 Python 3.10 包带入项目虚拟环境。

测试覆盖决策异常/低置信度门控、密钥不入决策日志、过期状态、非法动作、标定投影、物理夹持与松爪落盘、状态恢复。在线结果另行记录，单次成功不等于泛化成功率。

## 参考

- [Agent as Policy 项目](https://agent-as-policy-2026.github.io/)：观察、代码/工具调用和物理反馈闭环。
- [TypeSafe Jev 官方文档](https://docs.typesafe.ai/introduction)：结构化决策模型。
- [State](https://docs.typesafe.ai/concepts/state)：当前仅支持文本/JSON，不能直接输入相机图像。
- [Confidence](https://docs.typesafe.ai/confidence)：分布派生的置信度，不是机器人动作成功率。

模型资产归属和仿真修改说明见 [assets/robot/PROVENANCE.md](assets/robot/PROVENANCE.md)。仓库不附带任何 API 密钥。
