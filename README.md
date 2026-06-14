<div align="center">

<img src="assets/banner.svg" width="100%" alt="Agent 工具调用平台">

<br>

### 🤖 Agent 工具调用平台

[![Stars](https://img.shields.io/github/stars/dirjaker/agent_platform?style=flat-square&label=Stars&color=FFD700)](https://github.com/dirjaker/agent_platform/stargazers)
[![Forks](https://img.shields.io/github/forks/dirjaker/agent_platform?style=flat-square&label=Forks&color=4A90D9)](https://github.com/dirjaker/agent_platform/network/members)
[![Contributors](https://img.shields.io/github/contributors/dirjaker/agent_platform?style=flat-square&label=Contributors&color=8B4513)](https://github.com/dirjaker/agent_platform/graphs/contributors)
[![License](https://img.shields.io/github/license/dirjaker/agent_platform?style=flat-square&label=License&color=20B2AA)](https://github.com/dirjaker/agent_platform/blob/dev/LICENSE)

</div>

---

## ✨ 功能特性

| 功能 | 描述 |
|------|------|
| 🔧 **Function Calling** | 标准化工具调用协议，支持 OpenAI 格式 |
| 🔄 **ReAct 循环** | 推理-行动-观察循环，自主决策执行 |
| 🧰 **18 种内置工具** | 文件操作、网络搜索、代码执行、数据分析等 |
| 📝 **工具注册** | 自定义工具注册和管理，热插拔机制 |
| 💬 **多轮对话** | 支持上下文保持的多轮交互 |
| 📊 **执行追踪** | 完整记录工具调用链和执行过程 |


## 🚀 快速开始

```bash
# 克隆项目
git clone https://github.com/dirjaker/agent_platform.git
cd agent_platform

# 创建虚拟环境
conda create -n agent_platform python=3.12 -y
conda activate agent_platform

# 安装依赖
pip install -r requirements.txt

# 运行项目
python main.py
```

## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| **后端** | FastAPI, Pydantic |
| **AI 引擎** | OpenAI, DeepSeek |
| **工具集** | Python, subprocess |
| **存储** | SQLite |

## 📝 开发日志

- [x] Function Calling 协议
- [x] ReAct 循环引擎
- [x] 18 种内置工具
- [x] 工具注册机制
- [x] 执行追踪日志
- [ ] Web 工具编辑器
- [ ] 工具市场
- [ ] 多 Agent 协作

## 📄 许可证

[MIT License](LICENSE)

---

<div align="center">

🔗 **GitHub**: [dirjaker/agent_platform](https://github.com/dirjaker/agent_platform)

⭐ 如果这个项目对你有帮助，请给一个 Star 支持一下！

</div>
