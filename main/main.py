import cv2, boto3, threading, time, zmq, base64
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# --- 1. CONFIGURATION ---
PORT = "9090"
LOG_PORT = "9091"
REGION = "us-east-1"
CONFIDENCE_LEVEL = 80
AI_SCAN_INTERVAL = 0.5   
LOG_REFRESH_RATE = 1.5   

# --- 2. ZMQ INITIALIZATION (MUST BE BEFORE LOG FUNCTION) ---
context = zmq.Context()

# Video Broadcast Socket
footage_socket = context.socket(zmq.PUB)
footage_socket.bind(f'tcp://*:{PORT}')

# Raw Log Broadcast Socket
log_socket = context.socket(zmq.PUB)
log_socket.bind(f'tcp://*:{LOG_PORT}')

# --- 3. PROFESSIONAL LOGGER ---
def log(level, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tag = level.strip().center(10)
    log_string = f"{timestamp} ||{tag}|| {message}"
    
    # Print to VS Terminal
    print(log_string) 
    
    # Broadcast to Streamlit Dashboard Console
    try:
        log_socket.send_string(log_string)
    except Exception as e:
        print(f"ZMQ Error: {e}")

# --- 4. SYSTEM STARTUP ---
log("START", "Initializing PPE Monitoring System Engine...")

try:
    rekognition = boto3.client('rekognition', region_name=REGION)
    log("INFO", "AWS Rekognition Connectivity: VERIFIED")
except Exception:
    rekognition = None
    log("ERROR", "AWS Connectivity: FAILED (Check Tokens)")

executor = ThreadPoolExecutor(max_workers=2)

class GlobalState:
    def __init__(self):
        self.latest_res = None
        self.lock = threading.Lock()
        self.last_log_time = 0
        self.aws_active = True if rekognition else False

state = GlobalState()

# --- 5. AI BRAIN FUNCTION ---
def perform_ai_scan(image_bytes):
    if not rekognition:
        return
    try:
        response = rekognition.detect_protective_equipment(
            Image={'Bytes': image_bytes},
            SummarizationAttributes={
                'MinConfidence': CONFIDENCE_LEVEL, 
                'RequiredEquipmentTypes': ['HEAD_COVER', 'FACE_COVER', 'HAND_COVER']
            }
        )
        with state.lock:
            state.latest_res = response
            state.aws_active = True
    except Exception:
        with state.lock:
            state.aws_active = False

# --- 6. MAIN LOOP ---
cap = cv2.VideoCapture(0)
last_ai_scan = 0

log("READY", "System fully operational. Monitoring live feed...")

while True:
    ret, frame = cap.read()
    if not ret: break
    h, w, _ = frame.shape
    curr = time.time()

    # 1. Trigger AI Analysis
    if curr - last_ai_scan > AI_SCAN_INTERVAL:
        _, buf = cv2.imencode('.jpg', frame)
        executor.submit(perform_ai_scan, buf.tobytes())
        last_ai_scan = curr

    # 2. Process Results
    with state.lock:
        res = state.latest_res
        is_aws_up = state.aws_active
    
    # DEFAULT STATUS (Logic Gate)
    if not is_aws_up:
        current_status = "AWS BRAIN OFFLINE"
    elif res is None:
        current_status = "INITIALIZING AI..."
    else:
        current_status = "SAFE" # Default to safe only if AWS is up and we have a response

    # 3. Draw Boxes & Check Violations
    if is_aws_up and res:
        for p in res.get('Persons', []):
            box = p['BoundingBox']
            x1, y1 = int(box['Left']*w), int(box['Top']*h)
            x2, y2 = int((box['Left']+box['Width'])*w), int((box['Top']+box['Height'])*h)
            
            # Find PPE
            found = [eq['Type'] for bp in p.get('BodyParts', []) 
                     for eq in bp.get('EquipmentDetections', []) if eq['Confidence'] > CONFIDENCE_LEVEL]
            
            missing = []
            if 'HEAD_COVER' not in found: missing.append("Helmet")
            if 'FACE_COVER' not in found: missing.append("Mask")
            if 'HAND_COVER' not in found: missing.append("Gloves")

            color = (0, 0, 255) if missing else (0, 255, 0)
            label = "SAFE" if not missing else f"MISSING: {', '.join(missing)}"
            
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, label, (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
            
            if missing:
                current_status = f"VIOLATION: {', '.join(missing)}"

    # 4. Professional Logging (Mirroring VS Terminal to Dashboard)
    if curr - state.last_log_time > LOG_REFRESH_RATE:
        if not is_aws_up:
            log("ERROR", "AWS BRAIN OFFLINE || Check Internet/Credentials")
        elif "VIOLATION" in current_status:
            log("WARN", current_status)
        else:
            log("INFO", current_status)
        state.last_log_time = curr

    # 5. Broadcast Video
    _, zmq_buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    footage_socket.send(base64.b64encode(zmq_buf))

    # Local Window
    cv2.imshow("PPE System Backend", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'): break

cap.release()
cv2.destroyAllWindows()
log("STOP", "Engine shutdown. All processes terminated.")