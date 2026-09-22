# 决策与验证摘要
运行模式：`live_jev_agent_authored_playbook`。以下是任务级决策摘要和实测结果。

## coral

依据中央 RGB-D 测得表面中心 `[0.3848967751117371, 0.1598418522109865, 0.6499753458295375]`，从上方接近；先试提，确认夹持后才转移。
- `approach`：t=2.52s；双侧接触=False；观测 `observations/00002.522_coral_approach/center.png`。
- `descend`：t=4.28s；双侧接触=False；观测 `observations/00004.284_coral_descend/center.png`。
- `close`：t=5.08s；双侧接触=True；观测 `observations/00005.084_coral_close/center.png`。
- `test_lift`：t=6.85s；双侧接触=True；观测 `observations/00006.846_coral_test_lift/center.png`。
- `transfer`：t=9.74s；双侧接触=True；观测 `observations/00009.742_coral_transfer/center.png`。
- `lower`：t=10.99s；双侧接触=True；观测 `observations/00010.992_coral_lower/center.png`。
- `release`：t=11.79s；双侧接触=False；观测 `observations/00011.792_coral_release/center.png`。
- `retreat`：t=13.43s；双侧接触=False；观测 `observations/00013.428_coral_retreat/center.png`。
- 松爪、退臂、静置后评估：物体在盘内，速度 0.000000 m/s。该验收使用独立仿真真值，不冒充视觉估计。

## teal

依据中央 RGB-D 测得表面中心 `[0.33258001698184564, 0.23221741890182768, 0.6542121932353006]`，从上方接近；先试提，确认夹持后才转移。
- `approach`：t=17.96s；双侧接触=False；观测 `observations/00017.964_teal_approach/center.png`。
- `descend`：t=19.73s；双侧接触=False；观测 `observations/00019.726_teal_descend/center.png`。
- `close`：t=20.53s；双侧接触=True；观测 `observations/00020.526_teal_close/center.png`。
- `test_lift`：t=22.29s；双侧接触=True；观测 `observations/00022.288_teal_test_lift/center.png`。
- `transfer`：t=25.94s；双侧接触=True；观测 `observations/00025.940_teal_transfer/center.png`。
- `lower`：t=27.19s；双侧接触=True；观测 `observations/00027.190_teal_lower/center.png`。
- `release`：t=27.99s；双侧接触=False；观测 `observations/00027.990_teal_release/center.png`。
- `retreat`：t=29.63s；双侧接触=False；观测 `observations/00029.626_teal_retreat/center.png`。
- 松爪、退臂、静置后评估：物体在盘内，速度 0.000000 m/s。该验收使用独立仿真真值，不冒充视觉估计。

## gold

依据中央 RGB-D 测得表面中心 `[0.263632424722729, 0.17942567784501734, 0.6561573806160692]`，从上方接近；先试提，确认夹持后才转移。
- `approach`：t=33.95s；双侧接触=False；观测 `observations/00033.952_gold_approach/center.png`。
- `descend`：t=35.71s；双侧接触=False；观测 `observations/00035.714_gold_descend/center.png`。
- `close`：t=36.51s；双侧接触=True；观测 `observations/00036.514_gold_close/center.png`。
- `test_lift`：t=38.28s；双侧接触=True；观测 `observations/00038.276_gold_test_lift/center.png`。
- `transfer`：t=42.18s；双侧接触=True；观测 `observations/00042.180_gold_transfer/center.png`。
- `lower`：t=43.43s；双侧接触=True；观测 `observations/00043.430_gold_lower/center.png`。
- `release`：t=44.23s；双侧接触=False；观测 `observations/00044.230_gold_release/center.png`。
- `retreat`：t=45.87s；双侧接触=False；观测 `observations/00045.866_gold_retreat/center.png`。
- 松爪、退臂、静置后评估：物体在盘内，速度 0.000000 m/s。该验收使用独立仿真真值，不冒充视觉估计。
