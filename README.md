<div align="center">

# 📧 EmailNotixion

**实时 IMAP 邮件推送插件 for AstrBot**

[![Version](https://img.shields.io/badge/version-v1.1.0-blue.svg)](https://github.com/OlyMarco/EmailNotixion/releases)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-purple.svg)](https://github.com/Soulter/AstrBot)

一个简洁高效的邮件推送插件，支持多账号 IMAP 监控和跨平台消息推送。

[功能特性](#-功能特性) •
[快速开始](#-快速开始) •
[指令说明](#-指令说明) •
[配置指南](#-配置指南) •
[常见问题](#-常见问题)

</div>

---

## ✨ 功能特性

<table>
<tr>
<td width="50%">

### 🚀 高性能设计
- **异步非阻塞** - 不影响主线程
- **多账号并发** - 同时监控多个邮箱
- **智能缓存** - 账号状态5分钟缓存

</td>
<td width="50%">

### 🛡️ 稳定可靠
- **自动重建** - 每2分钟刷新连接
- **超时保护** - 30秒超时防止卡死
- **状态恢复** - 重载后自动恢复推送

</td>
</tr>
<tr>
<td width="50%">

### 📱 跨平台支持
- QQ / 微信 / 钉钉
- Telegram / Discord
- 所有 AstrBot 支持的平台

</td>
<td width="50%">

### 🔧 灵活配置
- 可调检查间隔 (≥0.5s)
- 可调字符上限 (≥10)
- 实时生效，无需重启

</td>
</tr>
</table>

## 📸 效果展示

<div align="center">
<img src="left.png" alt="指令示例" width="45%" />
<img src="right.png" alt="推送效果" width="45%" />
</div>

## 🚀 快速开始

### 1️⃣ 安装插件

在 AstrBot 插件市场搜索 `EmailNotixion` 并安装，或通过 Git 仓库地址安装：

```
https://github.com/OlyMarco/EmailNotixion
```

### 2️⃣ 添加邮箱账号

```
/email add imap.qq.com,123456@qq.com,你的授权码
```

### 3️⃣ 开启推送

```
/email on
```

就这么简单！现在你就可以收到邮件推送了 🎉

## 📖 指令说明

### 基本指令

| 指令 | 说明 |
|:-----|:-----|
| `/email` | 查看当前状态 |
| `/email on` | 开启邮件推送 |
| `/email off` | 关闭邮件推送 |
| `/email help` | 显示帮助信息 |

### 账号管理

| 指令 | 说明 |
|:-----|:-----|
| `/email add <配置>` | 添加邮箱账号 |
| `/email del <邮箱>` | 删除邮箱账号 |
| `/email list` | 查看账号状态 |

**添加格式**: `IMAP服务器,邮箱地址,应用密码`

```bash
# Gmail
/email add imap.gmail.com,your@gmail.com,应用专用密码

# QQ邮箱
/email add imap.qq.com,123456@qq.com,授权码

# 163邮箱
/email add imap.163.com,your@163.com,授权码

# Outlook
/email add imap-mail.outlook.com,your@outlook.com,密码
```

### 参数设置

| 指令 | 说明 | 范围 |
|:-----|:-----|:-----|
| `/email interval [秒]` | 设置检查间隔 | ≥ 0.5 |
| `/email text [字符数]` | 设置字符上限 | ≥ 10 |

```bash
/email interval 5    # 每5秒检查一次
/email text 100      # 内容最多显示100字符
```

### 调试指令

| 指令 | 说明 |
|:-----|:-----|
| `/email debug` | 查看详细调试信息 |
| `/email reinit` | 手动重建所有连接 |
| `/email refresh` | 刷新账号缓存 |

## ⚙️ 配置指南

### 获取授权码/应用密码

<details>
<summary><b>📧 QQ邮箱</b></summary>

1. 登录 [QQ邮箱](https://mail.qq.com)
2. 进入 **设置** → **账户**
3. 找到 **POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务**
4. 开启 **IMAP/SMTP服务**
5. 按提示生成 **授权码**

</details>

<details>
<summary><b>📧 Gmail</b></summary>

1. 登录 [Google账户](https://myaccount.google.com)
2. 进入 **安全性** → **两步验证**（需要先开启）
3. 找到 **应用专用密码**
4. 选择应用类型，生成密码

</details>

<details>
<summary><b>📧 163/126邮箱</b></summary>

1. 登录邮箱网页版
2. 进入 **设置** → **POP3/SMTP/IMAP**
3. 开启 **IMAP/SMTP服务**
4. 按提示设置 **客户端授权密码**

</details>

<details>
<summary><b>📧 Outlook/Hotmail</b></summary>

1. 登录 [Microsoft账户](https://account.microsoft.com)
2. 进入 **安全性** → **高级安全选项**
3. 添加新的登录方式或使用账户密码
4. IMAP服务器: `imap-mail.outlook.com`

</details>

### 配置参数说明

| 参数 | 默认值 | 说明 |
|:-----|:-------|:-----|
| `interval` | 3 | 邮件检查间隔（秒），最小 0.5 |
| `text_num` | 50 | 内容字符上限，最小 10 |
| `accounts` | [] | 邮箱账号列表 |
| `active_targets` | [] | 活跃推送目标（自动管理） |

## 🏗️ 技术架构

```
EmailNotixion
├── main.py          # 主插件逻辑
│   ├── Config       # 配置常量
│   ├── LogLevel     # 日志级别
│   ├── AccountCache # 账号缓存
│   └── EmailNotixion# 主插件类
│
└── xmail.py         # 邮件模块
    ├── EmailConfig  # 邮件配置常量
    └── EmailNotifier# 邮件通知器
```

### 核心特性

- **异步架构**: 使用 `asyncio.to_thread()` 包装同步 IMAP 操作
- **缓存机制**: 账号有效性缓存 5 分钟，避免频繁测试连接
- **自动重建**: 每 2 分钟重建所有连接，确保长期稳定
- **状态持久化**: 推送目标保存到配置，重载后自动恢复

## ❓ 常见问题

<details>
<summary><b>Q: 收不到邮件推送？</b></summary>

请检查：
1. 是否已执行 `/email on` 开启推送
2. 使用 `/email list` 检查账号状态
3. 确认授权码/应用密码是否正确
4. 确认 IMAP 服务是否已开启

</details>

<details>
<summary><b>Q: 添加账号失败？</b></summary>

常见原因：
- 格式错误：正确格式为 `IMAP服务器,邮箱,密码`
- 使用了登录密码而非授权码
- IMAP 服务未开启
- 网络连接问题

</details>

<details>
<summary><b>Q: 推送延迟很高？</b></summary>

可以调整检查间隔：
```
/email interval 1
```
注意：间隔过短可能被邮件服务器限制

</details>

<details>
<summary><b>Q: 插件重载后推送失效？</b></summary>

这是正常现象。在之前配置过推送的会话中发送任意消息，推送会自动恢复。

</details>

<details>
<summary><b>Q: 支持哪些邮箱？</b></summary>

支持所有提供 IMAP 服务的邮箱，包括但不限于：
- Gmail、QQ邮箱、163/126邮箱
- Outlook、Hotmail、Yahoo
- 企业邮箱、自建邮件服务器

</details>

## ⚠️ 安全提示

> **重要**: 本插件将邮箱密码以明文形式存储在配置文件中

**安全建议**：
- ✅ 使用应用专用密码/授权码，**不要使用登录密码**
- ✅ 定期更换授权码
- ✅ 保护好 AstrBot 的配置文件
- ✅ 及时清理不需要的账号

## 📝 更新日志

### v1.1.0 (2025-11)

🎉 **重大版本更新**

#### 新增功能
- ✨ 智能账号缓存 - 避免频繁连接测试，提升响应速度
- ✨ `/email refresh` - 手动刷新账号缓存
- ✨ 添加账号时自动验证连接

#### 问题修复
- 🐛 修复重复的方法定义问题
- 🐛 修复连接泄漏问题，添加 `cleanup()` 方法
- 🐛 优化时间判断逻辑，避免时区问题

#### 代码优化
- ♻️ 引入配置常量类，消除魔法数字
- ♻️ 统一日志系统，使用 `LogLevel` 枚举
- ♻️ 改进异常处理，区分不同错误类型
- ♻️ 移除死代码（未使用的方法）
- ♻️ 优化账号解析逻辑

#### 文档更新
- 📚 全新 README，采用优秀开源项目风格
- 📚 详细的配置说明和示例
- 📚 完善的常见问题解答

<details>
<summary>查看历史版本</summary>

### v1.0.8
- 智能新邮件检测：未读且2分钟内
- 防重复推送机制
- 状态持久化优化

### v1.0.7
- 简化连接管理，移除复杂健康检查
- 定时重建策略，每2分钟重建连接
- 超时控制优化

### v1.0.6
- 智能账号检测
- 代码结构优化
- 指令系统改进

### v1.0.5
- 智能状态恢复机制
- Event对象智能记录
- 后台管理支持

### v1.0.4
- 跨消息平台兼容性优化
- 统一使用 event.send() 方法
- 多账号并发处理

</details>

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源许可证。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

<div align="center">

**Made with ❤️ by [Temmie](https://github.com/OlyMarco)**

如果这个项目对你有帮助，请给个 ⭐ Star 支持一下！

</div>
