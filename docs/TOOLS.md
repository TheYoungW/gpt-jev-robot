# Agent 工具接口

新提案入口：`agent-template`、`agent-schema` 和 `agent-step`，详见 [V2 协议](PROTOCOL_V2.md)。MCP 新增 `agent_template`、`agent_schema`；底层兼容接口不具备 V2 的结构化证据校验。

默认入口与提案协议见 [LIVE_AGENT.md](LIVE_AGENT.md)。下文的 `act/decide` 是较低层的兼容接口；它不具备新的图片收据检查。默认 MCP `observe` 返回观测编号，使用 `agent_step` 提交看图后的下一步。

从仓库根目录运行。CLI 通过运行目录中的 `state.npz` 保存和恢复机器人、物体、控制目标和仿真时间。

## 基础动作

```json
{"op":"move","position":[0.32,0.18,0.76],"arm":"l","yaw":1.5707963267948966}
```

```json
{"op":"nudge","direction":"up","distance":0.025,"arm":"l"}
```

```json
{"op":"gripper","closed":true,"arm":"l"}
```

支持 `observe`、`move`、`nudge`、`gripper`、`wait`、`finish`。方向是 world 坐标：up/down 为 ±Z，forward/back 为 ±X，left/right 为 ±Y。`yaw` 是夹爪绕 world Z 的角度，不是末端局部增量。`finish` 必须通过独立验收器。

## Jev 提案

Agent 先读取最新图像，获取当前 `simulation_time`，再构造：

```json
{
  "op":"decide",
  "proposal":{
    "state":{
      "simulation_time":0.8,
      "observation_summary":"Open left gripper; coral object visible on table; tray is empty.",
      "evidence":"Current center and left-wrist RGB-D",
      "goal":"Place coral object inside tray and verify after release"
    },
    "candidates":{
      "approach":{
        "description":"Approach the clearly visible coral object from above with open fingers",
        "command":{"op":"move","position":[0.385,0.16,0.76]}
      },
      "observe":{
        "description":"Gather another observation if localization is uncertain",
        "command":{"op":"observe","label":"recheck"}
      }
    }
  }
}
```

保存为 JSON 文件后用 `jev-robot --run-dir runs/agent act path/to/proposal.json` 执行。示例时间必须换成当前状态时间，示例位姿必须根据当前观测更新。低置信度默认转为 observe，HTTP 错误直接停止，绝不静默使用离线选择替代 Jev。

`serve --video` 接受相同 JSON，每行一个请求，返回每行一个结果；发送 `{"op":"quit"}` 保存状态、完成 MP4。不要同时让两个进程操纵同一个运行目录。

MCP 用 stdio 传输，不开放网络端口。环境变量 `ROBOT_RUN_DIR` 选择运行目录。MCP 基础工具不自动决定动作；Agent 可以调用 `decide_next(proposal)`，由 Jev 选择并执行一个经过检查的动作，也可以先调用 Python `JevClient`。当前会话中的工具使用方式是 CLI/Python，MCP 则作为可供后续客户端接入的同一套能力封装。
