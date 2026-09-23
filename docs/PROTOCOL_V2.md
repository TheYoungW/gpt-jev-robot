# V2：结构化视觉证据与动作选择

V2 将“看见什么”和“选择什么动作”分开。会话 Agent 仍必须实际查看图像、填写观测和设计候选；Python 不替代视觉判断，也不运行固定抓取阶段。Jev 接收完整结构化状态，返回一次 Choice。

这是协议和可审计性改进，尚未证明能提高提取准确率或抓取成功率。结构化内容可能比短摘要消耗更多 token，需配对实验测量。

## 使用入口

```bash
jev-robot --run-dir runs/v2_session agent-start
# 在会话中打开返回的图片。
jev-robot --run-dir runs/v2_session agent-template > proposal.json
# 根据实际图片填写 proposal.json。
jev-robot --run-dir runs/v2_session agent-step proposal.json
# 每一步后重新看图，再填写下一份提案。
jev-robot agent-schema > visual-proposal-v2.schema.json
```

模板故意留下空的 `viewed_cameras`，不能直接执行；默认 unknown 不是机器已经看过图片的判断。模板的 wait 只是结构示例，应按场景选择候选。MCP 对应 `agent_schema()`、`agent_template()`、`observe()`、`agent_step(proposal)`。Schema 从实际校验模型生成。

仓库同时提供[导出的 JSON Schema](../schemas/visual-proposal-v2.schema.json) 和[真实 API 检查记录](../examples/protocol_v2/run_notes.md)。

`schema_version: "2.0"` 选择 V2；不填版本或填 `"1.0"` 仍走 V1 兼容路径，不会把旧文本冒充结构化观测。未知版本直接拒绝。

## 固定字段

| 字段 | 填写方与约束 |
| --- | --- |
| `schema_version` | 固定为 `"2.0"` |
| `observation_id` | 最新未消费观测 |
| `viewed_cameras` | Agent 实际打开的本次机器人相机；不含录像 overview |
| `compared_images` | 实际打开的历史图片引用，最多追溯八个相邻观测收据 |
| `working_arm` | l 或 r，默认 l；一份提案只使用一只手臂 |
| `target_id` / `objects` | Agent 分配并跨步骤维护 object_01 等 ID；记录外观与可见性，不读取仿真物体位置 |
| `visual_facts` | 带来源的关系判断：yes / no / unknown |
| `candidates` | observe 加至少一个 a01、a02 等中性编号候选 |
| `audit_note` | 可选复盘文字，只写本地日志，不发送给 Jev |

额外字段被拒绝，不能提交 decision_summary、自报 robot_feedback 或物体 XYZ。外观、reason 仍是 Agent 文本，不能因此宣称完全消除了语言偏差。

每个目标必须填写六个关系：

| 关系 | 含义 |
| --- | --- |
| visible | 目标目前是否能看见 |
| between_fingers | 是否位于工作夹爪的夹指之间 |
| finger_side_overlap | 夹指是否已到达适合闭合的物体侧面 |
| moves_with_gripper | 前后图片是否支持目标跟随夹爪移动 |
| inside_tray | 从图像判断目标整体是否在盘内；不读取仿真包围盒 |
| resting_on_surface | 是否看起来已由表面支撑 |

遮挡、视角冲突或尚未试提时填 unknown 并说明原因。no 表示观测支持否定，不能代替未知。不提供未经校准的数字“视觉成功概率”。

```json
{
  "subject": "object_02",
  "relation": "moves_with_gripper",
  "value": "unknown",
  "source": "agent_visual_assessment",
  "evidence": [],
  "reason": "No before/after lift images have been inspected yet"
}
```

肯定或否定关系必须引用当前图片。moves_with_gripper 还必须引用已声明查看的历史图片，两次记录的工作臂 TCP 位移超过 5 mm、仿真时间递增。这只是证据前提检查，不能证明图像解释正确，也不意味着固定的抬升幅度。

收据和原始图片哈希保存到 `observation_receipts/`。历史引用需要存在于最近八个收据的链接中，且图片哈希未改变。旧版运行目录可生成新观测开始使用 V2，但不能凭空生成原来没有保存的历史收据。

## 完整动作

一个候选片段如下，观测编号和判断必须来自实际图像：

```json
{
  "a01": {
    "intent": "test_lift",
    "command": {"op": "nudge", "direction": "up", "distance": 0.02, "arm": "l"},
    "requires": [{"fact": "object_02.between_fingers", "equals": "yes"}],
    "clearance": {
      "value": "yes",
      "source": "agent_visual_assessment",
      "evidence": [{"observation_id": "CURRENT_ID", "camera": "center"}],
      "reason": "The short upward corridor is visible and unobstructed"
    },
    "expected_observation": [{"relation": "moves_with_gripper", "value": "yes"}],
    "failure_signals": [{"relation": "moves_with_gripper", "value": "no"}]
  }
}
```

world 方向：up/down ±Z、forward/back ±X、left/right ±Y；距离米、角度弧度。完整参数及补全的默认值进入 Jev `state.candidate_actions`。criteria 描述由程序用 intent 和参数生成，不接受自定义候选说明。

每个移动候选独立提供 clearance，不能用“向上可见”支持“向下也可走”。未知或不满足的路径判断使该候选不可执行。这仍是视觉判断，不能替代完整碰撞检测。

意图：observe、approach、align、close、test_lift、transport、lower、release、retreat、wait、finish。它们没有固定先后顺序。程序验证 intent 与 op 的基本一致性，并补充必要条件：

- close：位于夹指间、与侧面有重叠。
- test_lift、transport、lower：双指接触、位于夹指间；transport 额外要求随动证据。
- release：图像支持目标在盘内，允许打开滑落后的空夹爪。
- finish：所有声明物体均有在盘内、表面支撑和脱离夹指的证据；实际 finish 仍调用独立验收器。
- 所有移动：候选独立的路径判断为 yes，通过工作空间和整条路径的 IK 预检。

requires 可引用 `object_02.inside_tray` 等关系，或 `robot.l.both_pad_contacts`、`robot.l.commanded_closed`。程序计算 met/unmet/unknown，不接受 Agent 自报 eligibility。commanded_closed 是执行器命令状态，不等于已夹住；双指接触不等于稳定夹持。

完整候选（包括不满足条件的备选）都发给 Jev。选中不可执行候选时，执行端改为 observe，记录 `gate_reason=ineligible_action`，保留原始 Choice。低置信度也回到 observe。协议错误在联网前拒绝；HTTP 错误不执行动作。

last_action 由执行器提供实际命令、执行状态、预期观测与失败信号。下一提案用新视觉事实生成 previous_action_review，缺失证据保持 unknown。这不是自动视觉成功检测器。

## 审计与边界

- decisions.jsonl：Jev 实际收到的规范化提案、响应、门控，不含 audit_note。
- agent_steps.jsonl：完整提案（含审计备注）、动作结果、下一观测。
- timing.observation_to_submission_s：拍摄到提交的墙钟间隔，包含用户暂停和工具等待，不是纯思考时间。
- timing.step_wall_time_s：预检、API、执行和反馈采集的墙钟时间；API 请求时长另外记录。

协议不能证明 Agent 真的看了图、对象 ID 跟踪正确或候选集完整。动作意图也无法覆盖所有复杂行为。它提供字段一致性、证据追溯和执行前检查；实际准确率与效率按[实验计划](EXPERIMENTS.md)验证。

V2 不新增颜色检测、反投影定位、固定抓取点、后台 GPT API 或物体真值输入。历史 examples/live_visual 保留原样，不能当作 V2 实验成绩。
