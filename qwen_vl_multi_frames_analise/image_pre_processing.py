import base64
import cv2
import numpy as np

class MovimentDetector:
    def __init__(self, threshold=12.0):
        
        self.threshold = threshold

    def detection_moviment (self,frame1_b64: str,frame2_b64: str,) -> tuple[bool, float]:

        img1_bytes = base64.b64decode(frame1_b64)
        img2_bytes = base64.b64decode(frame2_b64)

        arr1 = np.frombuffer(img1_bytes, np.uint8)
        arr2 = np.frombuffer(img2_bytes, np.uint8)

        frame1 = cv2.imdecode(arr1,cv2.IMREAD_GRAYSCALE)
        frame2 = cv2.imdecode(arr2,cv2.IMREAD_GRAYSCALE)

        if frame1 is None or frame2 is None:
            return False, 0.0
        
        diff = cv2.absdiff(frame1, frame2)
        motion_score = float(diff.mean())

        motion_detected = (motion_score > self.threshold)

        return motion_detected, motion_score


