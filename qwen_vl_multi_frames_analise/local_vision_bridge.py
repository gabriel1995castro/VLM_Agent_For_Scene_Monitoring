from __future__ import annotations

import asyncio
import base64
import logging
import time
from io import BytesIO

logger = logging.getLogger("uvicorn.error")


class GStreamerBridge:
    def __init__(
        self,
        ros_topic: str = "/camera/image_raw",
        target_fps: float = 2.0,
        mock: bool = False,
    ) -> None:
        self.ros_topic = ros_topic
        self.interval = 1.0 / max(target_fps, 0.1)
        self.mock = mock
        self._latest_frame_b64: str | None = None

    async def run(self, frame_queue: asyncio.Queue) -> None:
        if self.mock:
            logger.warning("GStreamerBridge: modo mock ativo — frames sintéticos")
            capture_task = asyncio.create_task(self._mock_capture_loop())
        else:
            loop = asyncio.get_event_loop()
            capture_task = loop.run_in_executor(None, self._ros_spin_thread)

        try:
            while True:
                start = time.monotonic()
                if self._latest_frame_b64 is not None:
                    if frame_queue.full():
                        try:
                            frame_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    try:
                        frame_queue.put_nowait(self._latest_frame_b64)
                    except asyncio.QueueFull:
                        pass
                elapsed = time.monotonic() - start
                await asyncio.sleep(max(0.0, self.interval - elapsed))
        except asyncio.CancelledError:
            capture_task.cancel()
            raise

    def _ros_spin_thread(self) -> None:
        try:
            import rclpy
            from sensor_msgs.msg import Image, CompressedImage
            import numpy as np

            rclpy.init()
            node = rclpy.create_node("mocktail_vision_bridge")

            def image_cb(msg: Image) -> None:
                try:
                    arr = np.frombuffer(msg.data, dtype=np.uint8)
                    # Usa sempre 3 canais para rgb8/bgr8 — evita erro de reshape
                    # quando msg.step tem padding
                    if msg.encoding in ('rgb8', 'bgr8'):
                        frame = arr.reshape((msg.height, msg.width, 3))
                    elif msg.encoding == 'mono8':
                        frame = arr.reshape((msg.height, msg.width))
                    else:
                        logger.warning("ROS encoding não suportado: %s", msg.encoding)
                        return
                    # Inverte canais apenas se BGR — rgb8 já está correto
                    bgr = msg.encoding == 'bgr8'
                    self._store_numpy_frame(frame, bgr=bgr)
                except Exception as exc:
                    logger.warning("ROS image_cb error: %s", exc)

            def compressed_cb(msg: CompressedImage) -> None:
                try:
                    # CompressedImage já é JPEG — envia direto como base64
                    self._latest_frame_b64 = base64.b64encode(bytes(msg.data)).decode()
                except Exception as exc:
                    logger.warning("ROS compressed_cb error: %s", exc)

            node.create_subscription(Image, self.ros_topic, image_cb, 10)
            node.create_subscription(
                CompressedImage,
                self.ros_topic + "/compressed",
                compressed_cb,
                10,
            )
            logger.info("GStreamerBridge: subscribed to ROS topic %s", self.ros_topic)
            rclpy.spin(node)

        except Exception as exc:
            logger.error(
                "GStreamerBridge: falha ao iniciar ROS2 (%s: %s). "
                "Use mock=True para desenvolvimento sem câmera.",
                type(exc).__name__,
                exc,
            )

    def _store_numpy_frame(self, frame: object, bgr: bool = False) -> None:
        """Converte frame numpy para JPEG base64. Inverte BGR→RGB se necessário."""
        from PIL import Image as PILImage
        import numpy as np
        arr = np.asarray(frame)
        if bgr and arr.ndim == 3 and arr.shape[2] == 3:
            arr = arr[:, :, ::-1]  # BGR → RGB
        img = PILImage.fromarray(arr.astype("uint8"))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        self._latest_frame_b64 = base64.b64encode(buf.getvalue()).decode()

    async def _mock_capture_loop(self) -> None:
        try:
            from PIL import Image as PILImage, ImageDraw
        except ImportError:
            logger.error("Pillow não instalado. pip install pillow")
            return

        while True:
            img = PILImage.new("RGB", (640, 480), color=(40, 40, 40))
            draw = ImageDraw.Draw(img)
            draw.rectangle([60, 160, 280, 320], fill=(210, 100, 30))   # laranja
            draw.rectangle([320, 160, 540, 320], fill=(30, 80, 210))   # azul
            draw.ellipse([260, 350, 380, 420], fill=(60, 160, 60))     # limão
            draw.text((10, 10), f"mock {time.time():.1f}", fill=(200, 200, 200))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            self._latest_frame_b64 = base64.b64encode(buf.getvalue()).decode()
            await asyncio.sleep(self.interval)