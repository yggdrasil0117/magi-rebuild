# 启动脚本编码修复

日期：2026-10-30。

用户运行 `scripts/start_magi.ps1` 时遇到字符串终止符和右括号缺失错误。脚本为含中文的UTF-8无BOM文件；在Windows PowerShell 5.1实际复现解析失败，而PowerShell 7可解析。

修复：仅为启动脚本增加UTF-8 BOM，保持原有启动和密钥处理逻辑。修复后使用各自原生 `Parser.ParseFile` 验证：

- Windows PowerShell 5.1.26100.9711：ParseErrors=0。
- PowerShell 7.6.5：ParseErrors=0。

本次只验证脚本解析，未启动或停止用户服务，未读取密钥、调用模型或运行全套业务测试。此为常规启动兼容修复，不纳入question.md的重大工程问题。保留用户已有的council/service.py工作区修改。

用户可在原终端重新运行 ` .\scripts\start_magi.ps1 `。脚本使用 `.venv-harness` 内的Python，不依赖当前终端是否激活 `.venv`。
