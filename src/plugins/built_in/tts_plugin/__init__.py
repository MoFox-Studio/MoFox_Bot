from src.plugin_system.base.plugin_metadata import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="文本转语音插件 (Text-to-Speech)",
    description="将文本转换为语音进行播放的插件，支持多种语音模式和智能语音输出场景判断。",
    usage="该插件提供 `tts_action` action。",
    version="0.1.0",
    author="MoFox-Bot团队",
    license="GPL-v3.0-or-later",
    repository_url="https://github.com/MaiM-with-u/maibot",
    keywords=["tts", "voice", "audio", "speech", "accessibility"],
    categories=["Audio Tools", "Accessibility", "Voice Assistant"],
    extra={
        "is_built_in": True,
        "plugin_type": "audio_processor",
    },
)
