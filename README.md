# TTS文本过滤插件 - 双语TTS扩展版

基于 [柯尔](https://github.com/Luna-channel) 的 [astrbot_plugin_tts_sanitizer](https://github.com/Luna-channel/astrbot_plugin_tts_sanitizer) 修改，新增双语TTS功能。

原插件通过透明包装 TTS Provider 过滤不适合语音朗读的内容（颜文字、特殊符号、网络用语等），不修改消息链，不产生额外消息。详细说明请参阅原项目。

## 🆕 新增功能：双语TTS

**核心效果**：文字显示中文，语音朗读其他语言（英语/日语/韩语等，可配置）。

### 工作原理

1. 插件通过 `on_llm_request` 钩子自动向 system prompt 注入翻译规则
2. 模型回复时在末尾附带 `«TTS»...«/TTS»` 标签包裹的翻译内容
3. `on_llm_response` 阶段提取翻译内容并缓存，同时从显示文本中删除标签
4. TTS Provider 包装层使用缓存的翻译内容生成语音

```
模型输出：今天心情很好呢~
         «TTS»
         I'm in a great mood today~
         «/TTS»

用户看到：今天心情很好呢~            ← 纯中文文字
语音朗读：I'm in a great mood today~  ← 英文语音
```

### 新增配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `bilingual_tts` | 双语TTS总开关 | `false` |
| `tts_language` | TTS朗读语言 | `English` |
| `bilingual_prompt` | 提示词模板（`{language}` 会被替换为语言名） | 内置默认模板 |

`tts_language` 可以填任何语言名，例如：`English`、`Japanese`、`Korean`、`French`、`Spanish`、`German` 等。

### 使用方法

1. 安装插件
2. 在配置面板中开启 `bilingual_tts`
3. 设置 `tts_language` 为你想要的朗读语言
4. **重启 AstrBot**（首次启用或更新代码后必须完全重启，热重载不够）
5. 开启 `debug_mode` 可在日志中查看详细的提取和缓存过程

### 注意事项

- 首次启用或更新插件代码后，需要**完全重启 AstrBot**，不能仅热重载插件，否则 TTS Provider 包装不会更新
- 关闭 `bilingual_tts` 后行为与原版完全一致，向后兼容
- `«TTS»` 标签使用了罕见的法文引号符号 `«»`，避免与日常对话内容冲突
- 提示词模板可自定义，需保留 `{language}` 占位符和 `«TTS»`/`«/TTS»` 标签格式

## 原版功能

原版的所有功能均保留，包括：颜文字过滤、特殊符号清理、智能替换、重复字符压缩、超长文本保护、Live Mode 支持、干净卸载等。命令 `/tts_filter_test`、`/tts_filter_stats`、`/tts_filter_reload` 均可正常使用。

详见原项目 [README](https://github.com/Luna-channel/astrbot_plugin_tts_sanitizer)。

## 致谢

感谢 [柯尔](https://github.com/Luna-channel) 开发的原版 TTS 文本过滤插件，本项目基于其 v1.0.1 版本修改。

## 📝 许可证

AGPL-3.0 License - 与原项目保持一致。
