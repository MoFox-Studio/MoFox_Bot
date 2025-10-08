<div align="center">

# 🌟 MoFox_Bot
**🚀 基于 MaiCore 的增强版 AI 智能体，提供更完善的功能和更好的使用体验**
</div>

<p align="center">
  <a href="https://raw.githubusercontent.com/MoFox-Studio/MoFox_Bot/refs/heads/master/LICENSE">
    <img src="https://img.shields.io/github/license/MoFox-Studio/MoFox_Bot" alt="license">
  </a>
  <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=edb641" alt="python">
  <a href="https://github.com/Microsoft/pyright">
    <img src="https://img.shields.io/badge/types-pyright-797952.svg?logo=python&logoColor=edb641" alt="pyright">
  </a>
  <a href="https://github.com/astral-sh/ruff">
    <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/charliermarsh/ruff/main/assets/badge/v2.json" alt="ruff">
  <a href="https://deepwiki.com/MoFox-Studio/MoFox_Bot"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki"></a>
  </a>
  <br />
  <a href="https://qm.qq.com/q/YwZTZl7BG8">
    <img src="https://img.shields.io/badge/墨狐狐的大学-169850076-violet?style=flat-square" alt="QQ Chat Group">
  </a>
    <a href="https://qm.qq.com/q/Lmm1LZnewg">
    <img src="https://img.shields.io/badge/墨狐狐技术部-1064097634-orange?style=flat-square" alt="QQ Chat Group">
  </a>
</p>
 
---
 
<div align="center">
 
## 📖 项目介绍
 
**MoFox_Bot** 是一个基于 [MaiCore](https://github.com/MaiM-with-u/MaiBot) `0.10.0 snapshot.5` 版本的增强型 `fork` 项目。
我们在保留原版几乎所有功能的基础上，进行了一系列的改进和功能拓展，致力于提供更强的稳定性、更丰富的功能和更流畅的用户体验
 
> [!IMPORTANT]
> **第三方项目声明**
>
> 本项目是由 **MoFox Studio** 独立维护的第三方项目，并非 MaiBot 官方版本。
> 所有后续更新和维护均由我们团队负责，与 MaiBot 官方无直接关系。
 
> [!WARNING]
> **迁移风险提示**
>
> 由于我们对数据库结构进行了重构和优化，从 MaiBot 官方版本直接迁移到 MoFox_Bot **可能会遇到数据不兼容的问题**。
> 在迁移前，请务必做好数据备份。
 
</div>
 
---
 
<div align="center">
 
## ✨ 功能特性
 
</div>
 
<table>
<tr>
<td width="50%">
 
### 🔧 原版功能（全部保留）
 
- 🧠 **智能对话系统** - 基于 LLM 的自然语言交互，支持 normal 和 focus 统一化处理
- 🔌 **强大插件系统** - 全面重构的插件架构，支持完整的管理 API 和权限控制
- 💭 **实时思维系统** - 模拟人类思考过程
- 📚 **表达学习功能** - 学习群友的说话风格和表达方式
- 😊 **情感表达系统** - 情绪系统和表情包系统
- 🧠 **持久记忆系统** - 基于图的长期记忆存储
- 🎭 **动态人格系统** - 自适应的性格特征和表达方式
- 📊 **数据分析** - 内置数据统计和分析功能，更好了解麦麦状态
 
</td>
<td width="50%">
 
### 🚀 拓展功能
 
- 🔄 **数据库切换** - 支持 SQLite 与 MySQL 自由切换，采用 SQLAlchemy 2.0 重新构建
- 🛡️ **反注入集成** - 内置一整套回复前注入过滤系统，为人格保驾护航
- 🎥 **视频分析** - 支持多种视频识别模式，拓展原版视觉
- 😴 **苏醒系统** - 能够睡觉、失眠、被吵醒，更具乐趣
- 📅 **日程系统** - 让墨狐规划每一天
- 🧠 **拓展记忆系统** - 支持瞬时记忆等多种记忆
- 🎪 **完善的 Event** - 支持动态事件注册和处理器订阅，并实现了聚合结果管理
- 🔍 **内嵌魔改插件** - 内置联网搜索等诸多功能，等你来探索
- 🌟 **还有更多** - 请参阅详细修改 [commits](https://github.com/MoFox-Studio/MoFox_Bot/commits)
 
</td>
</tr>
</table>
 
---
 
<div align="center">
 
## 🔧 系统要求
 
### 💻 基础环境
 
| 项目         | 要求                                                 |
| ------------ | ---------------------------------------------------- |
| 🖥️ 操作系统 | Windows 10/11, macOS 10.14+, Linux (Ubuntu 18.04+) |
| 🐍 Python 版本 | Python 3.10 或更高版本                               |
| 💾 内存       | 建议 4GB 以上可用内存                                |
| 💿 存储空间   | 至少 2GB 可用空间                                    |
 
### 🛠️ 依赖服务
 
| 服务         | 描述                                       |
| ------------ | ------------------------------------------ |
| 🤖 QQ 协议端  | [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 或其他兼容协议端 |
| 🗃️ 数据库     | SQLite (内置) 或 MySQL (可选)              |
| 🔧 管理工具   | Chat2DB (可选，用于数据库管理)             |
 
</div>
 
---
 
<div align="center">
 
## 🏁 快速开始
 
### 📦 安装与部署
 
</div>
 
> [!NOTE]
> 详细的安装和配置步骤，请务必参考我们的官方文档：
> *   **Windows 用户部署指南**: [https://mofox-studio.github.io/MoFox-Bot-Docs/docs/guides/deployment_guide.html](https://mofox-studio.github.io/MoFox-Bot-Docs/docs/guides/deployment_guide.html)
> *   **`bot_config.toml` 究极详细教程**: [https://mofox-studio.github.io/MoFox-Bot-Docs/docs/guides/bot_config_guide.html](https://mofox-studio.github.io/MoFox-Bot-Docs/docs/guides/bot_config_guide.html)
 
<div align="center">
 
### ⚙️ 配置要点
 
1.  📝 **核心配置**: 修改 `config/bot_config.toml` 中的基础设置，如 LLM API Key 等。
2.  🤖 **协议端配置**: 设置 NapCatQQ 或其他兼容的 QQ 协议端，确保通信正常。
3.  🗃️ **数据库配置**: 根据需求选择 SQLite 或配置你的 MySQL 服务器。
4.  🔌 **插件配置**: 在 `config/plugins/` 目录下按需配置插件。
 
</div>
 
---
 
<div align="center">
 
## 🙏 致谢
 
我们衷心感谢以下优秀的开源项目，没有它们，就没有 MoFox_Bot。
 
| 项目                                       | 描述                 | 贡献             |
| ------------------------------------------ | -------------------- | ---------------- |
| 🎯 [MaiM-with-u/MaiBot](https://github.com/MaiM-with-u/MaiBot) | 原版 MaiBot 项目     | 提供优秀的基础框架 |
| 🐱 [NapNeko/NapCatQQ](https://github.com/NapNeko/NapCatQQ) | 基于 NTQQ 的 Bot 协议端 | 现代化的 QQ 协议实现 |
| 🌌 [internetsb/Maizone](https://github.com/internetsb/Maizone) | 魔改空间插件         | 插件部分功能借鉴 |
 
</div>
 
---
 
<div align="center">
 
## ⚠️ 注意事项
 
> [!CAUTION]
> **重要提醒**
>
> - 使用本项目前，你必须阅读并同意 [**📋 用户协议 (EULA.md)**](EULA.md)。
> - 本应用生成的内容来自人工智能大模型，请仔细甄别其准确性，并请勿用于任何违反法律法规的用途。
> - AI 生成的所有内容不代表本项目团队的任何观点和立场。
 
</div>
 
---
 
<div align="center">
 
## 📄 开源协议
 
本项目基于 **[GPL-3.0](LICENSE)** 协议开源。
 
[![GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg?style=for-the-badge&logo=gnu)](LICENSE)
 
```
                        Copyright © 2025 MoFox Studio
                Licensed under the GNU General Public License v3.0
```
 
</div>
 
---
 
<div align="center">
 
**🌟 如果这个项目对你有帮助，请给我们一个 Star！**
 
**💬 有任何问题或建议？欢迎提交 Issue 或 Pull Request！**

**💬 [点击加入 QQ 交流群](https://qm.qq.com/q/jfeu7Dq7VS)**

_Made with ❤️ by [MoFox Studio](https://github.com/MoFox-Studio)_

</div>
