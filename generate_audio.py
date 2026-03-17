"""Stub de geração de áudio TTS.

Na v5.5, o TTS é feito pelo ElevenLabs via LiveAvatar (third-party voice).
Este arquivo existe apenas para manter compatibilidade com imports do api_server.py.
"""


async def generate_audio(
    text: str,
    *,
    voice: str = "kore",
    model: str = "gemini_2_5_pro_tts",
) -> bytes:
    raise RuntimeError(
        "generate_audio não disponível neste ambiente. "
        "Na v5.5, o áudio é gerado pelo ElevenLabs via LiveAvatar."
    )


async def generate_dialogue(
    dialogue: list[dict],
    *,
    model: str = "gemini_2_5_pro_tts",
) -> bytes:
    raise RuntimeError(
        "generate_dialogue não disponível neste ambiente. "
        "Na v5.5, o áudio é gerado pelo ElevenLabs via LiveAvatar."
    )
