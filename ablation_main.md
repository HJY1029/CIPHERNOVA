**【已调用 LLM】** 下表为真实运行 `generate_and_save` 后的汇总指标。
- **覆盖云端 LLM（共 4 家）**：`claude`、`deepseek`、`doubao`、`openai`

**表：主要消融实验结果**（对应 `tab:ablation_main`）

| 系统配置 | VPR (%) | FTPR (%) | 性能下降 (VPR/FTPR) |
| :--- | :---: | :---: | :---: |
| 完整所提方法 | 0.0 | 0.0 | – / – |
| 无测试反馈改进 | 0.0 | 0.0 | 0.0% / 0.0% |
| 无分层提示架构（仅 base_prompt.yaml） | 0.0 | 0.0 | 0.0% / 0.0% |
| 无任何提示 | 0.0 | 0.0 | 0.0% / 0.0% |

### 运行诊断
- **基线变体** `完整所提方法`：完成 generate 请求 **0/48**，GSR 命中 **0**，VPR 命中 **0**，FTPR 命中 **0**

**CryptoAgent 初始化失败**（该 provider 全部跳过）：`claude`: 未找到API密钥: ANTHROPIC_API_KEY。请在Web界面配置API密钥或设置环境变量。；`deepseek`: 未找到API密钥: DEEPSEEK_API_KEY。请在Web界面配置API密钥或设置环境变量。；`doubao`: 未找到API密钥: DOUBAO_API_KEY。请在Web界面配置API密钥或设置环境变量。；`openai`: 未找到API密钥: OPENAI_API_KEY。请在Web界面配置API密钥或设置环境变量。
**基线变体典型失败摘录**（`完整所提方法`）：
- 异常: 未找到API密钥: ANTHROPIC_API_KEY。请在Web界面配置API密钥或设置环境变量。
- 异常: 未找到API密钥: ANTHROPIC_API_KEY。请在Web界面配置API密钥或设置环境变量。
- 异常: 未找到API密钥: ANTHROPIC_API_KEY。请在Web界面配置API密钥或设置环境变量。
- 异常: 未找到API密钥: ANTHROPIC_API_KEY。请在Web界面配置API密钥或设置环境变量。
- 异常: 未找到API密钥: ANTHROPIC_API_KEY。请在Web界面配置API密钥或设置环境变量。
- 异常: 未找到API密钥: ANTHROPIC_API_KEY。请在Web界面配置API密钥或设置环境变量。
**解读**：全部 `ok=false` 多为 LLM 调用异常或 Agent 初始化失败；请检查 API Key、网络与 config.yaml。