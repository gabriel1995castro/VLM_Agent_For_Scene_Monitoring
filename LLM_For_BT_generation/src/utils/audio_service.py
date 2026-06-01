import torch
import warnings
import numpy as np
import logging
from kokoro import KPipeline
import soundfile as sf

# Configuração do logger
logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

class AudioManager:
    
    def __init__(self, device: str = "cuda" if torch.cuda.is_available() else "cpu"):
        self.device = device
        self.pipeline = None

    def iniciar_audio(self):
        try:

            logger.info("Carregando pipeline Kokoro...")
            self.pipeline = KPipeline(lang_code='p')
            logger.info("Pipeline Kokoro carregado com sucesso.")

        except Exception as e:

            logger.error(f"Falha ao carregar o pipeline Kokoro: {e}", exc_info=True)
            raise e
        
    def long_form_synthesize(self, text: str, language_code: str = "pt", voice: str = None):
        
        if not self.pipeline:
            raise RuntimeError("O Pipeline de TTS não foi carregado com sucesso.")
        
        language_list = {
            "en": "a",
            "es": "e",
            "pt": "p",}

        voice_list = {"e" : "ef_dora",
                      "jp_f" : "jf_tebukuro",
                      "a" : "af_heart",
                      "p" : "pf_dora",}
        
        kororo_personality = language_list.get(language_code, "p")
        if self.pipeline.lang_code != kororo_personality:
            self.pipeline = KPipeline(lang_code=kororo_personality)
        
        kororo_voice = voice if voice else voice_list.get(kororo_personality, "pf_dora")

        try: 
            generator = self.pipeline(text, voice= kororo_voice,speed=0.9,split_pattern=r'\n+')
            audio_pieces = []
            sample_rate = 24000

            for i, (gs, ps, audio) in enumerate(generator):
                logger.debug(f"Processando chunk {i}: {gs[:50]}...")
                audio_pieces.append(audio)
            

                silence = np.zeros(int(0.25 * sample_rate))
                audio_pieces.append(silence)
            
            if audio_pieces:
                final_audio = np.concatenate(audio_pieces)
            else:
                final_audio = np.zeros(int(0.5 * sample_rate))
            
       
            if torch.cuda.is_available(): 
                torch.cuda.empty_cache()
            
            return sample_rate, final_audio
            
        except Exception as e:
            logger.error(f"Erro ao sintetizar áudio: {e}", exc_info=True)
            raise e
    
    def desligar_audio(self):
   
        del self.pipeline
        self.pipeline = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
     
   

    
audio_manager = AudioManager()
                    
            

