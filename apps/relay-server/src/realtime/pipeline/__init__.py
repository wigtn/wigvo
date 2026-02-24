"""Pipeline 모듈 — CommunicationMode별 오디오 흐름 전략.

Mode → Pipeline 매핑:
  VOICE_TO_VOICE → VoiceToVoicePipeline
  TEXT_TO_VOICE  → TextToVoicePipeline
  FULL_AGENT     → FullAgentPipeline
"""
