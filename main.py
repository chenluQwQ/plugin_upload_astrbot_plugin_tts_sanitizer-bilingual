from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
import astrbot.api.message_components as Comp
import asyncio
import re
from typing import Optional, Dict, Any

# 双语 TTS 标签正则：匹配 «TTS»...«/TTS»（支持换行，兼容旧版 Prompt 注入残留）
EN_TAG_PATTERN = re.compile(r'\s*«TTS»\s*(.*?)\s*«/TTS»', re.DOTALL)

# 内置默认值
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


@register(
    "tts_sanitizer_bilingual", "柠弥", "TTS文本过滤插件 - 支持双语TTS和语音Tool，基于柯尔的tts_sanitizer扩展", "1.3.0"
)
class TTSSanitizerPlugin(Star):
    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)

        if isinstance(config, AstrBotConfig):
            self.config = config
        else:
            self.config = self._get_default_config()

        self._compile_patterns()
        self._wrapped_providers: list = []

    def _get_default_config(self) -> Dict[str, Any]:
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

    def _has_translate_api(self) -> bool:
        """检查是否配置了独立翻译 API"""
        return bool(self.config.get("translate_api_key", ""))

    async def initialize(self):
        bilingual = self.config.get('bilingual_tts', False)
        has_api = self._has_translate_api()
        speak_tool = self.config.get('enable_speak_tool', False)
        logger.info(
            f"TTS文本过滤插件 v1.3.0 已启动 - 双语: {bilingual}, 翻译API: {has_api}, 语音Tool: {speak_tool}"
        )
        try:
            providers = self.context.get_all_tts_providers()
            if providers:
                self._wrap_all_providers()
        except Exception:
            pass

    # =========================================================================
    # 语音 Tool：让模型主动发语音
    # =========================================================================

    @filter.llm_tool(name="speak")
    async def speak_tool(self, event: AstrMessageEvent, text: str) -> MessageEventResult:
        '''发送一条语音消息。当用户要求你"说一句话"、"用声音/语音回答"、"念出来"、"读给我听"等需要语音输出的场景时调用此工具。

        Args:
            text(string): 要朗读的文本内容
        '''
        if not self.config.get("enable_speak_tool", False):
            yield event.plain_result(text)
            return

        try:
            providers = self.context.get_all_tts_providers()
            if not providers:
                logger.warning("🎤 speak tool: 没有可用的 TTS Provider")
                yield event.plain_result(text)
                return

            provider = providers[0]
            # 调用 get_audio（已被我们包装，会自动翻译/过滤）
            audio_path = await provider.get_audio(text)

            if audio_path:
                yield event.chain_result([Comp.Record(file=audio_path, url=audio_path)])
            else:
                yield event.plain_result(text)

        except Exception as e:
            logger.warning(f"🎤 speak tool 失败: {e}")
            yield event.plain_result(text)

    # =========================================================================
    # 核心：TTS Provider 包装
    # =========================================================================

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        logger.info("TTS过滤: on_astrbot_loaded 钩子被触发")
        self._wrap_all_providers()
        self._register_provider_change_hook()

    def _register_provider_change_hook(self):
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
        if getattr(provider, '_tts_bilingual_wrapped', False):
            return False

        original_get_audio = provider.get_audio
        plugin = self

        async def wrapped_get_audio(text: str) -> str:
            debug_mode = plugin.config.get('debug_mode', False)
            if debug_mode:
                logger.debug(f"TTS过滤: 原文: {text[:50]}...")

            if not plugin.config.get('enabled', True) or not text:
                return await original_get_audio(text)

            # 清理可能残留的 «TTS» 标签
            text = EN_TAG_PATTERN.sub("", text).strip()
            if not text:
                return await original_get_audio("")

            # === 双语模式：调翻译 API ===
            if plugin.config.get('bilingual_tts', False) and plugin._has_translate_api():
                try:
                    translated = await plugin._translate_text(text)
                    if translated:
                        if debug_mode:
                            logger.info(f"🌐 双语TTS: '{text[:30]}...' → '{translated[:30]}...'")
                        filtered = plugin._apply_filters(translated)
                        if filtered.strip():
                            return await original_get_audio(filtered)
                except Exception as e:
                    logger.warning(f"🌐 双语TTS: 翻译失败，降级为中文: {e}")

            # === 普通模式：过滤后朗读 ===
            max_len = plugin.config.get('max_length', 200)
            if max_len > 0 and len(text) > max_len:
                if debug_mode:
                    logger.info(f"🚫 TTS过滤: 文本 {len(text)} 字超过限制 {max_len}，跳过")
                return await original_get_audio("")

            filtered = plugin._apply_filters(text)
            if debug_mode and filtered != text:
                logger.info(f"🔧 TTS过滤: '{text[:30]}...' → '{filtered[:30]}...'")
            if not filtered.strip():
                return await original_get_audio("")
            return await original_get_audio(filtered)

        provider.get_audio = wrapped_get_audio
        provider._tts_bilingual_wrapped = True
        provider._tts_bilingual_original_get_audio = original_get_audio

        if provider.support_stream():
            self._wrap_provider_stream(provider)

        self._wrapped_providers.append(provider)
        return True

    def _wrap_provider_stream(self, provider):
        original_get_audio_stream = provider.get_audio_stream
        plugin = self

        async def wrapped_get_audio_stream(
            text_queue: "asyncio.Queue[str | None]",
            audio_queue: "asyncio.Queue[bytes | tuple[str, bytes] | None]",
        ) -> None:
            filtered_queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def filter_worker():
                while True:
                    text = await text_queue.get()
                    if text is None:
                        await filtered_queue.put(None)
                        break
                    if not plugin.config.get('enabled', True):
                        await filtered_queue.put(text)
                        continue
                    filtered = plugin._apply_filters(text)
                    if filtered.strip():
                        await filtered_queue.put(filtered)

            filter_task = asyncio.create_task(filter_worker())
            try:
                await original_get_audio_stream(filtered_queue, audio_queue)
            finally:
                if not filter_task.done():
                    filter_task.cancel()

        provider.get_audio_stream = wrapped_get_audio_stream
        provider._tts_bilingual_original_get_audio_stream = original_get_audio_stream

    def _unwrap_all_providers(self):
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
    # 独立翻译 API 调用
    # =========================================================================

    async def _translate_text(self, text: str) -> Optional[str]:
        """调用 OpenAI 兼容 API 翻译文本"""
        api_key = self.config.get("translate_api_key", "")
        api_base = self.config.get("translate_api_base", "https://api.openai.com/v1").rstrip("/")
        model = self.config.get("translate_model", "gpt-4o-mini")
        language = self.config.get("tts_language", "English")

        if not api_key:
            return None

        url = f"{api_base}/chat/completions"

        import aiohttp
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"You are a translator. Translate the following text to {language}. "
                        f"Keep the same tone and emotion. Output ONLY the translation, nothing else. "
                        f"Do not include any emoticons, Chinese characters, or explanations."
                    )
                },
                {"role": "user", "content": text}
            ],
            "max_tokens": 500,
            "temperature": 0.3,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.warning(f"🌐 翻译API错误 {resp.status}: {error_text[:200]}")
                    return None
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()

    # =========================================================================
    # 过滤逻辑
    # =========================================================================

    def _compile_patterns(self):
        try:
            patterns = self.config.get("remove_patterns", DEFAULT_REMOVE_PATTERNS)
            self.remove_regex = [re.compile(p) for p in patterns]
            count = self.config.get("max_repeat_count", 2)
            self.repeat_regex = re.compile(f"(.)\\1{{{count},}}") if count > 0 else None
            self.replacements = self._parse_replacements()
        except Exception as e:
            logger.warning(f"编译配置失败: {e}")
            self.remove_regex = []
            self.repeat_regex = None
            self.replacements = {}

    def _parse_replacements(self):
        replacements = {}
        for item in self.config.get("replacement_words", DEFAULT_REPLACEMENTS):
            if isinstance(item, str) and "|" in item:
                try:
                    original, replacement = item.split("|", 1)
                    if original.strip() and replacement.strip():
                        replacements[original.strip()] = replacement.strip()
                except ValueError:
                    pass
        return replacements

    def filter_text(self, text: str) -> str:
        if not text:
            return ""
        return self._apply_filters(text)

    def _apply_filters(self, text: str) -> str:
        max_processing_length = self.config.get("max_processing_length", 10000)
        if not text or len(text) > max_processing_length:
            return ""

        # 清理 «TTS» 标签残留
        text = EN_TAG_PATTERN.sub("", text)

        for regex in self.remove_regex:
            text = regex.sub("", text)

        for word in self.config.get("filter_words", DEFAULT_FILTER_WORDS):
            text = text.replace(word, "")

        for original, replacement in self.replacements.items():
            text = text.replace(original, replacement)

        if self.repeat_regex:
            count = self.config.get("max_repeat_count", 2)
            text = self.repeat_regex.sub(lambda m: m.group(1) * count, text)

        text = re.sub(r'["""\u201c\u201d]\s*["""\u201c\u201d]', '', text)
        text = re.sub(r"['''\u2018\u2019]\s*['''\u2018\u2019]", '', text)
        text = re.sub(r'[「」『』【】\[\]]\s*[「」『』【】\[\]]', '', text)

        text = re.sub(r'[,，、;；]\s*(?=[,，、;；\s])', '', text)
        text = re.sub(r'[,，、;；]\s*$', '', text)
        text = re.sub(r'^\s*[,，、;；]\s*', '', text)

        text = re.sub(r"\s+", " ", text).strip()

        if self.config.get("tts_pause_markers", False):
            text = text.replace("\n", "<#2#>")
            text = re.sub(r'([。？！?!])', r'\1<#2#>', text)
            text = re.sub(r'([，,、;；])', r'\1<#1#>', text)
            text = re.sub(r'([…—]+)', r'\1<#2#>', text)
            text = re.sub(r'(<#\d#>){2,}', lambda m: m.group(0)[-5:], text)

        return text

    def should_skip_tts(self, text: str) -> bool:
        max_len = self.config.get("max_length", 200)
        return not text.strip() or (max_len > 0 and len(text) > max_len)

    # =========================================================================
    # 命令
    # =========================================================================

    @filter.command("tts_bi_test")
    async def test_filter(self, event: AstrMessageEvent):
        full_msg = event.message_str.strip()
        for cmd in ["/tts_bi_test", "tts_bi_test"]:
            if full_msg.startswith(cmd):
                user_input = full_msg[len(cmd):].strip()
                break
        else:
            user_input = full_msg

        if not user_input:
            yield event.plain_result("请输入测试文本，例如：\n/tts_bi_test 你好(＾_＾)测试233")
            return

        filtered = self.filter_text(user_input)
        skip = self.should_skip_tts(filtered)

        result = f"""📝 原文 ({len(user_input)} 字符):
{user_input}

🔧 过滤后 ({len(filtered)} 字符):
{filtered or "(空文本)"}

📊 TTS状态: {"❌ 跳过" if skip else "✅ 可朗读"}"""

        yield event.plain_result(result)

    @filter.command("tts_bi_stats")
    async def show_stats(self, event: AstrMessageEvent):
        wrapped_count = len(self._wrapped_providers)
        has_api = self._has_translate_api()
        model = self.config.get("translate_model", "gpt-4o-mini") if has_api else "N/A"

        result = f"""📊 TTS过滤插件 v1.3.0

• 启用: {"✅" if self.config.get("enabled", True) else "❌"}
• 双语TTS: {"✅ (" + self.config.get("tts_language", "English") + ")" if self.config.get("bilingual_tts", False) else "❌"}
• 翻译API: {"✅ " + model if has_api else "❌ 未配置"}
• 语音Tool: {"✅" if self.config.get("enable_speak_tool", False) else "❌"}
• 停顿标记: {"✅" if self.config.get("tts_pause_markers", False) else "❌"}
• 已包装 Provider: {wrapped_count} 个"""

        yield event.plain_result(result)

    @filter.command("tts_bi_reload")
    async def reload_config(self, event: AstrMessageEvent):
        try:
            self._compile_patterns()
            self._wrap_all_providers()

            lang = self.config.get("tts_language", "English")
            bilingual = self.config.get("bilingual_tts", False)
            has_api = self._has_translate_api()
            model = self.config.get("translate_model", "gpt-4o-mini") if has_api else "N/A"
            speak = self.config.get("enable_speak_tool", False)
            yield event.plain_result(
                f"✅ 配置已重新加载\n"
                f"• 双语TTS: {'✅ (' + lang + ')' if bilingual else '❌'}\n"
                f"• 翻译API: {'✅ ' + model if has_api else '❌'}\n"
                f"• 语音Tool: {'✅' if speak else '❌'}\n"
                f"• 已包装 {len(self._wrapped_providers)} 个 Provider"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 重新加载失败: {e}")

    async def terminate(self):
        self._unwrap_all_providers()
        logger.info("TTS过滤插件已停止，所有 TTS Provider 已恢复原始状态")
