from src.plugin_system.base.plugin_metadata import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="Whisper本地语音识别",
    description="通过OpenAI Whisper模型提供本地语音转文字的功能",
    usage="在 bot_config.toml 中将 asr_provider 设置为 'local' 即可启用",
    version="0.1.0",
    author="Elysia",
)