import asyncio
import json
import threading
import time
import logging
import unicodedata
import httpx
import numpy as np
import websockets
import cv2

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hud")

BACKEND_HTTP = "http://localhost:8000"
BACKEND_WS   = "ws://localhost:8000"
CAMERA_TOPIC = "/camera/image_raw"

DUMMY_SDP = "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"

WINDOW_MAIN = "HUD — Camera"
WINDOW_TREE = "HUD — Behavior Tree"

C_BG        = (15,  15,  20)       
C_BAR       = (20,  20,  28)       
C_BORDER    = (60,  60,  80)      
C_WHITE     = (240, 240, 250)
C_GRAY      = (160, 160, 175)
C_DONE      = (80,  220, 120)      
C_ACTIVE    = (0,   210, 255)      
C_PENDING   = (130, 130, 150)      
C_SPEECH_BG = (10,  10,  18)
C_SPEECH_FG = (0,   230, 110)
C_PHASE_FG  = (200, 200, 215)


TREE_COLORS = {
    "agent":    (60,  200, 60),
    "sequence": (50,  190, 240),
    "observer": (80,  100, 240),
    "fallback": (60,  160, 255),
    "condition": (255, 180, 60),
    "default":  (160, 160, 180),
}

NODE_ICONS = {
    "sequence": "SEQ",
    "fallback": "FB",
    "condition": "IF",
    "agent": "ACT",
    "observer": "OBS"
}

def normalize_node_type(raw_type):
    if raw_type is None:
        return "default"

    if hasattr(raw_type, "value"):
        return str(raw_type.value).lower().strip()

   
    if isinstance(raw_type, dict):

        if "value" in raw_type:
            return str(raw_type["value"]).lower().strip()

        if "_value_" in raw_type:
            return str(raw_type["_value_"]).lower().strip()

    if isinstance(raw_type, str):
        return raw_type.lower().strip()
    
    return str(raw_type).lower().strip()

def put_text(img, text, pos, scale=0.6, color=C_WHITE, thickness=1,font=cv2.FONT_HERSHEY_DUPLEX, line_type=cv2.LINE_AA):
    
    text_str = str(text)
    
    text_limpo = unicodedata.normalize('NFKD', text_str).encode('ASCII', 'ignore').decode('ASCII')
    cv2.putText(img, text_limpo, pos, font, scale, color, thickness, line_type)

def draw_rect_alpha(img, pt1, pt2, color, alpha=0.55):
    """Retângulo semi-transparente."""
    overlay = img.copy()
    cv2.rectangle(overlay, pt1, pt2, color, -1)
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)


def draw_panel(img, pt1, pt2, bg_color=C_BG, border_color=C_BORDER,
               alpha=0.75, radius=0):
    """Painel com fundo semi-transparente e borda."""
    draw_rect_alpha(img, pt1, pt2, bg_color, alpha)
    cv2.rectangle(img, pt1, pt2, border_color, 1, cv2.LINE_AA)



class HudDisplayNode(Node):
    def __init__(self):
        super().__init__("hud_display_node")

        self._lock = threading.Lock()

        self._current_frame = None
        self._speech_text   = "Iniciando..."
        self._phase         = ""
        self._tasks         = []
        self._active_task   = ""
        self._tree          = None

        self.create_subscription(Image, CAMERA_TOPIC, self._image_cb, 10)

        cv2.namedWindow(WINDOW_MAIN, cv2.WINDOW_NORMAL)
        cv2.namedWindow(WINDOW_TREE, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_MAIN, 960, 540)
        cv2.resizeWindow(WINDOW_TREE, 760, 940)

        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()

        self.create_timer(1.0 / 30.0, self._update_gui)
        self.get_logger().info("HUD iniciado.")


    def _image_cb(self, msg: Image):
        try:
            arr = np.frombuffer(msg.data, dtype=np.uint8)
            if msg.encoding in ("rgb8", "bgr8"):
                frame = arr.reshape((msg.height, msg.width, 3))
                if msg.encoding == "rgb8":
                    frame = frame[:, :, ::-1].copy()
            elif msg.encoding == "mono8":
                frame = arr.reshape((msg.height, msg.width))
            else:
                return
            with self._lock:
                self._current_frame = frame
        except Exception as e:
            self.get_logger().warning(f"image_cb error: {e}")


    def _update_gui(self):
        with self._lock:
            frame       = self._current_frame
            speech      = self._speech_text
            phase       = self._phase
            tasks       = list(self._tasks)
            active      = self._active_task
            tree        = self._tree

        if frame is None:
            display = np.full((480, 640, 3), C_BG, dtype=np.uint8)
            put_text(display, "Aguardando camera...", (80, 240),
                     scale=1.0, color=(0, 130, 255), thickness=2)
        else:
            display = frame.copy()

        h, w = display.shape[:2]

        if phase:
            draw_rect_alpha(display, (0, 0), (w, 44), C_BAR, alpha=0.80)
            cv2.rectangle(display, (0, 43), (w, 44), C_BORDER, -1)

            cv2.rectangle(display, (0, 0), (4, 44), C_ACTIVE, -1)
            put_text(display, phase.upper(), (14, 30),
                     scale=0.75, color=C_PHASE_FG, thickness=1,
                     font=cv2.FONT_HERSHEY_DUPLEX)

        if tasks:
            ROW_H   = 30
            PANEL_W = 340
            PAD_X   = 12
            PAD_Y   = 8
            title_h = 28

            x0 = w - PANEL_W - 10
            y0 = 54
            panel_h = title_h + PAD_Y + len(tasks) * ROW_H + PAD_Y

            draw_panel(display,
                       (x0, y0), (x0 + PANEL_W, y0 + panel_h),
                       alpha=0.82)

            put_text(display, "TAREFAS", (x0 + PAD_X, y0 + 19),
                     scale=0.52, color=C_GRAY, thickness=1,
                     font=cv2.FONT_HERSHEY_DUPLEX)
            cv2.line(display,
                     (x0 + 1, y0 + title_h),
                     (x0 + PANEL_W - 1, y0 + title_h),
                     C_BORDER, 1, cv2.LINE_AA)

            for i, t in enumerate(tasks):
                done      = t.get("completed", False)
                is_active = t.get("id") == active
                label     = t.get("text", "")[:38]

                ry = y0 + title_h + PAD_Y + i * ROW_H

                if is_active and not done:
                    draw_rect_alpha(display,
                                    (x0 + 1, ry - 2),
                                    (x0 + PANEL_W - 1, ry + ROW_H - 4),
                                    (0, 60, 80), alpha=0.55)

                bar_color = C_DONE if done else C_ACTIVE if is_active else C_PENDING
                cv2.rectangle(display,
                               (x0 + 1, ry - 1),
                               (x0 + 4, ry + ROW_H - 5),
                               bar_color, -1)

                icon  = "[X]" if done else "->" if is_active else "[ ]"
                color = C_DONE if done else C_ACTIVE if is_active else C_PENDING

                put_text(display, icon, (x0 + PAD_X, ry + 16),
                         scale=0.50, color=color, thickness=1)
                put_text(display, label, (x0 + PAD_X + 18, ry + 16),
                         scale=0.52, color=color, thickness=1)

        if speech:
            FOOT_H = 72

        texto_tarefa_ativa = ""
        for t in tasks:
            if t.get("id") == active:
                texto_tarefa_ativa = t.get("text", "")
                break

        if texto_tarefa_ativa:
            exibicao_rodape = f"Executando: {texto_tarefa_ativa}"
            rotulo_rodape = "TAREFA ATUAL"
        else:
            exibicao_rodape = speech
            rotulo_rodape = "FALA"

        
        if exibicao_rodape:
            FOOT_H = 72
            draw_rect_alpha(display,
                            (0, h - FOOT_H), (w, h),
                            C_SPEECH_BG, alpha=0.80)
            cv2.line(display,
                     (0, h - FOOT_H), (w, h - FOOT_H),
                     C_BORDER, 1, cv2.LINE_AA)

            
            put_text(display, rotulo_rodape, (12, h - FOOT_H + 16),
                     scale=0.40, color=C_GRAY, thickness=1)

            max_chars = 90
            line1 = exibicao_rodape[:max_chars]
            line2 = exibicao_rodape[max_chars:max_chars * 2] if len(exibicao_rodape) > max_chars else ""

            put_text(display, line1, (12, h - FOOT_H + 36),
                     scale=0.68, color=C_SPEECH_FG, thickness=1)
            if line2:
                put_text(display, line2, (12, h - FOOT_H + 60),
                         scale=0.62, color=C_SPEECH_FG, thickness=1)
        cv2.imshow(WINDOW_MAIN, display)

        tree_canvas = np.full((940, 1100, 3), (12, 12, 18), dtype=np.uint8)


        for gx in range(0, 1100, 60):
            cv2.line(tree_canvas, (gx, 0), (gx, 940), (22, 22, 30), 1)
        for gy in range(0, 940, 60):
            cv2.line(tree_canvas, (0, gy), (1100, gy), (22, 22, 30), 1)

        if tree:
            self._draw_tree(tree, 550, 55, 900, tree_canvas, tasks, active)
        else:
            put_text(tree_canvas, "Aguardando árvore...", (320, 470), scale=0.9, color=(60, 60, 80), 
                     thickness=1)

        cv2.imshow(WINDOW_TREE, tree_canvas)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            rclpy.shutdown()


    def _draw_tree(self, node, x, y, dx, canvas, tasks=None, active_task_id=None):
            if not node:
                return

            if tasks is None:
                tasks = []

            raw_type = node.get("type")

            node_type = normalize_node_type(raw_type)

            content = node.get("content")

            if not content or str(content).strip() == "":
                content = node_type
            task_ref  = node.get("task", "")          
            children  = node.get("children", [])

            color = TREE_COLORS.get(node_type, TREE_COLORS["default"])


            if node_type == "agent":
                color = C_PENDING 
            
                for t in tasks:
                    if t.get("text") == content:
                        if t.get("completed"):
                            color = C_DONE    
                        elif t.get("id") == active_task_id:
                            color = C_ACTIVE  
                        break

            if children:
                V_STEP = 120
                n = len(children)
                
                min_spacing = 220 
                step = max(dx // max(n, 1), min_spacing)
                
                total_width = step * n
                cx = x - (total_width // 2) + (step // 2)

                for child in children:
                    child_x = cx
                    child_y = y + V_STEP

                    cv2.line(canvas, (x, y), (child_x, child_y), (80, 80, 100), 1, cv2.LINE_AA)

                    self._draw_tree(child, child_x, child_y, step, canvas, tasks, active_task_id)
                    cx += step

            RADIUS = 20
            
            cv2.circle(canvas, (x, y), RADIUS + 5, (*color[:2], max(color[2] - 80, 0)), 1, cv2.LINE_AA)
            cv2.circle(canvas, (x, y), RADIUS, color, -1, cv2.LINE_AA)

            label_main = content[:22] + "..." if len(content) > 22 else content
            if not label_main:
                label_main = node_type
            icon = NODE_ICONS.get(node_type, "?")
            label_main = f"{icon} {label_main}"   
            lx = x + RADIUS + 8
            ly = y - 6

            (tw, th), _ = cv2.getTextSize(label_main, cv2.FONT_HERSHEY_DUPLEX, 0.48, 1)
            cv2.rectangle(canvas, (lx - 2, ly - th - 2), (lx + tw + 2, ly + 4), (12, 12, 18), -1)

            put_text(canvas, label_main, (lx + 1, ly + 1), scale=0.48, color=(0, 0, 0), thickness=2)
            put_text(canvas, label_main, (lx, ly), scale=0.48, color=C_WHITE, thickness=1)

            type_label = node_type.upper()[:10]
            (tw2, th2), _ = cv2.getTextSize(type_label, cv2.FONT_HERSHEY_DUPLEX, 0.35, 1)
            bx1 = lx - 2
            by1 = ly + 4
            bx2 = bx1 + tw2 + 8
            by2 = by1 + th2 + 5

            cv2.rectangle(canvas, (bx1, by1), (bx2, by2), color, -1, cv2.LINE_AA)
            put_text(canvas, type_label, (bx1 + 4, by2 - 3), scale=0.35, color=(10, 10, 10), thickness=1)


    def _ws_loop(self):
        while rclpy.ok():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._ws_session())
            except Exception as e:
                logger.warning(f"WS reconnect: {e}")
                time.sleep(3)

    async def _ws_session(self):
        try:
            async with websockets.connect(
                f"{BACKEND_WS}/session/control",
                ping_interval=20,
                ping_timeout=60,
            ) as ws:
                msg        = json.loads(await ws.recv())
                session_id = msg["session_id"]
                print("\n" + "=" * 50)
                print("HUD CONECTADO! AGUARDANDO COMANDOS...")
                print(f"USE ESTE SESSION_ID NO TRIGGER: {session_id}")
                print("=" * 50 + "\n")

                await ws.send(json.dumps({"type": "session.start"}))

                async for raw in ws:
                    event = json.loads(raw)
                    etype = event.get("type")

                    if etype == "hud.state":
                        with self._lock:
                            self._phase       = event.get("phase", "")
                            self._tasks       = event.get("tasks", [])
                            self._active_task = event.get("active_task_id", "")

                    elif etype == "hud.speech":
                        with self._lock:
                            self._speech_text = event.get("text", "")

                    elif etype == "hud.tree":
                        with self._lock:
                            self._tree = event.get("tree", None)

        except Exception as e:
            logger.error(
                f"HUD WebSocket CRASHOU: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise



def main():
    rclpy.init()
    node = HudDisplayNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()