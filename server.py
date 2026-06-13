from flask import Flask, request, jsonify, send_file
import datetime
import requests
import os
import subprocess
import re
import logging
import ssl
import json
import base64
import threading
from base64 import b64encode, b64decode
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

app = Flask(__name__)
# 중요 로그가 잘 보이도록 기본 Werkzeug 접속 로그는 에러만 출력합니다.
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ================= [설정값] =================
SECRET_PASSWORD = "password"
ESP32_MAC = "11:22:33:44:55:66"  # ESP32 MAC 주소
TOKEN_FILE = "tokens.json"

CLIENT_ID = "xxxxxxxxxxxxxxxxxxxxxxxxx"
CLIENT_SECRET = "xxxxxxxxxxxxxxxxxxxxxxxxxxxx"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_FILE = os.path.join(BASE_DIR, "server_domain-chain.pem")  # 인증서 파일 매칭
KEY_FILE = os.path.join(BASE_DIR, "server_domain-key.pem")
PORT = 443 

ST_PUBLIC_KEY_BASE_URL = "https://key.smartthings.com"
st_public_key_cache = {}

device_info = {
    "last_seen": "None",
    "current_state": "UNKNOWN",
    "current_ip": None 
}

# ================= [토큰 제어 시스템] =================
def save_tokens(token_data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)

def load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None

def refresh_smartthings_token():
    tokens = load_tokens()
    if not tokens or "refresh_token" not in tokens:
        print("❌ [토큰 갱신 실패] 리프레시 토큰을 찾을 수 없습니다.")
        return None

    url = "https://api.smartthings.com/oauth/token"
    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    b64_auth_str = b64encode(auth_str.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {b64_auth_str}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}

    try:
        res = requests.post(url, headers=headers, data=data, timeout=5)
        res.raise_for_status()
        new_tokens = res.json()
        new_tokens["device_id"] = tokens.get("device_id")
        save_tokens(new_tokens)
        print("✨ [보안 갱신] 스마트싱스 Access Token 실시간 재발급 성공!")
        return new_tokens.get("access_token")
    except Exception as e:
        print(f"❌ [토큰 갱신 실패] 삼성 서버 통신 거절: {e}")
        return None

# ================= [로컬 기기 탐색 및 기존 보안 인증] =================
def find_ip_by_mac(target_mac):
    target_mac_colon = target_mac.lower().replace('-', ':') 
    target_mac_dash = target_mac.lower().replace(':', '-')  
    try:
        output = subprocess.check_output('arp -a', shell=True).decode('utf-8', errors='ignore')
        for line in output.split('\n'):
            line_lower = line.lower()
            if target_mac_colon in line_lower or target_mac_dash in line_lower:
                ip_match = re.search(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', line)
                if ip_match: return f"http://{ip_match.group(0)}"
    except Exception as e:
        print(f"ARP 조회 오류: {e}")
    return None

def verify_auth(req_time_str, received_password):
    try:
        req_time_obj = datetime.datetime.strptime(req_time_str, "%Y-%m-%d-%H:%M")
        now = datetime.datetime.now()
        if abs((now - req_time_obj).total_seconds()) > 120: return False, "요청 시간이 만료되었습니다."
        if received_password == SECRET_PASSWORD: return True, "Success"
        return False, "비밀번호가 일치하지 않습니다."
    except:
        return False, "잘못된 데이터 형식입니다."

# ================= [스마트싱스 서명 유효성 검증 패키지] =================
def get_smartthings_public_key(full_key_url):
    global st_public_key_cache
    if full_key_url in st_public_key_cache: return st_public_key_cache[full_key_url]
    try:
        resp = requests.get(full_key_url)
        resp.raise_for_status()
        public_key = resp.text
        st_public_key_cache[full_key_url] = public_key
        return public_key
    except:
        return None

def verify_request(req):
    auth_header = req.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Signature "): return False
    try:
        params = dict(item.strip().split("=", 1) for item in auth_header.split("Signature ")[1].split(","))
        key_id = params.get("keyId").strip('"')
        full_key_url = ST_PUBLIC_KEY_BASE_URL + key_id
        headers_to_sign = params.get("headers").strip('"').split(" ")
        signature = b64decode(params.get("signature").strip('"'))
        
        public_key_pem = get_smartthings_public_key(full_key_url)
        if not public_key_pem: return False
        
        cert_obj = x509.load_pem_x509_certificate(public_key_pem.encode())
        public_key = cert_obj.public_key()
        
        signing_string = ""
        for i, header in enumerate(headers_to_sign):
            if header == "(request-target)": signing_string += f"(request-target): {req.method.lower()} {req.path}"
            else: signing_string += f"{header}: {req.headers.get(header)}"
            if i < len(headers_to_sign) - 1: signing_string += "\n"
            
        public_key.verify(signature, signing_string.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return True
    except:
        return False

def subscribe_to_device_events(install_data, lifecycle):
    try:
        auth_token = install_data["authToken"]
        installed_app_id = install_data["installedApp"]["installedAppId"]
        device_id = install_data["installedApp"]["config"]["switchDevice"][0]["deviceConfig"]["deviceId"]
        
        token_data = {"access_token": auth_token, "refresh_token": install_data.get("refreshToken"), "device_id": device_id}
        save_tokens(token_data)
        
        api_url = f"https://api.smartthings.com/v1/installedapps/{installed_app_id}/subscriptions"
        headers = {"Authorization": f"Bearer {auth_token}"}
        payload = {"sourceType": "DEVICE", "device": { "deviceId": device_id, "componentId": "main", "capability": "switch", "attribute": "switch", "stateChangeOnly": True }}
        requests.post(api_url, json=payload, headers=headers)
        print(f"\n✅ [스마트앱 연동 성공] 가상 기기 ID 맵핑 완료: {device_id}")
    except Exception as e:
        print(f"❌ 구독 절차 실패: {e}")

# ================= [역방향 핵심: 서버 ➔ 스마트싱스 앱 상향 제어] =================
def sync_to_smartthings(status):
    tokens = load_tokens()
    if not tokens or "device_id" not in tokens: return
    
    url = f"https://api.smartthings.com/v1/devices/{tokens['device_id']}/commands"
    
    def send_command_payload(token_key):
        headers = {"Authorization": f"Bearer {token_key}", "Content-Type": "application/json"}
        payload = {"commands": [{"component": "main", "capability": "switch", "command": "on" if status.upper() == "ON" else "off"}]}
        return requests.post(url, json=payload, headers=headers, timeout=3)

    try:
        print(f"\n➔ [서버 ➔ 스마트싱스] 역방향 앱 스위치 상태 동기화 시도...")
        res = send_command_payload(tokens["access_token"])
        
        if res.status_code in [401, 400]:
            print("   ⚠️ 토큰 만료 감지! 실시간 토큰 리프레시 작동...")
            renewed_token = refresh_smartthings_token()
            if renewed_token:
                res = send_command_payload(renewed_token)
                
        print(f"◀ [스마트싱스 응답] 가상 상태 동기화 처리 결과 코드: {res.status_code}")
    except Exception as e:
        print(f"❌ [역방향 동기화 최종 실패]: {e}")

# ================= [포트 5000 전용: 로컬 웹서버 라우트 라인] =================
@app.route('/')
def index():
    print(f"\n[접속 로그] 사용자가 5000번 웹 제어 페이지(index.html)에 로컬 접근함.")
    return send_file(os.path.join(os.path.dirname(__file__), 'index.html'))

@app.route('/api/get_status', methods=['POST'])
def get_secure_status():
    data = request.get_json(silent=True) or {}
    is_valid, msg = verify_auth(data.get('request_time'), data.get('password'))
    if is_valid:
        return jsonify({"status": "success", "current_state": device_info["current_state"], "esp32_ip": device_info["current_ip"]}), 200
    return jsonify({"status": "error", "message": msg}), 403

@app.route('/api/control', methods=['POST'])
def control_switch():
    """웹 브라우저의 조작 버튼 클릭 시 트리거"""
    data = request.get_json(silent=True) or {}
    is_valid, msg = verify_auth(data.get('request_time'), data.get('password'))
    if is_valid:
        target_ip = find_ip_by_mac(ESP32_MAC) or device_info.get("current_ip")
        if not target_ip: return jsonify({"status": "error", "message": "ESP32 탐지 불가"}), 404
        
        command = data.get('command').upper()
        print(f"\n[웹 브라우저 ➔ 서버] 사용자가 버튼을 직접 클릭함: {command}")
        
        esp_payload = {"request_time": datetime.datetime.now().strftime("%Y-%m-%d-%H:%M"), "command": command, "device_id": data.get('device_id', 'unknown'), "sender": "home_server"}
        try:
            print(f"➔ [서버 ➔ ESP32] 로컬 네트워크 전송 시작 -> {target_ip}/api/command")
            resp = requests.post(f"{target_ip}/api/command", json=esp_payload, timeout=3)
            print(f"◀ [ESP32 ➔ 서버] 전송 결과 코드 응답: {resp.status_code}")
            if resp.status_code == 200: 
                return jsonify({"status": "success", "message": f"{command} 전송 완료 (IP: {target_ip})"}), 200
        except Exception as e:
            return jsonify({"status": "error", "message": f"ESP32 연결 실패 ({target_ip})"}), 500
    return jsonify({"status": "error", "message": msg}), 403

@app.route('/api/change', methods=['POST'])
def handle_change():
    """ESP32 기기의 로컬 물리 상태 변경 감지 엔드포인트"""
    data = request.get_json(silent=True)
    if not data: return "Bad Request", 400
    
    status = data.get('status', 'UNKNOWN').upper()
    
    # 🛡️ 중복 루프 방지 장치
    if status == device_info["current_state"]:
        return "OK", 200
        
    device_info["current_state"] = status
    device_info["last_seen"] = data.get('request_time', 'Unknown Time')
    device_info["current_ip"] = f"http://{request.remote_addr}" 
    
    print(f"\n[ESP32 ➔ 서버 물리 상태 보고] 기기 버튼이 수동으로 제어됨: {status} (IP: {device_info['current_ip']})")
    
    if status in ["ON", "OFF"]:
        sync_to_smartthings(status)
        
    return "OK", 200

@app.route('/api/state', methods=['POST'])
def handle_state():
    data = request.get_json(silent=True)
    if not data: return "Bad Request", 400
    device_info["last_seen"] = data.get('request_time', 'Unknown Time')
    device_info["current_state"] = data.get('status', 'UNKNOWN')
    device_info["current_ip"] = f"http://{request.remote_addr}" 
    return "ALIVE", 200

# ================= [포트 443 전용: 스마트싱스 클라우드 웹훅 라인] =================
@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.json
    if not data or not data.get("lifecycle"): return "Bad Request", 400
    
    lifecycle = data["lifecycle"]
    if lifecycle == "CONFIRMATION": return "OK", 200
    if not verify_request(request): return "Unauthorized", 401

    if lifecycle in ["INSTALL", "UPDATE"]:
        lifecycle_data = data.get("installData", {}) or data.get("updateData", {})
        if lifecycle_data: subscribe_to_device_events(lifecycle_data, lifecycle)
        return jsonify({"installData": {}}) if lifecycle == "INSTALL" else jsonify({"updateData": {}})
	
    # 🌟 호스트님이 확인해 주신 최적화 '한 줄 평탄화' 구조 적용 완료
    elif lifecycle == "CONFIGURATION":
        phase = data.get("configurationData", {}).get("phase")
        if phase == "INITIALIZE":
            return jsonify({"configurationData": {"initialize": {"name": "ESP32 스마트 전등 웹훅 앱", "description": "ESP32 제어용 스마트싱스 브릿지", "id": "app", "permissions": ["r:devices:*", "x:devices:*"], "firstPageId": "Page1"}}})
        elif phase == "PAGE":
            return jsonify({"configurationData": {"page": {"pageId": "Page1", "previousPageId": None, "nextPageId": None, "name": "연동할 기기를 선택해주세요", "complete": True, "sections": [{"name": "전등 스위치 매핑", "settings": [{"id": "switchDevice", "name": "스위치를 선택해 주세요.", "description": "Tap to set", "type": "DEVICE", "required": True, "multiple": False, "capabilities": ["switch"], "permissions": ["r", "x"]}]}]}}})
	
    elif lifecycle == "EVENT":
        for event in data.get("eventData", {}).get("events", []):
            if event.get("eventType") == "DEVICE_EVENT":
                value = event["deviceEvent"].get("value").upper()
                
                print(f"\n[스마트싱스 앱 ➔ 서버 원격 명령 수신] 아이콘 터치 작동: {value}")
                
                if value == device_info["current_state"]:
                    continue
                
                device_info["current_state"] = value
                target_ip = find_ip_by_mac(ESP32_MAC) or device_info.get("current_ip")
                if target_ip:
                    esp_payload = {"request_time": datetime.datetime.now().strftime("%Y-%m-%d-%H:%M"), "command": value, "device_id": "smartthings_cloud", "sender": "smartthings"}
                    try:
                        print(f"➔ [서버 ➔ ESP32 로컬 제어 릴레이] 원격 신호 전송 -> {target_ip}/api/command")
                        requests.post(f"{target_ip}/api/command", json=esp_payload, timeout=2)
                    except Exception as e:
                        print(f"❌ ESP32 통신 지연 에러: {e}")
                        
        return jsonify({"eventData": {}})

    return jsonify({})

# ================= [멀티포트 스레딩 동시 실행 엔진] =================
if __name__ == '__main__':
    print("멀티 포트 하이브리드 홈 서버 구동 개시")
    
    # 1. 포트 5000 로컬 HTTP 웹서버 (데몬 백그라운드 스레드로 가동)
    print("🔓 [스레드 1 개방] 로컬 제어용 HTTP 웹서버 대기 중 -> Port: 5000")
    http_server_thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    )
    http_server_thread.daemon = True
    http_server_thread.start()

    # 2. 포트 443 스마트싱스 전용 HTTPS 보안 서버 (메인 프로세스로 가동)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT_FILE, KEY_FILE)
    print("🔒 [스레드 2 개방] 스마트싱스 연동 HTTPS 보안 서버 대기 중 -> Port: 443")
    
    # 관리자 권한 CMD에서 작동해야 443 포트를 정상적으로 바인딩합니다.
    app.run(host='0.0.0.0', port=PORT, ssl_context=context, debug=False, use_reloader=False)