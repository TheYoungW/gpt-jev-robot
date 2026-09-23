# gpt-jev-robot

云弈双臂机器人在 MuJoCo 中把桌上的不规则物体收纳进盘子。采用 **当前会话中的 Agent → Jev 决策 → Python/MCP 运动接口 → 物理反馈** 的分层结构。

这个仓库不调用 GPT API。Agent 在会话中读取图像、给出目标和候选动作；Jev 通过 TypeSafe API 回答明确的“下一步选哪个动作”；Python 负责执行、限位检查、图像记录和结果验证。

## 演示

V2 的真实接口检查见 [examples/protocol_v2](examples/protocol_v2/run_notes.md)：Agent 填写结构化观测，Jev 返回重新观察，未执行抓取。这个记录用于核查新协议接线，不作为性能提升证据。

当前会话逐步看图操作的完整结果见 [examples/live_visual](examples/live_visual/)，包含抓取前后图片、约 4 倍速视频、39 次真实 Jev 决策、逐步视觉判断和独立验收报告。三个物体最终都落稳在盘内；黄色首次试提失败后重抓，黄色和红色运输/下降时曾滑落入盘，青色保持夹持至主动松爪。详见[实验记录与局限](examples/live_visual/run_notes.md)。这是一轮流程验证，不代表稳定抓取成功率。

早期固定流程的对照结果保留在 [examples/verified](examples/verified/)。两组视频均来自 MuJoCo 渲染。

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

默认使用会话视觉 Agent 模式：

```bash
jev-robot --run-dir runs/my_agent
```

这个命令只启动场景并返回相机图片。**接下来由当前会话中的 Agent 打开图片，判断目标、夹持位置、朝向和下一小步，提交给 Jev 选择；执行器完成一步后立即返回新图。** 没有自动颜色定位、预先算好的抓取点或固定八步流程。

```bash
jev-robot --run-dir runs/my_agent agent-step proposal.json
```

新提案使用 [V2 结构化视觉协议](docs/PROTOCOL_V2.md)：引用最新图片，填写有证据的关系与未知项，提交含完整参数、条件和结果检查的候选动作。`agent-template` 生成待填写表单，`agent-schema` 输出 JSON Schema。V2 不把 Agent 的推荐理由发送给 Jev；旧提案按 V1 保持兼容。Python 没有内置 GPT：需要当前会话中的 Agent 持续看图和发出新提案，单独运行命令不会自动替代这一过程。

Jev 密钥通过 `TYPESAFE_API_KEY` 传入，或读取本地忽略的 `.secrets/typesafe.key`。密钥不进入日志。

早期确定性流程只保留为显式基线，不是默认模式：

```bash
jev-robot --run-dir runs/legacy_baseline baseline-demo --video
jev-robot --run-dir runs/legacy_online baseline-demo --online --video
```

运行输出：

- `before_*.png` / `after_*.png`：抓取前后四个视角。
- `observations/`：每次动作后的 RGB、深度和当时的相机内外参。
- `episode_4x.mp4`：约 4 倍的**仿真时间**回放；本轮分段录制有效约 4.2 倍，不包含模型等待时暂停的仿真时间。
- `decisions.jsonl`：候选动作、输入状态、Jev 原始选择、分布、置信度和门控结果。
- `events.jsonl`：实际控制与接触反馈；`report.json`：独立验收结果。
- `agent_steps.jsonl`：每份看图后的单步提案、Jev 选择、执行结果和新观测编号。
- `decision_summary.md`：便于学习的任务级决策摘要、动作与结果，不是模型内部思维链。

## 在会话里由 Agent 操作

```bash
jev-robot --run-dir runs/agent agent-start
jev-robot --run-dir runs/agent agent-observe
jev-robot --run-dir runs/agent agent-template > proposal.json
# 实际看图并填写 proposal.json 后执行。
jev-robot --run-dir runs/agent agent-step proposal.json
# 完成后导出录像并进行独立最终验收。
jev-robot --run-dir runs/agent agent-evaluate
```

`act` 支持受限 JSON 动作，也支持 `op=decide`：Agent 提供当前观测状态和多个候选动作，Jev 选择后执行。必须引用当前 `simulation_time`，过期提案在联网前即被拒绝。完整格式见 [工具接口](docs/TOOLS.md)。`serve --video` 在同一进程中持续读取 JSONL，保留仿真、录像和会话状态。

MCP stdio 服务：

```bash
jev-robot-mcp
```

设置 `ROBOT_RUN_DIR` 可指定会话目录。提供 `observe`、`agent_schema`、`agent_template`、`agent_step`、`move_to`、`nudge`、`set_gripper`、`decide_next`、`verify_completion`。这些基础工具就是“肌肉层”；接入客户端后，Agent 可自由组合，Jev 接口在 Python 决策层。参见 [架构](docs/ARCHITECTURE.md) 与 [实验计划](docs/EXPERIMENTS.md)。

## 模型、标定与边界

- 原始 URDF：`assets/robot/models/yunyi_v1_0.urdf`，保留原始关节、限位与 tool0。
- 网格来自本地配套 SDK；生成的 MJCF 只保存在运行目录。
- 仿真 TCP 是根据夹爪网格确定的指尖中心，**不同于原 URDF tool0**。详见 [标定说明](docs/CALIBRATION.md)。
- 相机安装位置是暂定值；仿真内参和每次观测的外参是精确读取值，不代表实机标定已完成。
- 默认视觉 Agent 模式不运行颜色/几何物体定位，Agent 看 RGB 图片后作判断。旧颜色算法仅在显式 baseline 模式中运行；独立最终验收使用仿真真值，不提供给动作规划。
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
