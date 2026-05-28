import base64
import cv2
import numpy as np
from dataclasses import dataclass

@dataclass
class ReportMonitoring:
    """
    Padroniza o retono da funcao de monitoramento para exibir informacoes basicas necessarias.
    """
    motion_detected: bool
    mean_score: float
    max_score: float
    noisy_frames: int
    valid_pairs: int

class MovimentDetector:
    def __init__(self, threshold: float = 12.0, blur_threshold: float = 80.0, min_valid_pairs: int = 3):
        
        self.threshold = threshold
        self.blur_threshold = blur_threshold
        self.min_valid_pairs = min_valid_pairs


    def analyze_sequence(self, frames_b64: list[str]) -> ReportMonitoring:
        """
        Recebe as multiplos frames e analisa a partir dos pixels se existe alguma alteracao entre os quadros.       
        """

        if len (frames_b64)<2:
            return ReportMonitoring(False,0.0,0.0,0,0)
        
        gray_frames = [self._decode_gray(f) for f in frames_b64]
        scores :list[float] = []
        noisy_frames = 0

        for i in range (len(gray_frames)-1):

            f1, f2 = gray_frames[i], gray_frames[i + 1]
            
            if f1 is None or f2 is None:
                noisy_frames += 1
                continue

            if self.detected_blurry(f1) or self.detected_blurry(f2):
                noisy_frames += 1
                continue

            diff = cv2.absdiff(f1,f2)
            scores.append(float(diff.mean()))


        if len(scores)  < self.min_valid_pairs:

           return ReportMonitoring(motion_detected=False, mean_score=0.0, max_score=0.0, 
                                   noisy_frames=noisy_frames, valid_pairs=len(scores))
        
        mean_score = float(np.mean(scores))
        max_score = float(np.max(scores))
        motion_detected = mean_score > self.threshold

        return ReportMonitoring (motion_detected=motion_detected,mean_score=mean_score,
            max_score=max_score,noisy_frames=noisy_frames,valid_pairs=len(scores),)


    def _decode_gray(self, frame_b64: str) -> np.ndarray | None:
        """
        Converte  o frame em analise para uma  escala em tons de cinza.        
        """
        try:
            img_bytes = base64.b64decode(frame_b64)
            arr = np.frombuffer(img_bytes, np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        except Exception:
            return None
        
    def detected_blurry(self, gray: np.ndarray) -> bool:
        """
        Sinaliza que o frame esta muito borrado para ser analisado pelo modelo de VLM.
        """

        variance = cv2.Laplacian(gray, cv2.CV_64F).var()
        return variance < self.blur_threshold

 

