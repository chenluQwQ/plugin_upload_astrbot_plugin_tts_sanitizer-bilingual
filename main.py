from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
import asyncio
import re
from typing import Optional, Dict, Any

# 双语 TTS 标签正则：匹配 «TTS»...«/TTS»（支持换行）
EN_TAG_PATTERN = re.compile(r'\s*«TTS»\s*(.*?)\s*«/TTS»', re.DOTALL)

# 内置默认值（与 _conf_schema.json 保持一致）
DEFAULT_REMOVE_PATTERNS = [
    r"[（(][^（()]*[）)]",
    r"[＞>][＿_][＜<]",
    r"[＾^][＿_][＾^]",
    r"[oO][＿_][oO]",
    r"[xX][＿_][xX]",
    r"[－-][＿_][－-]",
    r"[★☆♪♫♬♩♡♥❤️💖💕💗💓💝💟💜💛💚💙🧡🤍🖤🤎💔❣️💋]",
    r"[→←↑↓↖↗↘↙↔↕↺↻]",
]

DEFAULT_FILTER_WORDS = [
    "ω", "Ω", "σ", "Σ", "ε", "д", "Д",
    "´", "`", "＝", "∀", "∇",
    "orz", "OTZ", "QAQ", "QWQ", "TAT", "TUT", "www",
]

DEFAULT_REPLACEMENTS = ["233|哈哈哈", "666|厉害", "999|很棒", "555|呜呜呜"]

# 默认双语 TTS 提示词模板，{language} 会被替换为配置的语言
DEFAULT_BILINGUAL_PROMPT = """【语音朗读规则】
每次回复时，在回复的最末尾另起一行，用 «TTS» 和 «/TTS» 标签附上你回复内容的{language}翻译版本。
格式示例：
你的中文回复内容
«TTS»
Your translated content here
«/TTS»

要求：
- «TTS» 标签必须放在回复的最末尾，与正文之间换行分隔
- 翻译内容应保持与中文原文相同的语气和情感
- 不要在翻译中包含颜文字或中文字符
- 不要解释或提及这个标签的存在"""


@register(
    "tts_sanitizer_bilingual", "柠弥", "TTS文本过滤插件 - 支持双语TTS，基于柯尔的tts_sanitizer扩展", "1.1.0"
)
class TTSSanitizerPlugin(Star):
    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)

        if isinstance(config, AstrBotConfig):
            self.config = config
        else:
            self.config = self._get_default_config()

        self._compile_patterns()
        # 记录已包装的 provider，用于卸载时恢复
        self._wrapped_providers: list = []
        # 双语 TTS 缓存：on_decorating_result 提取后暂存，供 TTS 包装层读取
        self._pending_tts_text: Optional[str] = None

    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "enabled": True,
            "max_length": 200,
            "max_processing_length": 10000,
            "remove_patterns": DEFAULT_REMOVE_PATTERNS,
            "filter_words": DEFAULT_FILTER_WORDS,
            "replacement_words": DEFAULT_REPLACEMENTS,
            "max_repeat_count": 2,
            "debug_mode": False,
        }

    async def initialize(self):
        """异步插件初始化方法"""
        logger.info(
            f"TTS文本过滤插件 v1.1.0 已启动 - 最大字数: {self.config.get('max_length', 200)}, 双语TTS: {self.config.get('bilingual_tts', False)}"
        )
        logger.info(
            f"当前配置: 启用={self.config.get('enabled', True)}, 调试模式={self.config.get('debug_mode', False)}"
        )
        logger.info(
            "📢 工作模式: 透明包装 TTS Provider，不修改消息链，不产生额外消息"
        )
        # 热重载时也尝试包装 Provider
        try:
            providers = self.context.get_all_tts_providers()
            if providers:
                self._wrap_all_providers()
        except Exception:
            pass  # 首次启动时 Provider 可能还没加载，由 on_astrbot_loaded 处理

    # =========================================================================
    # 双语 TTS：LLM 请求前注入提示词
    # =========================================================================

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """在 LLM 请求前注入双语 TTS 提示词"""
        if not self.config.get("bilingual_tts", False):
            return
        if not self.config.get("enabled", True):
            return

        language = self.config.get("tts_language", "English")
        prompt_template = self.config.get("bilingual_prompt", "") or DEFAULT_BILINGUAL_PROMPT
        prompt = prompt_template.replace("{language}", language)

        req.system_prompt += "\n\n" + prompt

        if self.config.get('debug_mode', False):
            logger.info(f"🌐 双语TTS: 已注入 {language} 提示词到 system_prompt")

    # =========================================================================
    # 核心：TTS Provider 包装
    # =========================================================================

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """AstrBot 加载完成后，包装所有 TTS Provider 的 get_audio 方法"""
        logger.info("TTS过滤: on_astrbot_loaded 钩子被触发")
        self._wrap_all_providers()
        self._register_provider_change_hook()

    def _register_provider_change_hook(self):
        """注册 Provider 变更钩子，当 Provider 重载时自动重新包装"""
        try:
            from astrbot.core.provider.entities import ProviderType
            pm = self.context.provider_manager
            def _on_provider_change(provider_id: str, provider_type, umo):
                if provider_type == ProviderType.TEXT_TO_SPEECH:
                    logger.info(f"TTS过滤: 检测到 TTS Provider 变更({provider_id})，重新包装...")
                    self._wrap_all_providers()
            pm.register_provider_change_hook(_on_provider_change)
            logger.info("TTS过滤: 已注册 Provider 变更钩子")
        except Exception as e:
            logger.warning(f"TTS过滤: 注册 Provider 变更钩子失败: {e}")

    def _wrap_all_providers(self):
        """包装所有 TTS Provider（热重载时先解包再重新包装）"""
        # 先解包所有已包装的 Provider，确保热重载时闭包会更新
        self._unwrap_all_providers()

        logger.info("TTS过滤: 开始包装 TTS Provider...")
        try:
            providers = self.context.get_all_tts_providers()
            logger.info(f"TTS过滤: 获取到 {len(providers) if providers else 0} 个 TTS Provider")
        except Exception as e:
            logger.warning(f"TTS过滤: 获取 TTS Provider 失败: {e}")
            return

        if not providers:
            logger.warning("TTS过滤: 未发现 TTS Provider，包装将不会生效！")
            return

        wrapped_count = 0
        for provider in providers:
            if self._wrap_provider(provider):
                wrapped_count += 1

        if wrapped_count > 0:
            logger.info(f"TTS过滤: 已包装 {wrapped_count} 个 TTS Provider")
        else:
            logger.warning("TTS过滤: 未能包装任何 Provider")

    def _wrap_provider(self, provider) -> bool:
        """包装单个 TTS Provider，返回是否成功包装"""
        if getattr(provider, '_tts_bilingual_wrapped', False):
            return False

        original_get_audio = provider.get_audio
        plugin = self

        async def wrapped_get_audio(text: str) -> str:
            debug_mode = plugin.config.get('debug_mode', False)
            if debug_mode:
                logger.debug(f"TTS过滤: 包装函数被调用，原文: {text[:50]}...")
            
            if not plugin.config.get('enabled', True) or not text:
                return await original_get_audio(text)

            # 双语模式：优先使用 on_decorating_result 缓存的翻译内容
            if plugin.config.get('bilingual_tts', False) and plugin._pending_tts_text:
                tts_text = plugin._pending_tts_text
                plugin._pending_tts_text = None  # 用完即清
                if debug_mode:
                    logger.info(f"🌐 双语TTS: 使用缓存内容朗读 '{tts_text[:50]}...'")
                filtered = plugin._apply_filters(tts_text)
                if not filtered.strip():
                    return await original_get_audio("")
                return await original_get_audio(filtered)

            # 超过最大朗读字数则跳过（返回空，TTS Provider 自行处理）
            max_len = plugin.config.get('max_length', 200)
            if max_len > 0 and len(text) > max_len:
                if debug_mode:
                    logger.info(f"🚫 TTS过滤: 文本 {len(text)} 字超过限制 {max_len}，跳过朗读")
                return await original_get_audio("")

            filtered = plugin.filter_text(text)

            if filtered != text:
                logger.info(f"🔧 TTS过滤: '{text[:30]}...' → '{filtered[:30]}...'")

            if not filtered.strip():
                return await original_get_audio("")

            return await original_get_audio(filtered)

        provider.get_audio = wrapped_get_audio
        provider._tts_bilingual_wrapped = True
        provider._tts_bilingual_original_get_audio = original_get_audio

        # 包装 get_audio_stream（Live Mode 支持）
        if provider.support_stream():
            self._wrap_provider_stream(provider)

        self._wrapped_providers.append(provider)
        return True

    def _wrap_provider_stream(self, provider):
        """包装 TTS Provider 的流式 get_audio_stream 方法（Live Mode）"""
        original_get_audio_stream = provider.get_audio_stream
        plugin = self

        async def wrapped_get_audio_stream(
            text_queue: "asyncio.Queue[str | None]",
            audio_queue: "asyncio.Queue[bytes | tuple[str, bytes] | None]",
        ) -> None:
            # 创建过滤中间队列
            filtered_queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def filter_worker():
                """从 text_queue 读取文本，过滤后放入 filtered_queue"""
                while True:
                    text = await text_queue.get()
                    if text is None:
                        await filtered_queue.put(None)
                        break
                    if not plugin.config.get('enabled', True):
                        await filtered_queue.put(text)
                        continue
                    filtered = plugin.filter_text(text)
                    if plugin.config.get('debug_mode', False) and filtered != text:
                        logger.info(
                            f"🔧 TTS流式过滤: '{text[:30]}' → '{filtered[:30]}'"
                        )
                    if filtered.strip():
                        await filtered_queue.put(filtered)
                    # 过滤后为空的段直接丢弃

            # 启动过滤 worker
            filter_task = asyncio.create_task(filter_worker())
            try:
                await original_get_audio_stream(filtered_queue, audio_queue)
            finally:
                if not filter_task.done():
                    filter_task.cancel()

        provider.get_audio_stream = wrapped_get_audio_stream
        provider._tts_bilingual_original_get_audio_stream = original_get_audio_stream

    def _unwrap_all_providers(self):
        """恢复所有被包装的 TTS Provider"""
        restored_count = 0
        for provider in self._wrapped_providers:
            if hasattr(provider, '_tts_bilingual_original_get_audio'):
                provider.get_audio = provider._tts_bilingual_original_get_audio
                del provider._tts_bilingual_original_get_audio

            if hasattr(provider, '_tts_bilingual_original_get_audio_stream'):
                provider.get_audio_stream = provider._tts_bilingual_original_get_audio_stream
                del provider._tts_bilingual_original_get_audio_stream

            if hasattr(provider, '_tts_bilingual_wrapped'):
                del provider._tts_bilingual_wrapped

            restored_count += 1

        self._wrapped_providers.clear()
        if restored_count > 0:
            logger.info(f"TTS过滤: 已恢复 {restored_count} 个 TTS Provider")

    # =========================================================================
    # 双语 TTS：LLM 响应后提取翻译内容并缓存，同时从文本中去掉标签
    # =========================================================================

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """从 LLM 响应中提取 «TTS» 内容并缓存，同时去掉标签让用户只看到中文"""
        if not self.config.get("bilingual_tts", False):
            return
        if not resp.result_chain:
            return
        for seg in resp.result_chain.chain:
            if hasattr(seg, 'text') and seg.text:
                en_match = EN_TAG_PATTERN.search(seg.text)
                if en_match:
                    # 先提取并缓存 TTS 朗读内容
                    self._pending_tts_text = en_match.group(1).strip()
                    if self.config.get('debug_mode', False):
                        logger.info(f"🌐 双语TTS: 缓存朗读内容 '{self._pending_tts_text[:50]}...'")
                    # 再从显示文本中删掉标签
                    seg.text = EN_TAG_PATTERN.sub('', seg.text).strip()

    # =========================================================================
    # 过滤逻辑（保持不变）
    # =========================================================================

    def _compile_patterns(self):
        """编译正则表达式和解析替换配置"""
        try:
            # 编译正则过滤规则（合并后的 remove_patterns）
            patterns = self.config.get("remove_patterns", DEFAULT_REMOVE_PATTERNS)
            self.remove_regex = [re.compile(p) for p in patterns]

            # 编译重复字符压缩（0=关闭）
            count = self.config.get("max_repeat_count", 2)
            if count > 0:
                self.repeat_regex = re.compile(f"(.)\\1{{{count},}}")
            else:
                self.repeat_regex = None

            self.replacements = self._parse_replacements()

        except Exception as e:
            logger.warning(f"编译配置失败: {e}")
            self.remove_regex = []
            self.repeat_regex = None
            self.replacements = {}

    def _parse_replacements(self):
        """解析替换词汇配置"""
        replacements = {}
        replacement_list = self.config.get("replacement_words", DEFAULT_REPLACEMENTS)

        for item in replacement_list:
            if isinstance(item, str) and "|" in item:
                try:
                    original, replacement = item.split("|", 1)
                    original = original.strip()
                    replacement = replacement.strip()
                    if original and replacement:
                        replacements[original] = replacement
                except ValueError:
                    logger.warning(f"无效的替换配置格式: {item}")

        return replacements

    def filter_text(self, text: str) -> str:
        """过滤文本 - 如果有 «TTS» 标签则提取翻译内容用于 TTS"""
        if not text:
            return ""

        # 双语模式：检查是否有 «TTS»...«/TTS» 标签
        if self.config.get("bilingual_tts", False):
            en_match = EN_TAG_PATTERN.search(text)
            if en_match:
                en_text = en_match.group(1).strip()
                if self.config.get('debug_mode', False):
                    logger.info(f"🌐 双语TTS: 提取朗读内容 '{en_text[:50]}...'")
                # 对提取的内容也跑一遍过滤（去颜文字等）
                return self._apply_filters(en_text)

        # 没有 «TTS» 标签或未启用双语 → 走原来的过滤逻辑
        return self._apply_filters(text)

    def _apply_filters(self, text: str) -> str:
        """原有的过滤逻辑"""
        max_processing_length = self.config.get("max_processing_length", 10000)
        if not text or len(text) > max_processing_length:
            return ""

        # 0. 先去掉 «TTS»...«/TTS» 标签（防止标签本身被朗读）
        text = EN_TAG_PATTERN.sub("", text)

        # 1. 正则过滤（颜文字、括号内容、特殊符号等）
        for regex in self.remove_regex:
            text = regex.sub("", text)

        # 2. 直接过滤的字符和词汇（omega、颜文字用字、网络用语等）
        filter_words = self.config.get("filter_words", DEFAULT_FILTER_WORDS)
        for word in filter_words:
            text = text.replace(word, "")

        # 3. 替换词汇（替换为其他内容）
        for original, replacement in self.replacements.items():
            text = text.replace(original, replacement)

        # 4. 重复字符压缩
        if self.repeat_regex:
            count = self.config.get("max_repeat_count", 2)
            text = self.repeat_regex.sub(lambda m: m.group(1) * count, text)

        # 5. 清理空引号对（过滤内容后残留的 ""、''、「」、""、'' 等）
        text = re.sub(r'["""\u201c\u201d]\s*["""\u201c\u201d]', '', text)
        text = re.sub(r"['''\u2018\u2019]\s*['''\u2018\u2019]", '', text)
        text = re.sub(r'[「」『』【】\[\]]\s*[「」『』【】\[\]]', '', text)

        # 6. 清理残留标点（连续逗号/顿号、开头结尾的标点等）
        text = re.sub(r'[,，、;；]\s*(?=[,，、;；\s])', '', text)
        text = re.sub(r'[,，、;；]\s*$', '', text)
        text = re.sub(r'^\s*[,，、;；]\s*', '', text)

        # 7. 清理多余空格
        text = re.sub(r"\s+", " ", text).strip()

        # 8. MiniMax TTS 停顿标记（仅在启用时）
        if self.config.get("tts_pause_markers", False):
            # 换行 → 停顿2拍
            text = text.replace("\n", "<#2#>")
            # 句号、问号、感叹号 → 停顿2拍
            text = re.sub(r'([。？！?!])', r'\1<#2#>', text)
            # 逗号、顿号、分号 → 停顿1拍
            text = re.sub(r'([，,、;；])', r'\1<#1#>', text)
            # 省略号、破折号 → 停顿2拍
            text = re.sub(r'([…—]+)', r'\1<#2#>', text)
            # 清理连续停顿标记
            text = re.sub(r'(<#\d#>){2,}', lambda m: m.group(0)[-5:], text)

        return text

    def should_skip_tts(self, text: str) -> bool:
        """检查是否跳过TTS"""
        max_len = self.config.get("max_length", 200)
        return not text.strip() or (max_len > 0 and len(text) > max_len)

    # =========================================================================
    # 命令
    # =========================================================================

    @filter.command("tts_bi_test")
    async def test_filter(self, event: AstrMessageEvent):
        """测试过滤功能"""
        full_msg = event.message_str.strip()

        for cmd in ["/tts_bi_test", "tts_bi_test"]:
            if full_msg.startswith(cmd):
                user_input = full_msg[len(cmd) :].strip()
                break
        else:
            user_input = full_msg

        if not user_input:
            yield event.plain_result(
                "请输入测试文本，例如：\n/tts_bi_test 你好(＾_＾)测试233"
            )
            return

        filtered = self.filter_text(user_input)
        skip = self.should_skip_tts(filtered)

        filter_words = self.config.get("filter_words", DEFAULT_FILTER_WORDS)
        replacements_info = [f"{k}→{v}" for k, v in list(self.replacements.items())[:3]]
        if len(self.replacements) > 3:
            replacements_info.append(f"等{len(self.replacements)}个")

        result = f"""📝 原文 ({len(user_input)} 字符):
{user_input}

🔧 过滤后 ({len(filtered)} 字符):
{filtered or "(空文本)"}

⚙️ 当前配置:
• 正则规则: {len(self.remove_regex)} 条
• 过滤字符/词汇: {len(filter_words)} 个
• 替换规则: {", ".join(replacements_info) if replacements_info else "无"}
• 重复压缩: {"关闭" if not self.repeat_regex else f">{self.config.get('max_repeat_count', 2)}次→{self.config.get('max_repeat_count', 2)}次"}

📊 处理结果:
• 字符压缩率: {round((len(user_input) - len(filtered)) / len(user_input) * 100, 1) if user_input else 0}%
• TTS状态: {"❌ 跳过" if skip else "✅ 可朗读"}"""

        yield event.plain_result(result)

    @filter.command("tts_bi_stats")
    async def show_stats(self, event: AstrMessageEvent):
        """显示插件状态和配置信息"""
        filter_words = self.config.get("filter_words", DEFAULT_FILTER_WORDS)
        replacement_count = len(self.replacements)
        wrapped_count = len(self._wrapped_providers)
        repeat_count = self.config.get("max_repeat_count", 2)

        result = f"""📊 TTS过滤插件状态 v1.1.0

🔧 状态:
• 启用: {"✅" if self.config.get("enabled", True) else "❌"}
• 双语TTS: {"✅ (" + self.config.get("tts_language", "English") + ")" if self.config.get("bilingual_tts", False) else "❌"}
• 最大朗读字数: {self.config.get("max_length", 200)}（0=无限制）
• 调试模式: {"✅" if self.config.get("debug_mode", False) else "❌"}
• 已包装 Provider: {wrapped_count} 个

⚙️ 配置:
• 正则过滤规则: {len(self.remove_regex)} 条
• 过滤字符/词汇: {len(filter_words)} 个
• 替换词汇: {replacement_count} 个
• 重复压缩: {"关闭" if repeat_count == 0 else f">{repeat_count}次→{repeat_count}次"}

💡 工作模式: Provider 透明包装（不修改消息链）"""

        yield event.plain_result(result)

    @filter.command("tts_bi_reload")
    async def reload_config(self, event: AstrMessageEvent):
        """重新加载配置并重新包装 Provider"""
        try:
            self._compile_patterns()
            # 重新包装（确保新配置生效）
            self._wrap_all_providers()

            lang = self.config.get("tts_language", "English")
            bilingual = self.config.get("bilingual_tts", False)
            pause = self.config.get("tts_pause_markers", False)
            yield event.plain_result(
                f"✅ 配置已重新加载\n"
                f"• 双语TTS: {'✅ (' + lang + ')' if bilingual else '❌'}\n"
                f"• 停顿标记: {'✅' if pause else '❌'}\n"
                f"• 已包装 {len(self._wrapped_providers)} 个 Provider\n"
                f"💡 切换语言后如果模型仍输出旧语言，请清空对话上下文"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 重新加载失败: {e}")

    async def terminate(self):
        """插件销毁时恢复所有 TTS Provider"""
        self._unwrap_all_providers()
        logger.info("TTS过滤插件已停止，所有 TTS Provider 已恢复原始状态")
