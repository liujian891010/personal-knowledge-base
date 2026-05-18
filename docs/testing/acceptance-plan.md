# acceptance-plan

当前发布验收基线为 v1.0.43。

## 固定命令

仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1
```

发布签名安装包时追加签名产物 lane：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1 -ReleaseArtifacts
```

## 验收矩阵

完整矩阵见 [`v1.0.43-acceptance-matrix.md`](v1.0.43-acceptance-matrix.md)。

`V1043-M5-05` 冻结的规则：

1. 每个 P0 任务至少要有单测或 smoke gate。
2. 发布前命令必须通过 `scripts\ci\v1043-acceptance.ps1` 固化。
3. 签名安装包验证走 `-ReleaseArtifacts`，无签名凭据时必须在 RC 报告里写明豁免原因。
4. `V1043-M5-06` 负责补齐 1000 次连续同步和长期离线回归报告。
5. `V1043-M5-07` 只消费已通过的矩阵、稳定性报告和签名产物结果，不再临时发明验收口径。
