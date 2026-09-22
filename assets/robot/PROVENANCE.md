# 模型资产来源

- `models/yunyi_v1_0.urdf`：用户指定的 `/home/ubuntu/motorbridge/runtime/models/yunyi_v1_0.urdf` 的原样副本。
- `meshes/bessica_M1.3/*.STL`：用户本地配套 `/home/ubuntu/Articore-SDK/arx_d_can/meshes/bessica_M1.3/` 中的对应网格。原 URDF 的相对网格路径在 motorbridge 目录中不存在，因此从配套 SDK 获取。
- 保留资产原有权利归属，不替原始 CAD 模型赋予新的开源许可证。
- 相机、桌面、盘子、组合物体、指尖碰撞垫、仿真 TCP、执行器和接触排除由本项目在生成 MJCF 时添加。
- 重力补偿、伺服增益、摩擦参数属于仿真假设，没有声称通过实机参数辨识。
