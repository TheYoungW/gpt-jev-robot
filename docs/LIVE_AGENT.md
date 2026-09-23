# 由会话中的 Agent 直接做视觉判断

用户要求用 Agent 的视觉理解和判断代替传统物体几何定位及固定抓取流程。默认入口因此改为 `AgentSession`。

新提案采用 [V2 结构化视觉证据协议](PROTOCOL_V2.md)，通过 `agent-template` / `agent-schema` 获取表单与规范。下文自由文本提案仅为 V1 兼容格式，历史记录不变。V2 的关系、未知项、候选参数和条件进入 Jev，审计备注保留本地。

## 执行关系

1. Python 返回相机 RGB 图片、机器人自身 TCP 与指垫接触反馈。
2. 当前会话中的 Agent **实际打开图片**，选择目标、夹持部位、接近方向与下一小步；也可以决定重新观察或重新抓取。
3. Agent 写一份单步提案：画面判断、简短任务级依据、两个以上候选动作。
4. Jev 根据这份描述选择一步；白名单接口执行。
5. Python 保存动作后的新图，返回会话。必须由 Agent 再次看图后才能提交下一份提案。

`AgentSession` 没有颜色检测、像素反投影定位、物体位姿读取、抓取点生成、物体遍历或动作阶段状态机。没有“按目标自动运行八个步骤”的循环。IK、关节插值和接触物理仍然保留：它们执行选定的运动，不判断抓哪里。

当前控制器提供 XYZ 平移和绕 Z 的朝向控制；本次视觉判断采用从上方接近。任意六维末端姿态和复杂避障尚未实现。

## 命令

```bash
jev-robot --run-dir runs/my_agent agent-start
# Agent 查看输出中的图片后，自己撰写 proposal.json。
jev-robot --run-dir runs/my_agent agent-step proposal.json
# 继续查看上一步返回的新图片，再撰写下一份单步提案。
# 视觉确认完成后，导出录像和独立验收报告。
jev-robot --run-dir runs/my_agent agent-evaluate
```

提案格式（观测编号和内容必须来自当前实际图像）：

```json
{
  "observation_id":"当前观测编号",
  "viewed_cameras":["center","l_wrist"],
  "visual_assessment":"The selected object is between the open fingers, but the fingertips are still visibly above its sides.",
  "decision_summary":"Make a small downward correction and inspect again before closing; the neighboring object is outside the gap.",
  "candidates":{
    "lower_small":{
      "description":"Lower the open gripper a short distance to inspect the side overlap more closely",
      "command":{"op":"nudge","direction":"down","distance":0.02}
    },
    "observe":{
      "description":"Obtain another view if the apparent clearance is ambiguous",
      "command":{"op":"observe","label":"recheck"}
    }
  }
}
```

MCP 对应 `observe()` → Agent 看图 → `agent_step(proposal)`。原有 `move_to`、`nudge`、`set_gripper` 保留为底层肌肉接口。不要通过普通 `act` 偷跑旧流程后声称使用了视觉 Agent 模式。

## 证据与限制

- 每次观测有唯一编号和图片 SHA-256；机器人移动、图片变化或引用旧编号都会使提案失效。
- `viewed_cameras` 是调用者的审计声明，程序不能证明模型实际看过图片；需要结合会话中的图片工具调用核查。
- 每步只有一个动作；无预设物体顺序、无预设抓取高度、无自动生成的动作阶段描述。
- `agent_steps.jsonl` 保存提案、Jev 选择、实际结果及下一观测；Markdown 保存任务级摘要。
- `agent-evaluate` 是单独的最终验收入口，可以读取仿真真值。不得把它的物体坐标用于动作规划。
- 三路深度与标定仍可保存用于复盘；本次在线视觉操作不使用深度数组或反投影来决定抓取位置。
- 没有后台 GPT 服务。离开当前会话后，Python 只会等待下一份提案；不会自行获得 Agent 的判断能力。
- `agent_step` 自带逐动作录像，不与 `serve --video` 混用；录制冲突在调用 Jev 前就会被拒绝。

实际完成的一轮操作见 [live_visual 实验记录](../examples/live_visual/run_notes.md)，包含首次试提失败后的重抓、滑落处理和 IK 拒绝后的缩短下降，不只保留成功片段。

旧几何定位/固定流程仅保留在显式 `baseline-perceive` / `baseline-demo` 命令中，用于历史对照。
